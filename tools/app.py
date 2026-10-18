"""oktv-hub —— OK影视 接口修复与热更新中枢

一个自托管的「接口配置 + 资产」分发服务：
  1. 把失效的网盘类接口地址替换为当前存活域名；
  2. 上行同步公开社区配置，自动发现漂移后的新域名并入池；
  3. 定时巡检域名池，自动选主/降级；
  4. 提供 /gw/<TAG>/ 网关反代，配置写死后也不会随域名漂移失效；
  5. 同时托管 pg.jar / js / lib，作为完整的自建接口源。

用法：
    python app.py run       # 启动服务（含后台巡检/同步线程）
    python app.py check     # 只跑一次巡检
    python app.py sync      # 只跑一次上游同步
    python app.py render    # 只重新生成一次配置
"""
import hashlib
import json
import os
import sys
import threading
import time

from flask import Blueprint, Flask, Response, jsonify, request, send_from_directory

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core import config as C          # noqa: E402
from core import (absbase, ckvault, dav, devices, gateway, health,  # noqa: E402
                  libfill, notify, patch, probe, relay, report,
                  sources as S, store, sync, verify)

app = Flask(__name__, static_folder=None)
bp = Blueprint("oktv", __name__)

F_ACCESS = os.path.join(C.LOG_DIR, "access.jsonl")

# 嗅探豁免：/api/ck* 是查询/管理接口，其响应与语义由自己的 handler 负责，
# 再走一遍通用嗅探只会重复入库。
#
# 注意：曾经这里也排除了 /hai/lib/tokenm.json —— 那是过度排除。通用嗅探只看
# 「请求」的 query/headers，不看响应体，所以下发 tokenm 不会被误当成播放；
# 而如果端哪天用 `GET /hai/lib/tokenm.json?quark_cookie=...` 这种形式把凭据
# 送回来，排除掉就等于把唯一的线索扔掉。
_SKIP_SNIFF = ("/api/ck",)

# CK 端点接受的方法全集：端怎么写都接得住（含扫码页可能用的 PATCH / OPTIONS）
_WRITE_METHODS = ("GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS")


def _skip_sniff(path):
    p = path or ""
    return any(s in p for s in _SKIP_SNIFF)


def _client_ip():
    """取真实客户端 IP。

    只用**代理自己设置、客户端无法伪造**的头：
      * CF-Connecting-IP —— Cloudflare 边缘强制写入并覆盖同名请求头，可信；
      * X-Real-IP       —— 本机 nginx 若配了 `proxy_set_header X-Real-IP $remote_addr` 才可信；
      * remote_addr     —— 局域网直连时的真实来源。

    **刻意不看 X-Forwarded-For**：CF 是在客户端已有的 XFF 后面 append 真实地址，
    而链子里第一个值才是客户端可以自由伪造的。早先的实现取 XFF 第一段，等于
    允许任何人塞一句 `X-Forwarded-For: 10.0.0.108` 就把自己伪装成局域网设备，
    从而把只该发给内网的真实网盘 CK 拿走。安全判定（_is_lan）直接挂钩这里，
    所以这个口子必须堵死。
    """
    for k in ("CF-Connecting-IP", "X-Real-IP"):
        v = request.headers.get(k)
        if v:
            return v.strip()
    return (request.remote_addr or "").strip()


_LAN_PREFIX = ("127.", "10.", "192.168.", "172.16.", "172.17.", "172.18.", "172.19.",
               "172.2", "172.30.", "172.31.", "::1", "169.254.")


def _is_lan(ip=None):
    ip = ip if ip is not None else _client_ip()
    if not ip:
        return True                      # 取不到就当内网（本机/云隧道场景）
    if ip in ("localhost", ""):
        return True
    return ip.startswith(_LAN_PREFIX)


