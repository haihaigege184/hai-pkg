"""域名健康检查：并发探测 + 打分 + 自动选主

判定要点
  * 跟随重定向；重定向到新域名 -> 记录为候选域名（自动发现漂移后的新地址）
  * 空页 / HTTP 错误 / 停放页（DNSPod「域名已过期」）/ 密码跳转页 / CF 挑战页
    统一判为 dead。这些页面往往有 8~30KB，只按体积阈值会被骗过去。
  * 用「站点特征标记」(/vod /type/ /search api.php ...) 确认是真实站点；
    带特征且体积够大 -> q3，带特征 -> q2，仅存活无特征 -> q1
  * 记录延迟，用于选主时挑最快的
"""
import concurrent.futures as cf
import time

import requests
import urllib3

from . import config as C
from . import sources as S
from . import store

urllib3.disable_warnings()

_session = None


def _sess():
    global _session
    if _session is None:
        _session = requests.Session()
        _session.headers.update({"User-Agent": C.UA, "Accept-Language": "zh-CN,zh;q=0.9"})
    return _session


def _dns_ok(host):
    import socket
    try:
        socket.getaddrinfo(host, None)
        return True
    except Exception:
        return False


def _text_of(body, limit=300000):
    """把响应体尽力解成文本（只看前 300KB，足够判断页面性质）"""
    raw = (body or b"")[:limit]
    try:
        return raw.decode("utf-8", "ignore")
    except Exception:
        return raw.decode("gbk", "ignore")


def _kind_of(api="", name="", tag=""):
    """判断这个接口是不是「网盘聚合类」—— 它们的首页是 SPA，没有 MacCMS 特征。

    见 config.NETDISK_API_HINTS 的注释：对这类站点，判活不能靠首页 HTML 长得像
    不像影视站，否则会把活着的接口误判成失效（用户实测踩过这个坑）。
    """
    blob = ("%s %s %s" % (api or "", name or "", tag or "")).lower()
    for h in getattr(C, "NETDISK_API_HINTS", ()):
        if h and h.lower() in blob:
            return "netdisk"
    return "normal"


def _looks_like_app(low):
    """像不像一个正常的「应用型首页」（SPA / 自研前端），而不是空壳站。

    判据：必须有 HTML 骨架，且有下列任一「前端应用」痕迹 ——
    脚本标签、静态资源目录、常见框架挂载点。真·停放页通常只有静态 HTML 与广告
    脚本，但会先被 BAD_PAGE_HINTS 拦掉，所以这里放宽是安全的。
    """
    if "<html" not in low and "<!doctype" not in low:
        return False
    if "<script" in low:
        return True
    for m in ("/static/", "assets/", "_nuxt", "__next", 'id="app"', "id='app'",
              'id="root"', "id='root'", "/js/", "vue", "react", "webpack"):
        if m in low:
            return True
    return "<div" in low and "app" in low


def _classify(body, nbytes, kind=""):
    """按内容给质量分，返回 (quality, note)

    3 = 真实站点且页面完整   2 = 真实站点   1 = 存活但存疑  0 = 不可用

    判定顺序（顺序本身很重要）：
      1. 有 MacCMS 特征  -> 真站点（最高优先，避免被反例误杀）
      2. 命中停放页/密码页 -> 不可用
      3. 命中人机校验/WAF  -> **存活但存疑**（不能判死，见 CHALLENGE_HINTS）
      4. 网盘聚合类：像前端应用就按真站点/存活算（它们的首页本就没有 /vod）
      5. 太小 -> 停放页；否则算门户页
    """
    low = _text_of(body).lower()
    has_marker = any(m in low for m in C.SITE_MARKERS)

    if has_marker:
        if nbytes >= C.SITE_FULL_BYTES:
            return 3, ""
        return 2, ""

    bad = next((b for b in C.BAD_PAGE_HINTS if b in low), None)
    if bad:
        return 0, f"非站点页(命中「{bad}」)"

    chal = next((b for b in getattr(C, "CHALLENGE_HINTS", ()) if b in low), None)
    if chal:
        # 站点多半活着，只是不让我们这种数据中心 IP 过。判死会误伤真实用户。
        return 1, f"人机校验页(命中「{chal}」，客户端通常可正常)"

    if kind == "netdisk" and _looks_like_app(low):
        if nbytes >= getattr(C, "SITE_APP_MIN_BYTES", 1500):
            # 网盘聚合站的首页本来就没有 /vod 之类字样，只要是个像样的前端页面
            # 就按「真站点」算 —— 可用性取决于后端 JSON 接口，不是首页结构。
            return 2, "网盘聚合站首页(无 /vod 特征，按可用计)"
        if nbytes >= getattr(C, "SITE_APP_SHELL_MIN_BYTES", 500):
            return 1, "网盘站前端空壳(按存活计，判活靠后端接口)"

    if nbytes < C.HEALTH_MIN_BYTES or nbytes < C.SITE_MIN_BYTES:
        return 0, "响应体过小(疑似停放页)"
    return 1, "无站点特征(疑似门户页)"


