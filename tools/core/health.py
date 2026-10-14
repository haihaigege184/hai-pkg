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


def _classify(body, nbytes):
    """按内容给质量分，返回 (quality, note)

    3 = 真实站点且页面完整    2 = 真实站点   1 = 仅存活(无站点特征)   0 = 不可用
    """
    low = _text_of(body).lower()
    has_marker = any(m in low for m in C.SITE_MARKERS)
    bad = next((b for b in C.BAD_PAGE_HINTS if b in low), None)

    if bad and not has_marker:
        return 0, f"非站点页(命中「{bad}」)"
    if not has_marker:
        # 有页面但没有站点结构：门户页 / 空壳站，只能当兜底
        return (1, "无站点特征(疑似门户页)") if nbytes >= C.SITE_MIN_BYTES else \
               (0, "响应体过小(疑似停放页)")
    if nbytes >= C.SITE_FULL_BYTES:
        return 3, ""
    return 2, ""


def _quality(status, nbytes):
    """兼容旧调用：仅按状态给粗分"""
    if status != "alive":
        return 0
    return 2 if nbytes >= C.REAL_MIN_BYTES else 1


def probe(url, timeout=None):
    """探测单个地址，返回 (status, latency_ms, http_code, bytes, final_url, note, quality)"""
    timeout = timeout or C.HEALTH_TIMEOUT
    t0 = time.time()
    try:
        r = _sess().get(url, timeout=timeout, verify=False, allow_redirects=True)
        dt = int((time.time() - t0) * 1000)
        body = r.content or b""
        n = len(body)
        final = r.url
        if r.status_code >= 400:
            return "dead", dt, r.status_code, n, final, f"HTTP {r.status_code}", 0
        if n < C.HEALTH_MIN_BYTES:
            return "dead", dt, r.status_code, n, final, "响应体过小(疑似停放页)", 0
        q, note = _classify(body, n)
        if q == 0:
            return "dead", dt, r.status_code, n, final, note, 0
        return "alive", dt, r.status_code, n, final, note, q
    except Exception as e:
        dt = int((time.time() - t0) * 1000)
        host = S.norm_host(url)
        if not _dns_ok(host):
            note = "DNS解析失败(域名已废弃)"
        elif type(e).__name__ in ("ReadTimeout", "ConnectTimeout"):
            note = "连接超时(站点无响应)"
        elif type(e).__name__ == "SSLError":
            note = "SSL证书异常(播放器可能拒绝)"
        else:
            note = type(e).__name__
        return "dead", dt, 0, 0, url, note, 0


def check_all(pool, only_dead=False, tags=None):
    """并发检查域名池。就地更新 pool 中每条记录的 status/latency/checked。
    返回 (discovered, results) —— discovered 是 {tag: [新域名]}
    """
    tasks = []
    for tag, ent in pool.items():
        if tags and tag not in tags:
            continue
        for d in ent.get("domains", []):
            if only_dead and d.get("status") == "alive":
                continue
            tasks.append((tag, d))
    results = []
    discovered = {}

    def work(item):
        tag, d = item
        st, dt, code, n, final, note, q = probe(d["url"])
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
            results.append({"tag": tag, "url": d["url"], "status": st,
                            "latency": dt, "http": code, "note": note})

    # 自动吸收漂移出来的新域名
    for tag, urls in discovered.items():
        ent = pool.get(tag)
        if ent:
            added = S.merge_domains(ent, urls, origin="discovered")
            for u in added:
                # 新域名立即补测一次
                st, dt, code, n, final, note, q = probe(u)
                for d in ent["domains"]:
                    if d["url"] == u:
                        d.update({"status": st, "latency": dt, "http": code,
                                  "bytes": n, "quality": q,
                                  "checked": int(time.time()),
                                  "final": final, "note": note})
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
    alive = [d for d in ent.get("domains", []) if d.get("status") == "alive"]
    if not alive:
        return None
    alive.sort(key=lambda d: (-(d.get("quality") or 0),
                              d.get("latency") or 99999, d.get("url", "")))
    return alive[0]


def usable_count(ent):
    """真正「能拿来用」的地址数：存活且带站点特征（q>=2）"""
    return sum(1 for d in ent.get("domains", [])
               if d.get("status") == "alive" and (d.get("quality") or 0) >= 2)


def summary(pool):
    tot = al = us = full = 0
    for ent in pool.values():
        for d in ent.get("domains", []):
            tot += 1
            if d.get("status") == "alive":
                al += 1
                q = d.get("quality") or 0
                if q >= 2:
                    us += 1
                if q >= 3:
                    full += 1
    return {"domains_total": tot, "domains_alive": al,
            "domains_usable": us, "domains_full": full}