@app.after_request
def _access_log(resp):
    """把每个请求的「来源 IP / UA / 关键头」记下来。

    电视机「配置获取失败」这类问题，光看服务端 200 是分不清
    「客户端没连上」和「客户端拿到但解析失败」的——这条日志能直接分辨。
    """
    try:
        os.makedirs(C.LOG_DIR, exist_ok=True)
        # 注意：不能直接用 os.path.getsize —— 文件不存在时它会抛异常，
        # 结果就是第一条日志永远写不出来（曾被这个坑掉整整一轮排查）。
        if os.path.exists(F_ACCESS) and os.path.getsize(F_ACCESS) > 8 * 1024 * 1024:
            os.replace(F_ACCESS, F_ACCESS + ".1")
        try:
            nbytes = -1 if resp.direct_passthrough else len(resp.get_data())
        except Exception:
            nbytes = -1
        rec = {
            "t": time.strftime("%Y-%m-%d %H:%M:%S"),
            "ip": _client_ip(),
            "m": request.method, "p": request.path,
            "q": request.query_string.decode("utf-8", "ignore")[:1000],
            "s": resp.status_code,
            "ua": (request.headers.get("User-Agent") or "")[:160],
            "ae": request.headers.get("Accept-Encoding") or "",
            "range": request.headers.get("Range") or "",
            "conn": request.headers.get("Connection") or "",
            "len": nbytes,
        }
        with open(F_ACCESS, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception as e:
        store.log_event("error", f"访问日志写入失败: {type(e).__name__}: {e}")

    # 写入探针 + 设备台账：把「客户端有没有把 CK 送回来」变成可查的事实。
    # 网盘 CK 的采集天生是盲区（扫码与播放都不经过中枢），所以这里不做取舍，
    # 凡是有写入嫌疑的请求一律留证，再由 /api/probe 下结论。
    try:
        ip = _client_ip()
        ua = request.headers.get("User-Agent") or ""
        qs = request.query_string.decode("utf-8", "ignore")
        body = b""
        # 必须先初始化：下面整块在「中枢自身管理流量」时会被跳过，
        # 而末尾的 if ck_hit 在块外 —— 不初始化就会 UnboundLocalError，
        # 静默丢掉这一条探针/台账记录（09:37 实测连报 4 次）。
        ck_hit = False
        # 中枢自身的管理/自测流量（/api/status、/api/probe/reset…）不进证据也不进台账，
        # 否则「清零」这个动作本身会被立刻记成一次写入，把干净的测试基线弄脏。
        if not probe.is_ignored(request.path):
            if request.method not in ("GET", "HEAD"):
                try:
                    clen = int(request.headers.get("Content-Length") or 0)
                except Exception:
                    clen = 0
                if clen <= 262144:
                    body = request.get_data(cache=True) or b""
                # form 请求的原始 body 取不回来（Werkzeug 解析表单时消费掉了流），
                # 但路由里已经解析过 request.form —— 用拼回来的形式补上。
                # 少了这一步，form 形态的 CK 上报在探针里就是「空 body」，
                # 设备台账会把贡献方误判成纯消费方。
                if not body:
                    try:
                        form = request.form
                        if form:
                            body = "&".join("%s=%s" % (k, form.get(k)) for k in form.keys()).encode("utf-8")
                    except Exception:
                        pass
            prec = probe.record(request.method, request.path, qs, dict(request.headers),
                                body, resp.status_code, ip, ua)
            is_write = request.method not in ("GET", "HEAD")
            ck_hit = bool(prec and probe.looks_like_ck(prec))
            if prec is not None or is_write:
                devices.touch(ip, ua=ua, path=request.path,
                              kind="write" if is_write else "read", seen_ck=ck_hit)
        if ck_hit:
            store.log_event("ck", f"探针捕获到携带 CK 的请求：{request.method} "
                                  f"{request.path} 来自 {ip}")
    except Exception as e:
        store.log_event("error", f"探针/台账记录失败: {type(e).__name__}: {e}")

    # 网盘 CK 自动嗅探：客户端把带 CK 的请求送到中枢（且成功）就算「播放成功」，
    # 该渠道 CK 入库并 hits+1 —— 这是「播放成功即保存」的自动路径。
    if not _skip_sniff(request.path):
        try:
            ckvault.sniff_request(request.path, request.query_string.decode("utf-8", "ignore"),
                                  dict(request.headers), resp.status_code)
        except Exception as e:
            store.log_event("error", f"CK 嗅探失败: {type(e).__name__}: {e}")
    return resp

POOL = {}                 # { TAG: {tag, api, proxy, rules, name, domains:[...]} }
REGISTRY = {}
_last_render = {"ts": 0, "jsm": None}
F_HEALTH_STATE = C.F_HEALTH_STATE          # 兼容旧引用：{TAG: {alive, ts, streak, notified_offline}}

# ----------------------------------------------------------------------------
# 初始化
# ----------------------------------------------------------------------------


def bootstrap():
    """加载基线 -> 播种域名池 -> 合并已落盘的数据"""
    global REGISTRY
    base = patch.load_baseline()
    REGISTRY = S.build_sources(base)

    saved = store.load(C.F_DOMAINS, None)
    if saved:
        POOL.update(saved)
    # 基线 + 内置历史域名知识库播种（已有条目的域名做并集）
    sync.seed_pool_from_baseline(POOL, REGISTRY)
    dropped = _prune_pool()
    _seed_last_alive()
    gateway.set_pool(POOL)
    _flush()
    store.log_event("boot", f"中枢启动，登记接口源 {len(REGISTRY)} 个，域名池 {len(POOL)} 组"
                            + (f"，清理私有/占位地址 {dropped} 条" if dropped else ""))
    # 补全 lib 资源（缺失时播放器会对 404 疯狂重试）
    libfill.fill()
    # 把保险库里的网盘 CK 渲染进 lib/tokenm.json（动态路由始终以保险库为准，这里落盘一份做备份）
    ckvault.write_tokenm()
    _cks = ckvault.summary()
    if _cks["configured"]:
        store.log_event("ck", f"网盘 CK 保险库：已配置 {_cks['configured']} 个渠道"
                              f"（有效 {_cks['valid']}），注入字段 {_cks['injected_fields']} 个")


def _seed_last_alive():
    """给「我们主动写入过的 base」补一份 last_alive，让防误报宽限期立刻生效。

    【为什么 —— 2026-10-17】bases.json 里记着「这条地址是我们验证过才写进配置的」。
    但刚部署/刚重启时，新加的 last_alive 字段还是空的，一旦这一轮又撞上探测坏窗口，
    就会出现「配置明明指向一条我们刚验证过的好地址，却仍报失效」的自相矛盾。
    这里以启动时刻兜底记一次；之后每轮巡检探通都会刷新它，探不通则会自然过期。
    """
    now = int(time.time())
    try:
        pairs = (patch._applied_bases() or {}).items()
    except Exception:
        return
    for tag, base in pairs:
        if not base:
            continue
        for d in (POOL.get(tag) or {}).get("domains", []) or []:
            if S.same_site(d.get("url") or "", base):
                d["last_alive"] = max(int(d.get("last_alive") or 0), now)


def _prune_pool():
    """清掉池里的私有/占位地址（上游配置常用 127.0.0.1 当本地代理占位符）"""
    n = 0
    for ent in POOL.values():
        keep = []
        for d in ent.get("domains", []) or []:
            if S.is_public_host(S.norm_host(d.get("url", ""))):
                keep.append(d)
            else:
                n += 1
        ent["domains"] = keep
    return n


def _flush():
    store.save(C.F_DOMAINS, POOL)


# ----------------------------------------------------------------------------
# 核心动作
# ----------------------------------------------------------------------------


def _tag_alive():
    """统计每个 TAG 当前「还有东西活着」的域名数（q>=1）

    为什么不用 usable_count(q>=2)：
      数据中心 IP 会被 Cloudflare/WAF 返回 403 或人机校验页，q 只能是 1。
      拿它当死站会**误报失效** —— 用户在家里明明能用（实测 hdhaven/re0.me/观影
      都是 CF 拦截，fmcf.fun 是瞬时失败被误判）。所以告警判据放宽到 q>=1：
      只要站点还有响应，就不打扰用户；真要判死必须是「一个响应的都没有」。
    """
    return {tag: health.survivable_count(ent) for tag, ent in POOL.items()}


def _name_of(tag):
    return (POOL.get(tag, {}) or {}).get("name") or (REGISTRY.get(tag, {}) or {}).get("name", "")


def _sites_of(tag):
    return (REGISTRY.get(tag, {}) or {}).get("sites", []) or [tag]


def _apply_health_notify():
    """对比上次巡检结果，接口由「有可用」变「无可用」→ 告警，反之 → 恢复通知

    【为什么要加滞后 —— 2026-10-15】
    用户反馈「蜡笔接口反复失效和修复通知」。原实现只要 alive 从 >0 掉到 0 就告警，
    但域名池实测本身就在抖（偶发超时、偶发 5xx、CDN 节点差异）。于是：
      alive 3 → 0（告警）→ 1（恢复通知）→ 0（告警）…
    即使 notify.send 有去重，30~60 分钟后照样能再发一轮，用户侧就是「反复骚扰」。

    现在改为「连续 OFFLINE_CONFIRM_ROUNDS 轮都无可用」才判定失效并告警，
    而**恢复永远立即生效**（宁可早报好消息）。同一轮掉线只告警一次，靠
    health_state 里的 notified_offline 标记；恢复后标记清零，下次掉线可再告警。
    """
    now_map = _tag_alive()
    prev = store.load(C.F_HEALTH_STATE, {}) or {}
    need = max(1, int(getattr(C, "OFFLINE_CONFIRM_ROUNDS", 2)))
    results = {"failed": [], "recovered": []}
    new_state = {}
    now = int(time.time())

    for tag, n in now_map.items():
        p = prev.get(tag) or {}
        pv = p.get("alive")
        streak = int(p.get("streak") or 0)
        notified = bool(p.get("notified_offline"))

        if n > 0:
            # 有可用：立刻恢复正常。
            # 注意「恢复通知」只在**确实告过警**时才发 —— 抖动被阈值压下去的那些轮
            # 从未告警，此时再报「已恢复」纯属噪音（实测第 3、5 轮会误报恢复）。
            if notified:
                best = health.pick_best(POOL.get(tag) or {})
                results["recovered"].append(tag)
                if C.NOTIFY_ON_FIX:
                    notify.send(notify.fmt_fixed(tag, _name_of(tag), best or "-"),
                                dedup_key=f"fix:{tag}", dedup_sec=600)
            streak = 0
            notified = False
        else:
            streak += 1
            confirmed = streak >= need
            # 只在「刚被确认失效」这一刻告警一次
            if confirmed and not notified:
                results["failed"].append(tag)
                if C.NOTIFY_ON_FAIL:
                    notify.send(notify.fmt_fail(tag, _name_of(tag), _sites_of(tag)),
                                dedup_key=f"fail:{tag}", dedup_sec=7200)
                notified = True
                store.log_event("offline",
                                f"{tag} 连续 {streak} 轮无可用地址，判定失效")
            if not confirmed:
                store.log_event("health",
                                f"{tag} 连续 {streak} 轮无可用（未达 {need} 轮阈值，暂不判定）")

        new_state[tag] = {"alive": n, "ts": now, "streak": streak,
                          "notified_offline": notified}

    store.save(C.F_HEALTH_STATE, new_state)
    return results


def do_check(only_dead=False, tags=None, notify_changes=True, render=True):
    """全池巡检。

    render=True（默认）时巡检结束后立刻重新生成配置 —— 接口选择页要**按最新
    延迟重排**（用户 2026-10-17 要求：每次巡检更新排序）。修复流程里已经在
    紧接着调 do_render()，那些调用点传 render=False 避免重复渲染。
    """
    disc, res = health.check_all(POOL, only_dead=only_dead, tags=tags)
    _flush()
    store.set_state(last_health=int(time.time()))
    alive = sum(1 for r in res if r["status"] == "alive")
    store.log_event("health", f"巡检 {len(res)} 个地址，存活 {alive} 个")
    changes = _apply_health_notify() if notify_changes else {"failed": [], "recovered": []}
    if render:
        try:
            do_render()
        except Exception as e:
            store.log_event("error", f"巡检后重渲染失败: {type(e).__name__}: {e}")
    return {"checked": len(res), "alive": alive, "results": res,
            "failed": changes["failed"], "recovered": changes["recovered"]}


def do_sync():
    stat = sync.sync_pool(POOL, REGISTRY)
    _flush()
    do_render()
    return stat


def do_repair(reason="manual"):
    """一次完整的检测-修复闭环：上游同步 -> 全池巡检 -> 重新生成并落盘 hai 包"""
    t0 = time.time()
    store.log_event("repair", f"开始修复流程（触发源：{reason}）")
    before = _tag_alive()
    sync_stat = sync.sync_pool(POOL, REGISTRY)
    _flush()
    check_stat = do_check(render=False)
    render_stat = do_render()
    after = _tag_alive()

    fixed = [t for t, n in after.items() if n > 0 and before.get(t, 0) == 0]
    still = sorted(t for t, n in after.items() if n == 0)
    libfill.fill()          # 新接入的站点可能引用新的 lib 文件
    stat = {
        "ok": True,
        "reason": reason,
        "elapsed": round(time.time() - t0, 1),
        "sync": {"sources": len(sync_stat.get("sources", [])),
                 "new_domains": sync_stat.get("new_domains", 0),
                 "harvested": sync_stat.get("harvested", 0)},
        "checked": check_stat["checked"],
        "alive": check_stat["alive"],
        "fixed": fixed,
        "still_offline": still,
        "changed_sites": len(render_stat.get("changed", [])),
        "pkg": C.PKG_DIR,
    }
    store.log_event("repair", f"修复完成：恢复 {len(fixed)} 个，仍失效 {len(still)} 个，"
                              f"耗时 {stat['elapsed']}s")
    return stat


# --------------------------------------------------------- 修复任务：后台执行 + 进度
# 「修复接口」要跑 1~3 分钟。同步调用期间调用方拿不到任何中间信息 —— 用户看到的是
# 「发了命令之后长时间没动静」，无法判断到底在跑还是挂了。
# 这里提供一个单例后台任务：带 background=1 时立即返回，执行过程中把阶段写进
# 模块级快照，调用方用 /api/repair/status 轮询，每进入新阶段就能回报一条进度。
# 不传 background 时行为与以前完全一致（同步返回），既有调用方不受影响。

_REPAIR = {
    "running": False, "stage": "idle", "step": 0, "total": 3,
    "note": "", "reason": "", "started": 0, "updated": 0, "finished": 0,
    "sync": None, "result": None, "error": None,
}
_REPAIR_LOCK = threading.Lock()


def _repair_set(**kw):
    with _REPAIR_LOCK:
        _REPAIR.update(kw)
        _REPAIR["updated"] = int(time.time())


def _repair_snapshot():
    with _REPAIR_LOCK:
        return dict(_REPAIR)


def do_repair_bg(reason="api"):
    """后台跑一次完整修复，逐阶段写进度（供 /api/repair/status 轮询）

    阶段与 do_repair 保持一一对应，最后一步仍会 libfill，语义不缩水。
    """
    t0 = time.time()
    # 无论从哪条路径进来（含同步调用），先把上一轮的成果字段清干净，
    # 否则轮询方会把残留的 check / result 当成本轮进度。
    _repair_set(sync=None, check=None, result=None, error=None)
    try:
        _repair_set(stage="sync", step=1, note="上游同步（拉取社区配置、发现新域名）")
        before = _tag_alive()
        sync_stat = sync.sync_pool(POOL, REGISTRY)
        _flush()
        _repair_set(sync={
            "sources": len(sync_stat.get("sources", [])),
            "new_domains": sync_stat.get("new_domains", 0),
            "harvested": sync_stat.get("harvested", 0),
        })

        _repair_set(stage="check", step=2, note="全池巡检（逐个地址探测可用性）")
        check_stat = do_check(render=False)
        _repair_set(check={"checked": check_stat.get("checked", 0),
                           "alive": check_stat.get("alive", 0)})

        _repair_set(stage="render", step=3, note="重新生成 hai 本地包")
        render_stat = do_render()
        libfill.fill()

        after = _tag_alive()
        fixed = [t for t, n in after.items() if n > 0 and before.get(t, 0) == 0]
        still = sorted(t for t, n in after.items() if n == 0)
        stat = {
            "ok": True, "reason": reason,
            "elapsed": round(time.time() - t0, 1),
            "sync": _repair_snapshot().get("sync") or {},
            "checked": check_stat["checked"],
            "alive": check_stat["alive"],
            "fixed": fixed,
            "still_offline": still,
            "changed_sites": len(render_stat.get("changed", [])),
            "pkg": C.PKG_DIR,
        }
        store.log_event("repair", f"修复完成：恢复 {len(fixed)} 个，"
                                  f"仍失效 {len(still)} 个，耗时 {stat['elapsed']}s")
        _repair_set(stage="done", step=3, note="完成", running=False,
                    result=stat, error=None, finished=int(time.time()))
    except Exception as e:
        store.log_event("repair", f"修复失败：{type(e).__name__}: {e}")
        _repair_set(stage="error", running=False, finished=int(time.time()),
                    error=f"{type(e).__name__}: {e}")


def do_daily():
    """每日巡检：修复 + 汇总报告 + 通知管理员"""
    stat = do_repair(reason="daily")
    st = health.summary(POOL)
    offline = [] if not stat["still_offline"] else \
        [f"{_name_of(t) or t}" for t in stat["still_offline"]]
    recovered = [f"{_name_of(t) or t}" for t in stat["fixed"]]
    if C.NOTIFY_DAILY:
        notify.send(notify.fmt_daily(
            {"tags": len(POOL), "alive": st["domains_usable"],
             "total": st["domains_total"], "alive_all": st["domains_alive"],
             "full": st["domains_full"],
             "usable_tags": sum(1 for t in _tag_alive().values() if t > 0),
             "sync_sources": stat["sync"]["sources"],
             "new_domains": stat["sync"]["new_domains"] + stat["sync"]["harvested"],
             "fixed": len(recovered)},
            offline, recovered), dedup_key="daily", dedup_sec=3600 * 20)
    store.set_state(last_daily=int(time.time()), last_daily_stat=stat)
    store.log_event("daily", f"每日巡检完成：可用 {st['domains_usable']}/{st['domains_total']}，"
                             f"仍失效 {len(offline)} 个")
    return stat


def do_render(mode=None):
    jsm, stat = patch.regenerate(POOL, mode=mode)
    _last_render["ts"] = time.time()
    _last_render["jsm"] = jsm
    return stat


def get_config(force=False):
    """取当前配置：超过 60s 或强制时重新生成"""
    if force or not _last_render["jsm"] or time.time() - _last_render["ts"] > 60:
        do_render()
    return _last_render["jsm"]


# ----------------------------------------------------------------------------
# 后台调度
# ----------------------------------------------------------------------------


def scheduler():
    time.sleep(5)
    while True:
        try:
            st = store.get_state()
            now = time.time()
            if now - st.get("last_health", 0) >= C.HEALTH_INTERVAL:
                do_check()
            if now - st.get("last_sync", 0) >= C.SYNC_INTERVAL:
                do_sync()
            if _due_daily():
                do_daily()
        except Exception as e:
            store.log_event("error", f"调度异常: {type(e).__name__}: {e}")
        time.sleep(60)


def _due_daily():
    """判断是否到了每日巡检时间（每天在 DAILY_HOUR:DAILY_MINUTE 之后触发一次）"""
    lt = time.localtime()
    if lt.tm_hour != C.DAILY_HOUR:
        return False
    if lt.tm_min < C.DAILY_MINUTE:
        return False
    last = store.get_state().get("last_daily", 0)
    return time.strftime("%Y-%m-%d", time.localtime(last)) != time.strftime("%Y-%m-%d", lt)


# ----------------------------------------------------------------------------
# 路由：配置与资产
# ----------------------------------------------------------------------------


@bp.route("/")
def index():
    return send_from_directory(os.path.join(C.BASE_DIR, "web"), "index.html")


_jsm_cache = {"key": None, "body": None}


def _jsm_body(req, tail):
    """渲染 jsm.json 的响应体。

    这里做「相对路径绝对化」：原版包是给「解压到本地再用本地 jsm.json」的用法
    写的，`./lib/tokenm.json` 在那种场景下是本地文件路径；而我们的用法是 HTTP
    配置源，同一个字符串到底被解析成网络地址还是端上的本地文件，取决于 PG 的
    内部实现。tokenm.json 恰恰是网盘 CK 的载体，一旦它落到端本地，扫码得到的
    CK 就永远回不到中枢。把路径渲染成端自己正在用的那个绝对地址，歧义即消除。
    """
    jsm = get_config()
    suffix = ""
    if getattr(C, "ABS_PATHS", True):
        base, pfx = absbase.site_base(req, C.PUBLIC_BASE or "")
        if base:
            suffix = base
            jsm = absbase.absolutize(jsm, base, prefix=pfx)
    key = (suffix, tail, _last_render["ts"], ckvault.version())
    if _jsm_cache["key"] == key and _jsm_cache["body"]:
        return _jsm_cache["body"]
    body = json.dumps(jsm, ensure_ascii=False, indent=1)
    _jsm_cache["key"], _jsm_cache["body"] = key, body
    return body


@bp.route("/jsm.json")
def jsm_json():
    return Response(_jsm_body(request, "/jsm.json"),
                    mimetype="application/json; charset=utf-8",
                    headers={"Cache-Control": "no-cache",
                             "Access-Control-Allow-Origin": "*"})


@bp.route("/version.json")
def version_json():
    st = store.get_state()
    return jsonify({"version": st.get("version", "1.0.0"),
                    "mode": st.get("mode", "direct"),
                    "last_sync": st.get("last_sync", 0),
                    "last_health": st.get("last_health", 0),
                    "sites": len(get_config().get("sites", []))})


@bp.route("/pg.jar")
def pg_jar():
    return send_from_directory(os.path.join(C.BASE_DIR, "assets"), "pg.jar",
                               mimetype="application/java-archive")


# ------------------------- hai 本地包（对外发布物） -------------------------

def _pkg_dir():
    return C.PKG_DIR if os.path.isdir(C.PKG_DIR) else os.path.join(C.BASE_DIR, "assets")


@bp.route("/hai/")
@bp.route("/hai")
def hai_index():
    st = store.get_state()
    return jsonify({
        "package": C.PKG_NAME,
        "version": st.get("version", "1.0.0"),
        "config": f"{C.PUBLIC_BASE}/hai/jsm.json" if C.PUBLIC_BASE else "/hai/jsm.json",
        "jar": f"{C.PUBLIC_BASE}/hai/pg.jar" if C.PUBLIC_BASE else "/hai/pg.jar",
        "sites": len(get_config().get("sites", [])),
        "last_health": st.get("last_health", 0),
        "last_daily": st.get("last_daily", 0),
    })


@bp.route("/hai/jsm.json")
def hai_jsm():
    """hai 包的主配置，永远回给最新生成的版本"""
    return Response(_jsm_body(request, "/hai/jsm.json"),
                    mimetype="application/json; charset=utf-8",
                    headers={"Cache-Control": "no-cache",
                             "Access-Control-Allow-Origin": "*"})


@bp.route("/hai/version.json")
def hai_version():
    return version_json()


@bp.route("/hai/lib/<path:p>", methods=list(_WRITE_METHODS))
def hai_lib(p):
    if p.replace("\\", "/").lower() == "tokenm.json":
        return _tokenm(p)
    d = os.path.join(_pkg_dir(), "lib")
    return _safe_send(d, p)


# ----------------------------------------------------------------------------
# 网盘 CK：动态 tokenm.json（一处保存，多端自动生效）
# ----------------------------------------------------------------------------


def _apply_ck_payload(data, source):
    """接受三种载荷：
       {channel, value} / {channel, fields{}} / 一整份 tokenm.json
    """
    if not isinstance(data, dict):
        return {"ok": False, "error": "需要 JSON 对象"}
    if data.get("channel"):
        return ckvault.report(data["channel"],
                              data.get("fields") or data.get("value") or data.get("ck") or "",
                              source=source,
                              state=data.get("state"),
                              force=bool(data.get("force")),
                              validate=C.CK_VALIDATE)
    res = ckvault.report_fields(data, source=source, validate=C.CK_VALIDATE)
    if not res:
        return {"ok": False, "error": "未识别到网盘 CK 字段"}
    return {"ok": True, "results": res}


def _payload_chunks():
    """把一次请求里**所有**可能承载 CK 的地方收集成候选文本。

    端点不可能预先知道 PG 用哪种形式把凭据送回来 —— 可能是一整份 tokenm.json
    （PUT body）、可能是一条 `quark_cookie=xxx`（form / query）、可能直接放在
    Cookie 头里、也可能是扫码页那种 multipart 提交。所以不做取舍，全部收上来，
    交给 ckvault.extract 去识别；识别不出就当普通读取处理。

    返回 [(来源标签, 文本), ...]
    """
    chunks = []
    qs = request.query_string.decode("utf-8", "ignore")
    if qs:
        chunks.append(("query", qs))

    try:
        clen = int(request.headers.get("Content-Length") or 0)
    except Exception:
        clen = 0

    if request.method not in ("GET", "HEAD", "OPTIONS") and clen <= 262144:
        ct = (request.content_type or "").lower()
        if "multipart/form-data" in ct or "x-www-form-urlencoded" in ct:
            try:
                for k, v in request.form.items():
                    chunks.append(("form:" + k, "%s=%s" % (k, v)))
            except Exception:
                pass
            try:
                for k, v in request.files.items():
                    chunks.append(("file:" + k, v.filename or ""))
            except Exception:
                pass
        else:
            raw = b""
            try:
                raw = request.get_data(cache=True) or b""
            except Exception:
                raw = b""
            if raw:
                chunks.append(("body", raw.decode("utf-8", "replace")))

    for k in ("Authorization", "X-CK", "X-Cookie", "X-Token",
              "X-Quark-Cookie", "X-Baidu-Cookie", "X-Tokenm"):
        v = request.headers.get(k)
        if v:
            chunks.append(("header:" + k, v))

    # Cookie 有两条来源，都要：
    #   1. 原始头 —— 最保真（一整条 `quark_cookie=__pus=…; __kp=…`）
    #   2. Flask 解析后的 cookie 字典 —— 兜底。有些 WSGI 服务器 / 测试客户端
    #      会把 Cookie 头剥离出 headers，只留解析结果，此时来源 1 是空的。
    #      拼回去时用 `; ` 连接，正好还原成原来的串（_CK_FIELD_RE 的值部分
    #      允许分号，所以能完整取回整条凭据，而不是被截断成半截）。
    # Cookie 有两条来源，但**只用先到的那条**：
    #   1. 原始头 —— 最保真（一整条 `quark_cookie=__pus=…; __kp=…`）
    #   2. Flask 解析后的 cookie 字典 —— 兜底。有些 WSGI 服务器会把 Cookie 头
    #      剥离出 headers，只留解析结果，此时来源 1 是空的。
    # 为什么互斥：解析后的字典是按 `;` 拆开的，拼回去虽然大多能还原，但遇到
    # 值里本身就带 `=` 的凭据（夸克 cookie 就是这样）会有残缺风险。残缺值一旦
    # 入库，会顶掉刚写进去的完整值 —— 宁可不用，也不能用坏的覆盖好的。
    raw_cookie = request.headers.get("Cookie") or request.environ.get("HTTP_COOKIE")
    if raw_cookie:
        chunks.append(("header:Cookie", raw_cookie))
    else:
        try:
            jar = request.cookies
            if jar:
                parts = ["%s=%s" % (k, jar.get(k)) for k in list(jar.keys())[:60]]
                chunks.append(("cookies", "; ".join(parts)[:8192]))
        except Exception:
            pass
    return chunks


def _absorb_payload(source):
    """尝试从当前请求里吸收 CK。

    返回 (识别到的条数, 结果列表)；0 表示这不是一次 CK 写入。
    """
    all_hits = []
    for tag, text in _payload_chunks():
        if not text or not text.strip():
            continue
        try:
            for ch, fields, src in ckvault.extract(text, source="%s %s/%s" % (source, _client_ip(), tag)):
                all_hits.append((ch, fields, src))
        except Exception:
            continue
        # 也可以是「整份 tokenm 字段」形态
        if text.lstrip().startswith("{"):
            try:
                obj = json.loads(text)
                if isinstance(obj, dict):
                    for f, v in obj.items():
                        ch = ckvault._FIELD2CH.get(str(f).lower())
                        if ch and str(v or "").strip():
                            all_hits.append((ch, {str(f).lower(): str(v).strip()},
                                             source + " /json"))
            except Exception:
                pass
    if not all_hits:
        return 0, []

    seen, out = set(), []
    for ch, fields, src in all_hits:
        key = (ch, tuple(sorted(fields.items())))
        if key in seen:
            continue
        seen.add(key)
        out.append(ckvault.report(ch, fields, source=src, validate=C.CK_VALIDATE))
    if out:
        ckvault.write_tokenm()
        store.log_event("ck", "tokenm 写入端点吸收 %d 条 CK（来源 %s）"
                              % (len(out), _client_ip()))
    return len(out), out


def _tokenm(p):
    """网盘 CK 的读写端点（tokenm.json）

    读：GET 返回「模板 + 保险库当前最优 CK」，公网来源只拿到空模板。
    写：任何方法、任何载荷形态下出现凭据字段都会被吸收并入库 —— 这一端点是
        「扫码设置一次，全屋自动生效」的上游入口。
    """
    if not _check_ck_source():
        return jsonify({"ok": False, "error": "该来源不允许上报 CK"}), 403

    if request.method == "OPTIONS":
        return Response("", 204, headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, POST, PUT, PATCH, OPTIONS",
            "Access-Control-Allow-Headers": "*",
        })

    # ---- 写入路径：任何方法都先尝试吸收 ----
    looks_write = (request.method not in ("GET", "HEAD")) or bool(request.query_string)
    if looks_write:
        try:
            n, results = _absorb_payload("tokenm")
            if n:
                return jsonify({"ok": True, "written": n, "results": results,
                                "version": ckvault.version()})
        except Exception as e:
            store.log_event("error", "tokenm 吸收异常: %s: %s" % (type(e).__name__, e))
        if request.method not in ("GET", "HEAD"):
            # 端写了一个我们认不出来的东西 —— 不报错（避免端放弃重试），
            # 但要留下明确记录，原始报文已由探针落盘。
            store.log_event("ck", "tokenm 收到写入但未识别出网盘 CK（%s %s，%d 字节）"
                                  % (request.method, _client_ip(),
                                     len(request.get_data(cache=True) or b"")))
            return jsonify({"ok": False, "error": "未识别到网盘 CK 字段",
                            "hint": "原始报文已记入 logs/probe.jsonl"})

    # ---- 读取路径 ----
    base, pfx = absbase.site_base(request, C.PUBLIC_BASE or "")
    # 中枢根（不含配置根目录）：/gw/<tag>/<path> 是绝对路由，不能用 base 拼
    hub = (absbase.base_from_request(request, C.PUBLIC_BASE or "") + C.PREFIX).rstrip("/")
    lan = C.CK_PUBLIC or _is_lan()
    body = ckvault.render_text(lan=lan, base=base, prefix=pfx, hub=hub)
    etag = '"ck-%d-%s"' % (ckvault.version(), hashlib.md5(body.encode()).hexdigest()[:10])
    if request.headers.get("If-None-Match") == etag:
        return Response(status=304, headers={"ETag": etag,
                                             "X-CK-Version": str(ckvault.version())})
    if request.method == "HEAD":
        return Response("", 200, headers={
            "ETag": etag, "X-CK-Version": str(ckvault.version()),
            "X-CK-Scope": "lan" if lan else "public-masked"})
    return Response(body, mimetype="application/json; charset=utf-8", headers={
        "Cache-Control": "no-cache, must-revalidate",
        "ETag": etag,
        "X-CK-Version": str(ckvault.version()),
        "X-CK-Scope": "lan" if lan else "public-masked",
        "Access-Control-Allow-Origin": "*",
    })