def _quality(status, nbytes):
    """兼容旧调用：仅按状态给粗分"""
    if status != "alive":
        return 0
    return 2 if nbytes >= C.REAL_MIN_BYTES else 1


def _one_shot(url, timeout, kind, ua=None):
    """探测一次，返回 (status, latency, code, bytes, final, note, quality)

    ua 非空时**只对这一条请求**覆盖 User-Agent（会话默认仍是 C.UA）。
    见 config.PROBE_UA_FALLBACKS：有些站的 WAF 按 UA 重置连接，
    换一个 UA 就能拿到真站页面。
    """
    t0 = time.time()
    headers = {"User-Agent": ua} if ua else None
    r = _sess().get(url, timeout=timeout, verify=False, allow_redirects=True,
                    headers=headers)
    dt = int((time.time() - t0) * 1000)
    body = r.content or b""
    n = len(body)
    final = r.url

    if r.status_code >= 400:
        low = _text_of(body).lower()
        chal = next((b for b in getattr(C, "CHALLENGE_HINTS", ()) if b in low), None)
        if chal:
            # CF/WAF 拦截 —— 数据中心 IP 才会被拦，客户端通常能过。
            # 判死会误伤真实用户，所以算「存活但存疑」(q1)。
            return ("alive", dt, r.status_code, n, final,
                    f"人机校验(HTTP {r.status_code}，数据中心IP被拦，客户端通常可正常)", 1)
        return "dead", dt, r.status_code, n, final, f"HTTP {r.status_code}", 0

    q, note = _classify(body, n, kind)
    if q == 0:
        return "dead", dt, r.status_code, n, final, note, 0
    return "alive", dt, r.status_code, n, final, note, q


def probe(url, timeout=None, kind="", attempts=None):
    """探测单个地址，返回 (status, latency_ms, http_code, bytes, final_url, note, quality)

    kind: "netdisk" 表示这是网盘聚合类接口，判定标准放宽（见 _kind_of）。

    【为什么要重试】实测「蜡笔」fmcf.fun 一次 ConnectionError 就被判死并标注
    失效，随后手工访问直接 200 + 623KB。瞬时失败（网络抖动/DNS 抖动/对端限流）
    不能等价于永久失效 —— 这是用户「标注失效的接口实测可以使用」的主要来源。
    所以对**异常类失败**重试 PROBE_ATTEMPTS 次；HTTP 层明确的 4xx/5xx 不重试
    （那是站点的确定回复）。
    """
    timeout = timeout or C.HEALTH_TIMEOUT
    attempts = max(1, int(attempts or getattr(C, "PROBE_ATTEMPTS", 3)))
    wait = float(getattr(C, "PROBE_RETRY_WAIT", 1.0))
    host = S.norm_host(url)
    uas = [None] + [u for u in (getattr(C, "PROBE_UA_FALLBACKS", ()) or ()) if u]

    last = None
    dns_dead = False
    for ua in uas:
        for i in range(attempts):
            try:
                out = _one_shot(url, timeout, kind, ua=ua)
                if ua:
                    st, dt, code, n, final, note, q = out
                    mark = f"换UA探通({ua[:22]})"
                    out = (st, dt, code, n, final,
                           (note + " · " + mark) if note else mark, q)
                return out
            except Exception as e:
                last = e
                # DNS 都解析不出来就别反复试了（域名确实废弃）
                if not _dns_ok(host):
                    dns_dead = True
                    break
                if i < attempts - 1:
                    time.sleep(wait * (i + 1))
        if dns_dead:
            break

    dt = 0
    e = last
    tries = attempts if dns_dead else attempts * len(uas)
    if dns_dead:
        note = "DNS解析失败(域名已废弃)"
    elif type(e).__name__ in ("ReadTimeout", "ConnectTimeout"):
        note = f"连接超时(站点无响应，已重试 {tries} 次/{len(uas)} 种UA)"
    elif type(e).__name__ == "SSLError":
        note = "SSL证书异常(播放器可能拒绝)"
    else:
        note = f"{type(e).__name__}(已重试 {tries} 次/{len(uas)} 种UA)"
    return "dead", dt, 0, 0, url, note, 0