def _check_ck_source():
    if not C.CK_ALLOW_PREFIX:
        return True
    ip = _client_ip()
    return any(ip.startswith(p) for p in C.CK_ALLOW_PREFIX) or ip in ("127.0.0.1", "::1")


@bp.route("/hai/lib/tokenm.json", methods=list(_WRITE_METHODS))
@bp.route("/lib/tokenm.json", methods=list(_WRITE_METHODS))
def tokenm_dynamic():
    """兼容两种配置挂载方式：/hai/lib/... 与根路径 /lib/..."""
    return _tokenm("tokenm.json")


@bp.route("/hai/js/<path:p>")
def hai_js(p):
    d = os.path.join(_pkg_dir(), "js")
    return _safe_send(d, p)


@bp.route("/hai/pg.jar")
def hai_jar():
    f = os.path.join(_pkg_dir(), "pg.jar")
    if not os.path.exists(f):
        return "not found", 404
    return send_from_directory(_pkg_dir(), "pg.jar",
                               mimetype="application/java-archive")


def _safe_send(d, p):
    full = os.path.normpath(os.path.join(d, p))
    if not full.startswith(os.path.normpath(d)):
        return "forbidden", 403
    return send_from_directory(d, p)


@bp.route("/lib/<path:p>")
def lib_assets(p):
    if p.replace("\\", "/").lower() == "tokenm.json":
        return _tokenm(p)
    d = os.path.join(C.BASE_DIR, "assets", "lib")
    full = os.path.normpath(os.path.join(d, p))
    if not full.startswith(os.path.normpath(d)):
        return "forbidden", 403          # 目录穿越防护
    return send_from_directory(d, p)


@bp.route("/js/<path:p>")
def js_assets(p):
    d = os.path.join(C.BASE_DIR, "assets", "js")
    full = os.path.normpath(os.path.join(d, p))
    if not full.startswith(os.path.normpath(d)):
        return "forbidden", 403
    return send_from_directory(d, p)


# ----------------------------------------------------------------------------
# 路由：网关反代
# ----------------------------------------------------------------------------


# strict_slashes=False：`/gw/dav` 与 `/gw/dav/` 都要直接命中，不能走 308。
# WebDAV 客户端遇到 308 的处理各家不一（有的丢方法、有的不跟随重定向），
# 而 PROPFIND/PUT 一旦被重定向基本就废了。
@bp.route("/gw/<tag>", defaults={"path": ""}, strict_slashes=False,
           methods=["GET", "POST", "PUT", "DELETE", "HEAD", "OPTIONS", "PATCH",
                    "PROPFIND", "MKCOL"])
@bp.route("/gw/<tag>/<path:path>", strict_slashes=False,
           methods=["GET", "POST", "PUT", "DELETE", "HEAD", "OPTIONS", "PATCH",
                    "PROPFIND", "MKCOL"])
def gw(tag, path):
    # 内置 WebDAV 端点：接管 PG「网盘及弹幕配置 → WEBDAV 缓存网盘信息」。
    # 挂在 /gw 之下是刻意的 —— CF 隧道的 ingress 只放行 /hai 与 /gw，
    # 这样局域网与公网用的是同一个地址形态。
    if tag.lower() == "dav":
        # PROPFIND 的 <D:href> 必须是「客户端自己请求时用的路径前缀」，
        # 否则端拿到的绝对路径对不上（局域网是 /gw/dav，公网老入口是 /oktv/gw/dav）。
        base_url = request.path
        if path and base_url.endswith(path):
            base_url = base_url[:-len(path)]
        out = dav.handle(path or "", request.method, dict(request.headers),
                         request.get_data(cache=True), _client_ip(),
                         base_url.rstrip("/") or "/gw/dav")
        return Response(out[0], out[1], headers=out[2])

    body = (request.get_data(cache=True)
            if request.method in ("POST", "PUT", "PATCH", "DELETE") else None)
    query = request.query_string.decode("utf-8", "ignore")

    # 外部中转优先：这类路由指向**固定上游**（如网盘换 token 的接口），
    # 不参与站点域名池的故障转移。未登记的路由 relay.handle 返回 None，
    # 落回下面的域名池反代。
    out = relay.handle(tag, path, request.method, dict(request.headers),
                       body, query, ip=_client_ip())
    if out is not None:
        raw, code, hdrs = out
        return Response(raw, code, headers=hdrs)

    raw, code, err, hdrs = gateway.proxy(tag, path, request.method,
                                         dict(request.headers), body, query)
    if raw is None:
        return Response(json.dumps({"error": err, "tag": tag}, ensure_ascii=False),
                        502, mimetype="application/json")
    return Response(raw, code, headers=hdrs)