def check_all(pool, only_dead=False, tags=None):
    """并发检查域名池。就地更新 pool 中每条记录的 status/latency/checked。
    返回 (discovered, results) —— discovered 是 {tag: [新域名]}
    """
    tasks = []
    for tag, ent in pool.items():
        if tags and tag not in tags:
            continue
        # 站点类型决定判定标准：网盘聚合站的首页没有 MacCMS 特征，
        # 用影视站那套特征去判会把活着的接口误判成失效。
        kind = _kind_of(ent.get("api"), ent.get("name"), tag)
        for d in ent.get("domains", []):
            # 用户已判弃用的域名：不探测（省超时）、固定标 dead、不计入任何可用数。
            # 万一将来被人抢注复活探到 q3，下面 pick_best_detail 也会把它过滤掉，
            # 不会写回下发配置 —— 双重保险。
            if S.is_retired(tag, d.get("url") or ""):
                d["retired"] = True
                d["status"] = "dead"
                d["quality"] = 0
                d["latency"] = 0
                d["http"] = 0
                d["bytes"] = 0
                d["note"] = "已弃用（用户确认到期停用，不再巡检）"
                d["checked"] = int(time.time())
                continue
            if only_dead and d.get("status") == "alive":
                continue
            tasks.append((tag, d, kind))
    results = []
    discovered = {}

    def work(item):
        tag, d, kind = item
        st, dt, code, n, final, note, q = probe(d["url"], kind=kind)
        if st == "alive" and final and not S.same_site(final, d["url"]):
            discovered.setdefault(tag, []).append(S.norm_base(final))
        return tag, d, st, dt, code, n, final, note, q

    with cf.ThreadPoolExecutor(max_workers=C.HEALTH_WORKERS) as ex:
        for tag, d, st, dt, code, n, final, note, q in ex.map(work, tasks):
            d["status"] = st
            d["latency"] = dt
            d["http"] = code
            d["bytes"] = n
            d["quality"] = q
            d["checked"] = int(time.time())
            d["final"] = final
            d["note"] = note
            # 记「最近一次探到完整真站」的时间 —— 供 survivable_count 的宽限期用，
            # 让成片的间歇性失败不至于把刚验证过的活站判死（见 ALIVE_GRACE_SEC）。
            if st == "alive" and int(q or 0) >= 2:
                d["last_alive"] = int(time.time())
            results.append({"tag": tag, "url": d["url"], "status": st,
                            "latency": dt, "http": code, "note": note})

    # ---- 二审：等网络安静下来，把「会被判失效」的 TAG 的地址**串行**重探一遍 ----
    # 并发高潮会把本机出口挤满 / 触发对端按 IP 限速，批量把活站判死
    # （实测：fmcf.fun 单发 6/6 成功 q3，16 并发下 6 次重试全 reset）。
    # 只复核「本轮一条活着的都没有」的 TAG —— 其余 TAG 就算个别地址探挂了也不影响
    # 结论，全量复核会让一轮巡检拖到几分钟。二审仍死的，才真判死。
    if getattr(C, "HEALTH_REPROBE", True):
        time.sleep(float(getattr(C, "HEALTH_RETRY_SETTLE", 2.0)))
        gap = float(getattr(C, "HEALTH_REPROBE_GAP", 0.4))
        rto = min(float(getattr(C, "HEALTH_TIMEOUT", 12)), 7.0)
        revived = 0
        by_url = {(r["tag"], r["url"]): r for r in results}
        doomed = []
        for tag, ent in pool.items():
            if tags and tag not in tags:
                continue
            if survivable_count(ent) > 0:
                continue
            kind = _kind_of(ent.get("api"), ent.get("name"), tag)
            for d in ent.get("domains", []) or []:
                if d.get("status") == "alive":
                    continue
                if d.get("retired") or S.is_retired(tag, d.get("url") or ""):
                    continue      # 用户已判弃用，不必复核
                if not _dns_ok(S.norm_host(d.get("url") or "")):
                    continue      # 域名确实废弃，不必复核
                doomed.append((tag, d, kind))
        for tag, d, kind in doomed:
            time.sleep(gap)
            st, dt, code, n, final, note, q = probe(d["url"], timeout=rto,
                                                    kind=kind, attempts=1)
            d.update({"status": st, "latency": dt, "http": code, "bytes": n,
                      "quality": q, "checked": int(time.time()),
                      "final": final, "note": note})
            if st == "alive" and int(q or 0) >= 2:
                d["last_alive"] = int(time.time())
            if st == "alive":
                revived += 1
                if final and not S.same_site(final, d["url"]):
                    discovered.setdefault(tag, []).append(S.norm_base(final))
                rec = by_url.get((tag, d["url"]))
                new_rec = {"tag": tag, "url": d["url"], "status": st,
                           "latency": dt, "http": code,
                           "note": note + " · 串行复核转活"}
                if rec is not None:
                    results[results.index(rec)] = new_rec
                else:
                    results.append(new_rec)
        if doomed:
            store.log_event("health",
                            f"串行复核 {len(doomed)} 个地址，救回 {revived} 个被并发误判死的")

    # 自动吸收漂移出来的新域名
    for tag, urls in discovered.items():
        ent = pool.get(tag)
        if ent:
            kind = _kind_of(ent.get("api"), ent.get("name"), tag)
            added = S.merge_domains(ent, urls, origin="discovered")
            for u in added:
                # 新域名立即补测一次
                st, dt, code, n, final, note, q = probe(u, kind=kind)
                for d in ent["domains"]:
                    if d["url"] == u:
                        d.update({"status": st, "latency": dt, "http": code,
                                  "bytes": n, "quality": q,
                                  "checked": int(time.time()),
                                  "final": final, "note": note})
                        if st == "alive" and int(q or 0) >= 2:
                            d["last_alive"] = int(time.time())
                if st == "alive":
                    store.log_event("discover", f"{tag} 自动发现可用新域名 {u} ({dt}ms)")
                    results.append({"tag": tag, "url": u, "status": st,
                                    "latency": dt, "http": code, "note": "discovered"})
    return discovered, results