# ----------------------------------------------------------------------------
# 路由：管理 API
# ----------------------------------------------------------------------------


@bp.route("/api/status")
def api_status():
    st = store.get_state()
    s = health.summary(POOL)
    return jsonify({
        "state": st,
        "pool": s,
        "tags": len(POOL),
        "sources": len(REGISTRY),
        "public_base": C.PUBLIC_BASE,
        "pkg_dir": C.PKG_DIR,
        "pkg_ok": os.path.isdir(C.PKG_DIR),
        "notify_enabled": notify.enabled(),
        "notify_target": notify.resolve_target()[3],
        "sites": len(get_config().get("sites", [])),
        "ck": {"version": ckvault.version(),
               "configured": ckvault.summary()["configured"],
               "valid": ckvault.summary()["valid"],
               "autosniff": C.CK_AUTOSNIFF,
               "abs_paths": getattr(C, "ABS_PATHS", True)},
        "probe": probe.stats(),
        "devices": {"count": len(devices.list_devices()),
                    "contributors": len(devices.contributor_ips())},
        "relay": relay.stats(),
        "dav": dav.stats(),
    })


@bp.route("/api/sites")
def api_sites():
    """接口状态明细（站点视角）—— 给机器人插件的「接口状态」命令用。

    与 /api/pool 的区别：/api/pool 是 TAG 视角（28 条，只有带远程地址的），
    这里是站点视角（79 条，和客户端里看到的一一对应），并且带上
    局域网/外网两个接口地址和「是否已打失效标注」。

    查询参数：
      ?status=offline   只看失效的
      ?brief=1          只返回汇总 + 地址，不要明细（消息短）
    """
    only = (request.args.get("status") or "").strip()
    # 用**实际下发的那一份**（get_config 就是渲染后写进 hai 包的内容）算明细，
    # 否则「是否已标注失效」会恒为 0，与实际配置对不上。
    try:
        cur = get_config()
    except Exception:
        cur = None
    rep = report.sites_report(POOL, jsm=cur if cur else None)
    if request.args.get("brief") in ("1", "true", "yes"):
        rep.pop("items", None)
    elif only:
        rep["items"] = [x for x in rep["items"] if x["status"] == only]
    return jsonify(rep)


@bp.route("/api/pool")
def api_pool():
    out = []
    for tag, ent in sorted(POOL.items()):
        det = health.pick_best_detail(ent)
        best = det["url"] if det and (det.get("quality") or 0) >= 2 else None
        if not best and det:
            best = det["url"] + "  (q%d 存疑)" % (det.get("quality") or 0)
        out.append({
            "tag": tag,
            "name": ent.get("name") or (REGISTRY.get(tag, {}) or {}).get("name", ""),
            "api": ent.get("api") or (REGISTRY.get(tag, {}) or {}).get("api", ""),
            "proxy": ent.get("proxy", ""),
            "sites": (REGISTRY.get(tag, {}) or {}).get("sites", []),
            "best": best,
            "alive": sum(1 for d in ent.get("domains", []) if d.get("status") == "alive"),
            "usable": health.usable_count(ent),
            "total": len(ent.get("domains", [])),
            "domains": sorted(ent.get("domains", []),
                              key=lambda d: (-(d.get("quality") or 0),
                                             d.get("status") != "alive",
                                             d.get("latency") or 99999)),
        })
    return jsonify(out)


@bp.route("/api/check", methods=["POST"])
def api_check():
    body = request.get_json(silent=True) or {}
    return jsonify(do_check(only_dead=bool(body.get("only_dead")),
                            tags=body.get("tags")))


@bp.route("/api/sync", methods=["POST"])
def api_sync():
    return jsonify(do_sync())


@bp.route("/api/render", methods=["POST"])
def api_render():
    body = request.get_json(silent=True) or {}
    mode = body.get("mode")
    if mode:
        store.set_state(mode=mode)
    return jsonify(do_render(mode=mode))


@bp.route("/api/repair", methods=["POST", "GET"])
def api_repair():
    """一键检测修复：同步上游 -> 全池巡检 -> 重新生成 hai 包

    默认同步返回（与既有调用方兼容）。带 `background:true` 时改为后台执行并立即
    返回，进度通过 /api/repair/status 轮询 —— SEA 插件用这个方式做「中途过程反馈」。
    """
    body = request.get_json(silent=True) or {}
    reason = body.get("reason", "api")

    if body.get("background") or body.get("bg"):
        with _REPAIR_LOCK:
            if _REPAIR.get("running"):
                return jsonify({"ok": False, "busy": True,
                                "stage": _REPAIR.get("stage"),
                                "step": _REPAIR.get("step"),
                                "total": _REPAIR.get("total")}), 409
            _REPAIR.update({
                "running": True, "stage": "starting", "step": 0, "total": 3,
                "note": "准备中", "reason": reason, "started": int(time.time()),
                "updated": int(time.time()), "finished": 0,
                # 三个成果字段必须全部清空。只清 sync 会导致上一轮的 check 残留，
                # 调用方轮询时会把「上一轮的巡检结果」当成本轮进度报出去。
                "sync": None, "check": None, "result": None, "error": None,
            })
        threading.Thread(target=do_repair_bg, args=(reason,), daemon=True).start()
        return jsonify({"ok": True, "accepted": True, "total": 3})

    return jsonify(do_repair(reason=reason))


@bp.route("/api/repair/status")
def api_repair_status():
    """查询当前（或上一次）修复任务的进度快照"""
    snap = _repair_snapshot()
    # result 只在完成时有值；running 期间不带，避免响应体过大
    if snap.get("running"):
        snap.pop("result", None)
    return jsonify(snap)


@bp.route("/api/daily", methods=["POST"])
def api_daily():
    return jsonify(do_daily())


@bp.route("/api/notify/test", methods=["POST"])
def api_notify_test():
    body = request.get_json(silent=True) or {}
    text = body.get("text") or ("✅ hai本地包 · 通知通道测试\n"
                                f"时间：{time.strftime('%Y-%m-%d %H:%M:%S')}\n"
                                "收到本条即表示 SEA 通知链路正常。")
    ok = notify.send(text, to=body.get("to"))
    return jsonify({"ok": ok, "target": notify.resolve_target()[3]})


@bp.route("/api/mode", methods=["POST"])
def api_mode():
    body = request.get_json(silent=True) or {}
    mode = body.get("mode", "direct")
    if mode not in ("direct", "gateway", "dual"):
        return jsonify({"error": "mode must be direct|gateway|dual"}), 400
    store.set_state(mode=mode)
    do_render(mode=mode)
    return jsonify({"ok": True, "mode": mode})