def pick_best(ent):
    """挑当前最优地址：先看内容质量（完整真站 q3 > 真站 q2 > 门户页 q1），再看延迟"""
    best = pick_best_detail(ent)
    return best["url"] if best else None


def pick_best_detail(ent):
    """同 pick_best，但返回整条记录，便于展示质量与体积"""
    alive = [d for d in ent.get("domains", [])
             if d.get("status") == "alive" and not d.get("retired")]
    if not alive:
        return None
    alive.sort(key=lambda d: (-(d.get("quality") or 0),
                              d.get("latency") or 99999, d.get("url", "")))
    return alive[0]


def usable_count(ent):
    """真正「能拿来用」的地址数：存活且带站点特征（q>=2）。

    用于**替换原地址**的判据 —— 质量不足的地址宁可不用。
    """
    return sum(1 for d in ent.get("domains", [])
               if d.get("status") == "alive" and (d.get("quality") or 0) >= 2
               and not d.get("retired"))


def survivable_count(ent, grace=None):
    """「有东西活着」的地址数：存活且 q>=1（含人机校验页、前端空壳），
    **外加最近 grace 秒内被探到过完整真站（q>=2）的地址**。

    用于**判断该站点是否应该被判失效 / 告警**。这些地址质量不足以替换原地址，
    但足以证明「站点没死、只是我们这种 IP 探测不到真相」，据此判死会误伤用户。

    【为什么要算「最近活着」—— 2026-10-17】实测这类站对机房 IP 是**成片**间歇性
    失败：正常窗口 9~10/10 成功，坏窗口连续 6 次全 reset。只按「本轮探到没有」
    判定，一段坏窗口就会把刚验证过的活站打成失效并告警，恢复后又报「已修复」，
    来回循环。记住 last_alive 就能把这种抖动吸收掉。grace=0 时退回旧口径。
    """
    if grace is None:
        grace = getattr(C, "ALIVE_GRACE_SEC", 0)
    now = time.time()
    n = 0
    for d in (ent or {}).get("domains", []) or []:
        if d.get("retired"):
            continue          # 已弃用域名不参与「站点是否还活着」的判断
        if d.get("status") == "alive" and (d.get("quality") or 0) >= 1:
            n += 1
            continue
        # 本轮没探到 —— 但不久前它还是完整真站，算「活着（存疑）」
        la = float(d.get("last_alive") or 0)
        if grace and la and (now - la) <= grace:
            n += 1
    return n


def summary(pool):
    tot = al = us = full = soft = 0
    for ent in pool.values():
        for d in ent.get("domains", []):
            tot += 1
            if d.get("status") == "alive":
                al += 1
                q = d.get("quality") or 0
                if q >= 2:
                    us += 1
                else:
                    soft += 1
                if q >= 3:
                    full += 1
    return {"domains_total": tot, "domains_alive": al,
            "domains_usable": us, "domains_full": full,
            "domains_soft": soft}