@bp.route("/api/domains", methods=["POST", "DELETE"])
def api_domains():
    """手工加/删域名
    POST   {tag, url}                  新增候选域名并立即探测
    DELETE {tag, url}                  从池中删除
    """
    body = request.get_json(silent=True) or {}
    tag, url = body.get("tag"), body.get("url")
    if not tag or not url:
        return jsonify({"error": "tag & url required"}), 400
    ent = POOL.setdefault(tag, {"tag": tag, "domains": []})

    if request.method == "DELETE":
        before = len(ent["domains"])
        ent["domains"] = [d for d in ent["domains"] if S.norm_base(d["url"]) != S.norm_base(url)]
        _flush()
        do_render()
        return jsonify({"ok": True, "removed": before - len(ent["domains"])})

    added = S.merge_domains(ent, [url], origin="manual")
    if added:
        st, dt, code, n, final, note, q = health.probe(added[0])
        for d in ent["domains"]:
            if d["url"] == added[0]:
                d.update({"status": st, "latency": dt, "http": code, "bytes": n,
                          "quality": q, "checked": int(time.time()), "note": note})
        store.log_event("manual", f"{tag} 手工新增域名 {added[0]} -> {st}")
    _flush()
    do_render()
    return jsonify({"ok": True, "added": added})


@bp.route("/api/logs")
def api_logs():
    return jsonify(store.tail_events(int(request.args.get("n", 200))))


# ----------------------------------------------------------------------------
# 路由：网盘 CK
# ----------------------------------------------------------------------------


@bp.route("/api/ck")
def api_ck():
    """CK 总览。默认掩码显示；?full=1 返回明文（仅供局域网自查）。"""
    d = ckvault.load()
    s = ckvault.summary()
    if request.args.get("full") == "1" and _is_lan():
        for r in s["rows"]:
            it = d["items"].get(r["channel"])
            if it:
                r["full"] = it.get("fields")
                r["history"] = (it.get("history") or [])[:3]
    ch = request.args.get("ch")
    if ch:
        it = d["items"].get(ch.lower())
        s["item"] = it
    s["autosniff"] = C.CK_AUTOSNIFF
    s["public_ck"] = C.CK_PUBLIC
    s["client_ip"] = _client_ip()
    s["is_lan"] = _is_lan()
    return jsonify(s)


@bp.route("/api/ck/report", methods=["POST"])
def api_ck_report():
    """CK 上报入口（插件 / 脚本 / 捕获台 / 客户端都可调）

    接受：
      {"channel":"quark","value":"__pus=..."}        指定渠道
      {"quark_cookie":"..."}                          整份字段
      {"raw":"<任意文本/URL/Cookie>"}                 自动识别
    """
    if not _check_ck_source():
        return jsonify({"ok": False, "error": "该来源不允许上报 CK"}), 403
    data = request.get_json(silent=True)
    src = f"{_client_ip()} {request.headers.get('User-Agent', '')[:40]}"
    if data is None:
        data = {"raw": request.get_data(as_text=True)}
    if isinstance(data, dict) and data.get("raw"):
        hits = ckvault.extract(str(data["raw"]), source=src + " /raw")
        if not hits:
            return jsonify({"ok": False, "error": "未能识别出网盘 CK"}), 400
        res = [ckvault.report(ch, f, source=s, validate=C.CK_VALIDATE)
               for ch, f, s in hits]
    else:
        res = _apply_ck_payload(data, src)
        if res.get("error"):
            return jsonify(res), 400
    ckvault.write_tokenm()
    return jsonify({"ok": True, "results": res, "version": ckvault.version()})


@bp.route("/api/ck/drop", methods=["POST", "DELETE"])
def api_ck_drop():
    ch = (request.args.get("ch") or (request.get_json(silent=True) or {}).get("channel") or "")
    ok = ckvault.drop(ch)
    return jsonify({"ok": ok, "channel": ch, "version": ckvault.version()})


@bp.route("/api/ck/check", methods=["POST", "GET"])
def api_ck_check():
    ch = request.args.get("ch")
    out = ckvault.recheck(ch)
    ckvault.write_tokenm()
    return jsonify({"ok": True, "results": out, "version": ckvault.version()})


@bp.route("/api/ck/version")
def api_ck_version():
    """轻量轮询：客户端/脚本判断是否需要重新拉 tokenm.json"""
    d = ckvault.load()
    return jsonify({"version": d.get("version") or 0, "updated": d.get("updated") or 0})


# ----------------------------------------------------------------------------
# tokenm 字段下发（代理地址等）
#   【为什么要在中枢配 —— 2026-10-18】tokenm.json 是由中枢渲染下发的（ext.token
#   指向我们的绝对地址），端每 15~30s 拉一次。用户在 App「网盘及弹幕配置」里填的
#   代理地址会被我们下发的值覆盖 →「配了代理却不起作用」。
#   README.txt:49 写明 "proxy" 就是用于科学上网的地址；PG jar 的媒体代理
#   （127.0.0.1:10078）只认它，所以起播（媒体流）与列表（爬虫）走的是两条不同的
#   网络通路 —— 这就是「列表能刷新、起播失败」的根因。
# ----------------------------------------------------------------------------
def _proxy_url_ok(u):
    """代理地址校验：允许 http/https/socks4/socks5，带可选 user:pass@"""
    u = (u or "").strip()
    if not u:
        return True                      # 空 = 清除
    low = u.lower()
    if not any(low.startswith(s) for s in ("http://", "https://", "socks4://", "socks5://")):
        return False
    rest = u.split("://", 1)[1]
    if "/" in rest:                      # 只允许 host:port，不接受路径
        rest = rest.split("/", 1)[0]
    host = rest.split("@")[-1]
    if ":" not in host:
        return False
    h, p = host.rsplit(":", 1)
    return bool(h) and p.isdigit() and 0 < int(p) < 65536


@bp.route("/api/settings", methods=["GET", "POST"])
def api_settings():
    """GET 取当前覆盖项；POST {proxy: "http://host:port"} 写入并重渲染 tokenm"""
    if request.method == "GET":
        ov = ckvault.tokenm_overrides()
        proxy = str(ov.get("proxy") or "")
        return jsonify({"ok": True, "proxy": proxy,
                        "youtube_proxy": ckvault.render(lan=True).get("youtube_proxy"),
                        "overrides": {k: v for k, v in ov.items()}})

    body = request.get_json(silent=True) or {}
    patch = {}
    if "proxy" in body:
        v = str(body.get("proxy") or "").strip()
        if not _proxy_url_ok(v):
            return jsonify({"ok": False,
                            "error": "代理地址格式不对，应为 scheme://host:port，"
                                     "scheme 取 http/https/socks4/socks5"}), 400
        patch["proxy"] = v
    for k in ("youtube_proxy", "pikpak_proxy", "tgsearch_api_proxy"):
        if k in body:
            v = str(body.get(k) or "").strip()
            if v and v not in ("proxy", "noproxy"):
                return jsonify({"ok": False,
                                "error": f"{k} 只能填 proxy（走代理）或 noproxy（不走）"}), 400
            patch[k] = v
    if not patch:
        return jsonify({"ok": False, "error": "没有可写入的字段"}), 400

    cur, path = ckvault.set_tokenm_overrides(patch)
    store.log_event("ck", f"tokenm 字段已更新：{ {k: v for k, v in patch.items()} }"
                          f" → {path or '未落盘'}")
    return jsonify({"ok": True, "path": path, "overrides": cur})


@bp.route("/api/devices")
def api_devices():
    """客户端台账：谁连过这个包、有没有把 CK 送回来过"""
    devs = devices.list_devices()
    return jsonify({
        "count": len(devs),
        "lan": sum(1 for d in devs if d.get("lan")),
        "contributors": [d for d in devs if int(d.get("ck_in") or 0) > 0],
        "devices": devs,
    })


@bp.route("/api/probe")
def api_probe():
    """写入探针：端到底有没有把 CK 送回来

    ?write=1   只看非 GET 的请求
    ?path=xxx  只看路径里含 xxx 的
    ?n=100     条数
    """
    n = int(request.args.get("n", 100))
    rows = probe.tail(n,
                      only_write=request.args.get("write") in ("1", "true", "yes"),
                      path_contains=request.args.get("path"))
    return jsonify({"stats": probe.stats(), "rows": rows})


@bp.route("/api/probe/reset", methods=["POST"])
@bp.route("/api/devices/reset", methods=["POST"])
def api_observe_reset():
    """清空探针记录 + 客户端台账，便于「干净地做一次真机测试」。

    只清证据（probe.jsonl / devices.json），不动 data/ck.json 里已入库的凭据
    —— 凭据是资产，要清请另调 /api/ck/drop?ch=xxx。
    """
    pr = probe.reset()
    dv = devices.reset()
    store.log_event("info", "观测数据已清零：探针 %s 条、台账 %s 台" %
                    (pr.get("cleared"), dv.get("cleared")))
    return jsonify({"ok": True, "probe": pr, "devices": dv})


@bp.route("/api/relay", methods=["GET", "POST"])
def api_relay():
    """外部中转：把端直连第三方的凭据换取请求引到中枢，旁路采集。

    背景：PG 的 tokenm.json 里 `open_api_url` 指向网盘换 token 的接口，
    端扫码后的凭据就 POST 到那里 —— 直连第三方，中枢看不见。把它改写到
    `/gw/ali/token` 即可透传 + 采集。

    GET               查看中转状态与最近记录（?n=100&hit=1）
    POST {"rewrite"}  开关「把 tokenm 外发地址改写到中枢」（改完端下一轮拉取即生效）
    POST {"key","enabled"}  开关某条路由
    """
    if request.method == "POST":
        d = request.get_json(silent=True) or {}
        if "rewrite" in d:
            relay.set_rewrite(bool(d["rewrite"]))
            store.log_event("relay", "外部中转改写已%s"
                                     % ("开启" if relay.rewrite_enabled() else "关闭"))
        if d.get("key"):
            relay.set_route_enabled(str(d["key"]), bool(d.get("enabled", True)))
        return jsonify({"ok": True, "stats": relay.stats(),
                        "routes": relay.routes()})

    n = min(int(request.args.get("n", 100)), 500)
    rows = relay.recent(n)
    if request.args.get("hit") in ("1", "true", "yes"):
        rows = [r for r in rows if r.get("hit")]
    return jsonify({"stats": relay.stats(), "routes": relay.routes(),
                    "rows": rows})


@bp.route("/api/dav")
def api_dav():
    """内置 WebDAV 端点状态：端有没有把凭据备份上来"""
    n = min(int(request.args.get("n", 100)), 500)
    return jsonify({"stats": dav.stats(), "files": dav.list_files(),
                    "rows": dav.recent(n)})


@bp.route("/ck")
def ck_page():
    """局域网 CK 捕获台（手机浏览器打开即可粘贴保存）"""
    p = os.path.join(C.BASE_DIR, "web", "ck.html")
    if not os.path.exists(p):
        return "capture page missing", 404
    with open(p, encoding="utf-8") as f:
        return Response(f.read(), mimetype="text/html; charset=utf-8")


@bp.route("/live")
def live_page():
    """CK 同步监视台（手机浏览器打开，边操作 OK影视 边看请求有没有回来）"""
    p = os.path.join(C.BASE_DIR, "web", "live.html")
    if not os.path.exists(p):
        return "live page missing", 404
    with open(p, encoding="utf-8") as f:
        return Response(f.read(), mimetype="text/html; charset=utf-8")


@bp.route("/api/libcheck")
def api_libcheck():
    """hai 包 lib 资源体检

    missing  —— 作者 README 要求使用者自备、但我们还没落地的（真正的待办）
    upstream —— 被配置引用、但上游发行版本来就没有的（404 属正常，jar 会自行回退）
    """
    need = libfill.needed_files()
    miss = libfill.missing()
    derive = set(libfill.DERIVE)
    return jsonify({
        "missing": sorted(m for m in miss if m in derive),
        "upstream": sorted(m for m in miss if m not in derive),
        "needed": sorted(need),
        "pkg_dir": C.PKG_DIR,
    })


@bp.route("/api/libfill", methods=["POST"])
def api_libfill():
    return jsonify(libfill.fill())


@bp.route("/api/verify")
def api_verify():
    """与原版 pg 包逐项比对：证明没有精简直播源 / 站点 / 字段 / 文件

    ?deep=1 额外比对每个资源文件的 sha256（默认只比大小）
    """
    deep = request.args.get("deep") in ("1", "true", "yes")
    return jsonify(verify.run(deep=deep))


@bp.route("/api/access")
def api_access():
    """最近的 HTTP 访问明细（含 UA），用于排查播放器取配置失败"""
    n = int(request.args.get("n", 100))
    only = request.args.get("path")
    out = []
    try:
        with open(F_ACCESS, encoding="utf-8") as f:
            lines = f.readlines()[-2000:]
        for ln in lines:
            try:
                r = json.loads(ln)
            except Exception:
                continue
            if only and only not in r.get("p", ""):
                continue
            out.append(r)
    except FileNotFoundError:
        pass
    return jsonify(out[-n:])


@bp.route("/api/preview")
def api_preview():
    jsm = get_config()
    key = request.args.get("key")
    for s in jsm.get("sites", []):
        if s.get("key") == key:
            return jsonify(s)
    return jsonify({"error": "not found"}), 404


# ----------------------------------------------------------------------------
# 蓝图注册：同时挂载在根路径与反向代理子路径下（如 /oktv），无需中间件
# ----------------------------------------------------------------------------
app.register_blueprint(bp)
if C.PREFIX:
    app.register_blueprint(bp, url_prefix=C.PREFIX, name="oktv_prefixed")


# ----------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------


def main():
    bootstrap()
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    if cmd == "check":
        print(json.dumps(do_check(), ensure_ascii=False, indent=2)[:4000])
    elif cmd == "sync":
        print(json.dumps(do_sync(), ensure_ascii=False, indent=2)[:4000])
    elif cmd == "render":
        print(json.dumps(do_render(), ensure_ascii=False, indent=2)[:4000])
    elif cmd == "repair":
        print(json.dumps(do_repair(reason="cli"), ensure_ascii=False, indent=2)[:4000])
    elif cmd == "daily":
        print(json.dumps(do_daily(), ensure_ascii=False, indent=2)[:4000])
    elif cmd == "notify-test":
        print("通知已发送" if notify.send("✅ hai本地包 · 通知通道测试（CLI）") else "通知发送失败")
    else:
        t = threading.Thread(target=scheduler, daemon=True)
        t.start()
        store.log_event("boot", f"HTTP 服务监听 {C.HOST}:{C.PORT}")
        _serve()


def _serve():
    """优先用 waitress 提供符合 HTTP/1.1 的服务。

    Flask 自带的 dev server 每个响应都回 `Connection: close`，
    部分电视机播放器/老版 HttpClient 在这种连接上会读不到响应体，
    表现出来就是「配置获取失败」。waitress 支持 keep-alive，兼容性稳得多。
    """
    try:
        from waitress import serve
        store.log_event("boot", "使用 waitress 生产级 WSGI（HTTP/1.1 keep-alive）")
        serve(app, host=C.HOST, port=C.PORT, threads=32,
              channel_timeout=180, ident="oktv-hub", clear_untrusted_proxy_headers=False)
    except ImportError:
        store.log_event("boot", "waitress 未安装，回落 Flask dev server")
        app.run(host=C.HOST, port=C.PORT, threaded=True, debug=False)


if __name__ == "__main__":
    main()
