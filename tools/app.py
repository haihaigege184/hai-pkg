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
import gzip as _gzip
import base64
import hashlib
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import concurrent.futures

from flask import (Blueprint, Flask, Response, g, jsonify, redirect, request,
                   send_from_directory)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core import config as C          # noqa: E402
from core import (absbase, addsite, checkin_ck, ckvault, dav, devices, endpoints, epbench,  # noqa: E402
                  gateway, health, libfill, navwatch, notify, patch, pkgupdate, probe,
                  relay, renderbase, report, siteorder, sources as S, store, sync,
                  tmdbwall, verify, posterweb, warm)

app = Flask(__name__, static_folder=None)
bp = Blueprint("oktv", __name__)

F_ACCESS = os.path.join(C.LOG_DIR, "access.jsonl")

# TMDB 图床代理（见 /hai/img 路由）
_IMG_HOST = "https://images.tmdb.org"
_IMG_DIR = os.path.join(C.DATA_DIR, "imgcache")
_TMDB_IMG = "https://images.tmdb.org"

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


@app.before_request
def _mark_start():
    g._t0 = time.time()


# 可以压缩的响应类型。json 是主力（jsm.json 42KB → 约 8KB），
# 其次是 lib/*.json 与 js/*.js（douban.json 22KB、youtube.json 39KB、drpy2.min.js 500KB）。
_GZIP_MIMES = ("application/json", "text/", "application/javascript",
               "application/x-javascript", "application/xml", "image/svg")
_GZIP_MIN = 900            # 小响应压了反而更大，直接放过
_GZIP_MAX = 3 * 1024 * 1024  # 超过 3MB 不做（pg.jar 之类本来就不该压）


@app.after_request
def _gzip_resp(resp):
    """按需 gzip —— 客户端明明在请求头里声明了 gzip，我们却一直发原文。

    【为什么值得做 —— 2026-10-22 实测】
    电视端 795 次请求里 795 次带 `Accept-Encoding: gzip`，服务端**一次都没压**。
    jsm.json 42.5KB 原文、tokenm.json 2.9KB × 每次启动 8 次、lib/*.json 最大 39KB。
    走 CF/FRP 出口时这些都是纯等待时间；压完体积降到 ~20%。
    """
    try:
        if resp.status_code != 200 or resp.headers.get("Content-Encoding"):
            return resp
        if "gzip" not in (request.headers.get("Accept-Encoding") or "").lower():
            return resp
        if request.headers.get("Range"):
            return resp
        mt = (resp.mimetype or "").lower()
        if not any(mt.startswith(t) or t in mt for t in _GZIP_MIMES):
            return resp
        # send_file 的响应默认 direct_passthrough（流式），要先落地成 bytes 才能压。
        if resp.direct_passthrough:
            try:
                clen = int(resp.headers.get("Content-Length") or 0)
            except Exception:
                clen = 0
            if clen > _GZIP_MAX:
                return resp
            resp.direct_passthrough = False
        data = resp.get_data()
        if len(data) < _GZIP_MIN or len(data) > _GZIP_MAX:
            return resp
        cz = _gzip.compress(data, 5)
        if len(cz) >= len(data):
            return resp
        resp.set_data(cz)
        resp.headers["Content-Encoding"] = "gzip"
        resp.headers["Content-Length"] = str(len(cz))
        resp.headers.add("Vary", "Accept-Encoding")
    except Exception:
        pass
    return resp


@app.after_request
def _access_log(resp):
    """把每个请求的「来源 IP / UA / 关键头 / 耗时」记下来。

    电视机「配置获取失败」这类问题，光看服务端 200 是分不清
    「客户端没连上」和「客户端拿到但解析失败」的——这条日志能直接分辨。
    `ms` 是 2026-10-22 补的：复盘「打开很慢」时必须知道**哪一个请求**慢，
    没有耗时就只能靠猜。
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
        try:
            ms = int((time.time() - getattr(g, "_t0", time.time())) * 1000)
        except Exception:
            ms = -1
        rec = {
            "t": time.strftime("%Y-%m-%d %H:%M:%S"),
            "ip": _client_ip(),
            "m": request.method, "p": request.path,
            "q": request.query_string.decode("utf-8", "ignore")[:1000],
            "s": resp.status_code,
            "ms": ms,
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
# 接口顺序/显隐的编辑锁：move 需要「读当前顺序 → 改 → 重渲染」原子完成，
# 否则连点两下箭头会各自基于旧顺序计算，第二次把第一次的移动吞掉。
_SITEORDER_LOCK = threading.RLock()
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
    # 用户主动加入的新站源：并入域名池（独立 TAG），参与巡检与自动修复
    _added = len(addsite.added_keys())
    _orph = addsite.prune_orphan_pool(POOL)
    if _added:
        store.log_event("boot", "已加入的新站源 %d 个，并入域名池" % _added)
    if _orph:
        store.log_event("boot", "清理域名池里的孤儿新增条目 %d 个" % len(_orph))
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
    # 手动同步也要留档：维护页「上游源」读的是 state，只存 last_daily_stat
    # 的话，点「上游同步」之后页面还是显示上一次巡检的数字（甚至空的）。
    try:
        store.set_state(last_sync_stat=stat, last_sync=int(time.time()))
    except Exception:
        pass
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
                 "harvested": sync_stat.get("harvested", 0),
                 "pkg_added": sync_stat.get("pkg_added", 0),
                 "detail": sync_stat.get("source_detail", [])},
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
            "pkg_added": sync_stat.get("pkg_added", 0),
            "detail": sync_stat.get("source_detail", []),
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
             "pkg_added": stat["sync"].get("pkg_added", 0),
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
    """取当前配置。

    【2026-10-22 改为 stale-while-revalidate】
    原来是「超过 60s 就在**这次请求里**重新渲染」，实测首次渲染 378ms ——
    设备空闲超过 60s 后必然吃这一下；走 CF/FRP 时还要叠加隧道 RTT，
    表现为「刚打开 App 那一下特别慢」。
    现在渲染交给后台线程（_render_keeper），请求路径只读内存里的成品：
    哪怕它比 60s 旧一点，也远好过让客户端干等 378ms。
    """
    if force or not _last_render["jsm"]:
        do_render()
    return _last_render["jsm"]


def _render_keeper():
    """后台保持配置常新：只要有成品，请求路径就再也不渲染。

    每 45s 检查一次（比原来 60s 的过期窗口更密），渲染成本 0.38s，
    折算 CPU 占用 <1%，换来设备侧「零等待」。
    """
    time.sleep(3)
    while True:
        try:
            if time.time() - _last_render["ts"] >= 45:
                do_render()
        except Exception as e:
            store.log_event("error", f"后台重渲染失败: {type(e).__name__}: {e}")
        time.sleep(5)


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
            if now - st.get("last_epbench", 0) >= C.EPB_INTERVAL:
                do_epbench()
            if _due_daily():
                do_daily()
        except Exception as e:
            store.log_event("error", f"调度异常: {type(e).__name__}: {e}")
        time.sleep(60)


def do_epbench(reason="定时"):
    """定时测速入口地址，并让渲染口径按当前档位决定要不要换。

    刻意**串行**（epbench.run 内部就是串的）：并发会自己把出口挤爆，
    测出来的全是假延迟 —— 与 health.check_all 的教训同源。
    """
    try:
        bench = epbench.run()
        dec = renderbase.decide(bench)
        store.set_state(last_epbench=int(time.time()))
        rows = [x for x in (bench.get("rows") or []) if x.get("ok")]
        if rows:
            store.log_event("endpoints",
                            "入口测速（%s）：%s" % (
                                reason, "；".join(
                                    "%s %sms/%sms" % (x.get("label") or x.get("kind"),
                                                      x.get("jsm_ms"), x.get("big_ms"))
                                    for x in rows)))
        if dec.get("changed"):
            store.log_event("render", "渲染口径已切换：%s" % dec.get("reason"))
            do_render()      # 口径变了，重渲染让下一次下发就带上
        return {"ok": True, "bench": bench, "decide": dec}
    except Exception as e:
        store.log_event("error", f"入口测速失败: {type(e).__name__}: {e}")
        return {"ok": False, "error": str(e)}


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
        base, pfx = absbase.site_base(req, renderbase.current() or "")
        if base:
            suffix = base
            jsm = absbase.absolutize(jsm, base, prefix=pfx)
    # ---- spider（jar）必须给电视**本地路径** ----
    # 2026-11-02 00:xx 定位：`./pg.jar` 被绝对化成 `http://10.0.0.11:8099/hai/pg.jar`，
    # app 会去下载（access log 能看到 206 分段），但**下载完不落地**
    #（/data/data/<pkg>/cache 和 files 都是空的，全盘也找不到新 jar），
    # 于是所有 csp_* 站点在 DexClassLoader 里找不到类 → `site=Wogg` 直接
    # NullPointerException → 界面「找不到数据」。
    # 原版能播，是因为它的接口配的就是端上已有的本地 jar。
    # 这里改成端上实实在在存在的那份（用户机器上 /sdcard/pg/pg.jar）。
    jsm["spider"] = "/data/data/com.hisen.android.tv/files/pg.jar"
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
        "config": (f"{renderbase.current()}/hai/jsm.json"
                    if renderbase.current() else "/hai/jsm.json"),
        "jar": (f"{renderbase.current()}/hai/pg.jar"
                 if renderbase.current() else "/hai/pg.jar"),
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


@bp.route("/hai/poster/")
def hai_poster():
    """豪华海报墙网页（服务端渲染的暗色页面，电视浏览器/手机都能开）

    筛选状态走 URL：t=类目 c=地区 s=排序 g=类型，服务端直接按数据源重排 ——
    电视 WebView 老，前端过滤+翻页最容易断层，这样最稳。
    `src=tmdb|douban` 指定数据源；不传就用服务端记住的那个（遥控设置键切换）。
    """
    src = str(request.args.get("src") or "").strip()
    src = src if src in ("tmdb", "douban") else posterweb.load_src()
    # 类目：TMDB 是数字 id，豆瓣是 `db2` 这种字符串 —— 原样往下传，
    # page_home 内部各自校验，非法值会退到该数据源的默认类目。
    # 数字部分两套数据是对齐的（2=热门电影 … 11=即将上映），所以切源后
    # 同一个位置还是同一类。
    t = request.args.get("t")
    if t in (None, ""):
        t = "db2" if src == "douban" else 2
    try:
        pg = int(request.args.get("pg") or 1)
    except ValueError:
        pg = 1
    return Response(
        posterweb.page_home(t, pg,
                            str(request.args.get("c") or ""),
                            str(request.args.get("s") or ""),
                            str(request.args.get("g") or ""),
                            src, bool(request.args.get("sm"))),
                    mimetype="text/html; charset=utf-8",
                    # 缓存 90 秒：电视上「返回海报墙」不再重新请求重新渲染
                    #（海哥 2026-11-02：不用每次都刷新）。类目/源变了 URL 也变，
                    # 所以不会串页。
                    headers={"Cache-Control": "private, max-age=90"})


@bp.route("/hai/poster/src")
def hai_poster_src():
    """切换海报墙数据源（TMDB / 豆瓣）。

    电视遥控「设置」键呼出的浮层就走这里。切换是**全局**的（写
    data/poster.src.json），因为电视、手机、公网入口看到的应该是同一套数据。
    切完 302 回原来那一页，并把 src 带到 query 里 —— WebView 里少一次
    额外跳转，用户感觉就是「按一下换个库」。
    """
    to = str(request.args.get("to") or "").strip()
    back = str(request.args.get("back") or "/hai/poster/").strip()
    if not back.startswith("/hai/"):
        back = "/hai/poster/"
    if to in ("tmdb", "douban"):
        posterweb.save_src(to)
        # 切源后回到该源的首页（类目 id 体系不同，t=2 在豆瓣叫 db2）
        back = ("/hai/poster/?src=douban&t=db2" if to == "douban"
                else "/hai/poster/?src=tmdb&t=2")
    return Response(status=302, headers={"Location": back,
                                         "Cache-Control": "no-cache"})


@bp.route("/hai/poster/p/<pid>")
def hai_poster_person(pid):
    """影人页（详情页点头像进来）。

    pid 前缀决定数据源：`db<cid>` 豆瓣影人 / `tp<id>` TMDB 影人。
    页面自己画，**不跳 app 原生搜索页**（海哥 11-01 要求）。
    """
    return Response(posterweb.page_person(pid, bool(request.args.get("sm"))),
                    mimetype="text/html; charset=utf-8",
                    headers={"Cache-Control": "no-cache"})


@bp.route("/hai/poster/q")
def hai_poster_search():
    """搜索结果页（详情页「🔍 搜索」进来）。豆瓣 + TMDB 两段结果，自绘海报卡。"""
    kw = str(request.args.get("kw") or "").strip()
    return Response(posterweb.page_search(kw, bool(request.args.get("sm"))),
                    mimetype="text/html; charset=utf-8",
                    headers={"Cache-Control": "no-cache"})


@bp.route("/hai/poster/cast")
def hai_poster_cast():
    """详情页演职员补全口（兜底）。

    2026-11-01 起豆瓣详情本身就带头像和影人 id 了（走 credits 接口，
    见 core/doubanx.credits），首屏就有 —— 这个口只在 credits 临时失败、
    页面退回「名字首字母」占位时才被 JS 调到。

    注意头像必须走站内代理（_pic_raw）：credits 给的 profile 是
    `https://img*.doubanio.com/view/personage/...` 原始地址，电视直连
    **一律 418**（防盗链只认 Referer）—— 原样返回就是一圈黑头像。
    """
    vid = str(request.args.get("vid") or "").strip()
    if not vid.startswith("db"):
        return jsonify(ok=0, people=[])
    sid = vid[2:]
    try:
        from core import doubanx as D
        from core.posterweb import _pic_raw
        for kind in ("tv", "movie"):
            cr = D.credits(sid, kind)
            if not cr.get("cast"):
                continue
            people = [{"name": c.get("name"), "cid": c.get("cid"),
                       "avatar": _pic_raw(c.get("profile") or "")}
                      for c in (cr.get("cast") or []) + (cr.get("crew") or [])]
            return jsonify(ok=1, people=people)
    except Exception as e:
        return jsonify(ok=0, error="%s: %s" % (type(e).__name__, e), people=[])
    return jsonify(ok=0, people=[])


# /hai/poster/d/__probe 在电视上验证「本地取链」是否可用（不经过服务器算直链）
_PROBE_HTML = """<!doctype html><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<body style="background:#0b0b0b;color:#7CFC00;font:15px/1.45 monospace;padding:18px;white-space:pre-wrap">
<script src="/hai/lib/haiplay.js?v=%JSVER%"></script>
<script>
(function () {
  var out = [];
  function log(s) { out.push(String(s)); document.body.textContent = out.join('@@NL@@'); }
  function q(k) { var m = location.search.match(new RegExp('[?&]' + k + '=([^&]*)')); return m ? decodeURIComponent(m[1]) : ''; }
  log('=== 海哥影视 本地取链自检 ===');
  log('bridge: ' + (typeof hai));
  if (typeof hai === 'undefined') { log('FAIL 没有 hai bridge（APK 未注入 HaiJs）'); return; }
  try { log('quark ck len: ' + ((hai.ck('quark') || '').length)); } catch (e) { log('ck err: ' + e); }
  try { log('baidu ck len: ' + ((hai.ck('baidu') || '').length)); } catch (e) { log('ck err: ' + e); }

  // 1) 先测 bridge 能不能发请求
  hai && setTimeout(function () {}, 0);
  var r0 = hai.http('GET', 'http://10.0.0.11:8099/hai/lib/haiplay.js', '{}', '');
  try { var j0 = JSON.parse(r0); log('http 自测: code=' + j0.code + ' len=' + (j0.body || '').length); }
  catch (e) { log('http 自测失败: ' + String(r0).slice(0, 120)); }

  var share = q('share') || 'https://pan.quark.cn/s/90729cfcaaac';
  var ep = parseInt(q('ep') || '1', 10);
  log('share: ' + share + '  ep=' + ep);

  // 2) 夸克 token 原始响应（排查用）
  try {
    var sid = (share.match(/[/]s[/]([0-9a-zA-Z]+)/) || [])[1] || '';
    var hd2 = {
      'User-Agent': ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
        + '(KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36'),
      'Referer': 'https://pan.quark.cn/',
      'Accept': 'application/json',
      'Content-Type': 'application/json',
      'Cookie': hai.ck('quark') || ''
    };
    var rq = hai.http('POST',
      'https://drive-pc.quark.cn/1/clouddrive/share/sharepage/token?pr=ucpro&fr=pc',
      JSON.stringify(hd2), JSON.stringify({ pwd_id: sid, passcode: '' }));
    log('token raw: ' + String(rq).slice(0, 420));
  } catch (e2) { log('token err: ' + e2); }

  log('...取链中（最多 40 秒）');
  HaiPlay.ep({ share: share, ep: ep, title: q('title') || '' }).then(function (r) {
    log('result: ' + JSON.stringify(r).slice(0, 900));
    if (r && r.ok) {
      var h = HaiPlay.href(r, ep, r.name || '');
      log('href: ' + h.slice(0, 260));
      log('>>> 5 秒后自动起播');
      setTimeout(function () { location.href = h; }, 5000);
    } else {
      log('FAIL 取链失败');
    }
  }).catch(function (e) { log('EXCEPTION: ' + e); });
})();
</script>
"""


@bp.route("/hai/dbg")
def hai_dbg():
    """页面 JS 的诊断落点（电视 WebView 里画不出来，只能这样取证）。"""
    m = (request.args.get("m") or "")[:200]
    try:
        with open(os.path.join(C.DATA_DIR, "dbg.log"), "a", encoding="utf-8") as f:
            f.write("%s\t%s\t%s\n" % (time.strftime("%H:%M:%S"), _client_ip(), m))
    except Exception:
        pass
    return Response("ok", mimetype="text/plain")


@bp.route("/hai/poster/d/<vid>")
def hai_poster_detail(vid):
    if str(vid) == "__probe":
        _v = "0"
        try:
            _v = str(int(os.path.getmtime(os.path.join(
                C.BASE_DIR if hasattr(C, "BASE_DIR") else ".",
                "assets", "lib", "haiplay.js"))))
        except Exception:
            pass
        return Response(_PROBE_HTML.replace("@@NL@@", chr(10)).replace("%JSVER%", _v),
                        mimetype="text/html")
    try:
        r = int(request.args.get("r") or 0)
    except ValueError:
        r = 0
    return Response(posterweb.page_detail(vid, r, bool(request.args.get("sm"))),
                    mimetype="text/html; charset=utf-8",
                    headers={"Cache-Control": "no-cache"})


@bp.route("/hai/poster/cfg", methods=["GET", "POST"])
def hai_poster_cfg():
    """海报墙配置：维护页「海报墙配置」卡的读写口。

    变量：rotate(轮播开关) / interval(换图秒数) / effect(转场) / imgs(图数)。
    存 data/poster.cfg.json；详情页每次渲染实时读，改完即生效。
    """
    if request.method == "POST":
        data = request.get_json(force=True, silent=True) or {}
        cfg = posterweb.save_cfg(data)
    else:
        cfg = posterweb.load_cfg()
    return jsonify(ok=True, cfg=cfg)


# ---------------------------------------------------------------------------
# 播放解析：进度广播 + 结果缓存（2026-11-03）
# ---------------------------------------------------------------------------
# 起因（海哥）：*「等待播放的遮罩界面能否显示状态信息，比如加载状态、片源信息、
# 缓冲」+「加快起播速度」*。
#
# 两条都不改播放链路，只加两样东西：
#   1. `_PLAY_PROG`：解析过程中逐段写状态（查找站源 → 解析第 N 集 → 就绪），
#      网页遮罩每 400ms 拉一次 `/hai/poster/progress`，把「在干什么」显示出来；
#   2. `_PLAY_CACHE`：算好的 href 缓存 8 分钟。详情页一打开就先预热续播那一集，
#      用户真按「▶ 播放」时直接命中，省掉 3~15 秒。
# 缓存键带集号：同一部剧换集必须重算，否则会播错集。
_PLAY_PROG = {}
_PLAY_CACHE = {}
_PLAY_LOCK = threading.Lock()
_PLAY_TTL = 8 * 60


def _pl_key(vid, ep):
    try:
        return (str(vid or ""), int(ep or 0))
    except (TypeError, ValueError):
        return (str(vid or ""), 0)


def _pg(vid, ep, stage, text, **kw):
    """写一条进度（视频解码前的「在干什么」，给遮罩显示）。"""
    d = {"stage": stage, "text": text, "ts": time.time()}
    d.update(kw)
    with _PLAY_LOCK:
        _PLAY_PROG[_pl_key(vid, ep)] = d
        if len(_PLAY_PROG) > 300:
            for k in list(_PLAY_PROG)[:100]:
                _PLAY_PROG.pop(k, None)


def _pl_get(vid, ep):
    with _PLAY_LOCK:
        v = _PLAY_CACHE.get(_pl_key(vid, ep))
    if not v or time.time() - v.get("ts", 0) > _PLAY_TTL:
        return None
    return v.get("data")


def _pl_put(vid, ep, data):
    with _PLAY_LOCK:
        _PLAY_CACHE[_pl_key(vid, ep)] = {"ts": time.time(), "data": data}
        if len(_PLAY_CACHE) > 200:
            for k in list(_PLAY_CACHE)[:80]:
                _PLAY_CACHE.pop(k, None)


_PAN_CN = {"quark": "夸克", "uc": "UC", "baidu": "百度", "ali": "阿里"}


def _fmt_size(n) -> str:
    try:
        n = int(n or 0)
    except (TypeError, ValueError):
        n = 0
    if n >= 1073741824:
        return "%.1f GB" % (n / 1073741824.0)
    if n > 0:
        return "%d MB" % max(1, round(n / 1048576.0))
    return "大小未知"


def _link_kbps(url, hdr, nbytes=128 * 1024, timeout=4.0) -> int:
    """量这条直链的实际速度（KB/s），0 表示探测失败。

    为什么要量：百度非会员只有 ~100 KB/s 的预览通道，照样能签出「合法」直链，
    交给播放器就是转圈到 3003。与其让它撞墙，不如先量一下 —— 慢的候选排到
    后面去，有快的就先播快的（多花 0.1~1 秒，省掉一次失败）。
    """
    h = dict(hdr or {})
    h["Range"] = "bytes=0-%d" % max(0, int(nbytes) - 1)
    t0 = time.time()
    try:
        with urllib.request.urlopen(
                urllib.request.Request(url, headers=h), timeout=timeout) as f:
            b = f.read(int(nbytes))
        dt = max(0.05, time.time() - t0)
        return int(len(b) / 1024.0 / dt)
    except Exception:
        return 0


@bp.route("/hai/poster/progress")
def hai_poster_progress():
    """播放解析进度（给起播遮罩显示「在干什么」）。

    详情页点「▶ 播放」后遮罩先铺上，然后每 400ms 拉一次这里：
      eps      正在汇总站源/选集
      resolve  正在解析第 N 集（夸克/百度…）
      slow     某条链太慢，换下一条
      probe    取到直链，正在量速度
      ready    已就绪，马上跳播放器
    """
    vid = str(request.args.get("vid") or "").strip()
    try:
        ep = int(request.args.get("ep") or 0)
    except ValueError:
        ep = 0
    with _PLAY_LOCK:
        d = dict(_PLAY_PROG.get(_pl_key(vid, ep)) or {})
    if not d:
        return jsonify(stage="", text="", age=0)
    d["age"] = round(time.time() - d.get("ts", time.time()), 1)
    return jsonify(d)


@bp.route("/hai/poster/warm")
def hai_poster_warm():
    """焦点预热：光标停在哪张卡上，就提前把它的选集聚合排进后台。

    绝不批量预扫（一部 20~40 秒，7 类 × 20 部要一小时，还会把采集站打到
    限流）。这里只排「用户下一眼可能点开的那一部」，串行、最多排 6 个。
    """
    vid = str(request.args.get("vid") or "").strip()
    title = str(request.args.get("name") or "").strip()
    ok = warm.warm_vid(vid, title)
    return jsonify(ok=1 if ok else 0, queued=int(ok), stat=warm.stats())


@bp.route("/hai/poster/warm/stat")
def hai_poster_warm_stat():
    return jsonify(warm.stats())


@bp.route("/hai/poster/play")
def hai_poster_play():
    """「▶ 播放」直达站源解析口（2026-10-30 需求 / 10-31 重写）

    详情页打开后由 JS 后台 fetch（不阻塞首屏），按**站源顺序**（玩偶打头）
    逐个站搜「完全同名」的资源，命中就把播放按钮的 href 换成
    `haiplay://play?site=&id=&name=&pic=` —— app 拦截后直达该站选集页。
    全部站源都没有完全同名 → ok=0，按钮保持 `haiplay://search?q=` 兜底。

    调试：加 &debug=1 会额外返回每站的候选片名，方便对「为什么没匹配上」取证。
    """
    vid = str(request.args.get("vid") or "").strip()
    if not vid:
        return jsonify(ok=0, error="缺少 vid")
    p = {}                              # 选集聚合结果（各分支按需覆盖）
    d = tmdbwall.detail(vid, with_plays=False)
    if not d:
        return jsonify(ok=0, error="未找到影片")
    title = d.get("vod_name") or ""
    year = d.get("vod_year") or ""
    # pic 必须是**绝对地址**：豆瓣条目这里是 /hai/dbimg/... 这种相对路径，
    # 直接塞进 haiplay:// 交给播放器会触发 app 侧 Objects.requireNonNull 的
    # NPE（真机 logcat 实锤），表现为「找不到数据」。
    _pic_u = str(d.get("vod_pic") or "")
    if _pic_u.startswith("/"):
        _pic_u = _ext_base() + _pic_u

    # ---- ⓪⓪ 夸克直链：**连文件列表都不要**（2026-11-01 新链路）------------
    #
    # 链路：分享链接 + 集号 → 服务端 token/detail 递归扫目录 → 挑第 N 集
    #   → 转存到自己网盘（台账去重，同集只转一次）→ file/download 取链
    #   → 塞进 /hai/pp/<token>（带 Cookie 头）→ ExoPlayer 播我们、我们代拉。
    #
    # 关键两个坑（都已实证）：
    #   ① `file/download` 必须用**夸克客户端 UA**，浏览器 UA 恒报 23018
    #      「download file size limit」，跟文件大小无关；
    #   ② CDN 直链只带 UA 会 412，必须 UA+Referer+Cookie 三件套 —— 播放器
    #      补不上，所以只能由服务端当中转（`_stream`）。
    if request.args.get("direct"):
        try:
            want = int(request.args.get("ep") or 0)
        except ValueError:
            want = 0
        # 详情页预热过的结果直接用（&fresh=1 可强制重算，调试用）
        if not request.args.get("fresh"):
            _hit = _pl_get(vid, want)
            if _hit:
                _d = dict(_hit)
                _d["cached"] = 1
                _pg(vid, want, "ready", "已就绪（缓存命中），开始播放",
                    pan=_d.get("pan") or "", file=_d.get("file") or "",
                    size=_d.get("size") or 0)
                return jsonify(**_d)
        _pg(vid, want, "eps", "正在汇总站源与选集…")
        try:
            p = _eps_payload(vid)
        except Exception:
            p = {}
        # 真机联调：&site=tmdb 强制走自建 spider，&site=push 走内建
        _sd = (request.args.get("site") or "").strip()
        site_dir = {"tmdb": "TMDBWall", "push": "push_agent"}.get(_sd, _SITE_DIRECT)
        from core import panck as _pk

        def _collect(_p):
            """从聚合结果里挑出这一集 + 所有能算直链的分享（夸克/UC）。"""
            _flat = []
            for sea in (_p.get("merged") or []):
                _flat.extend(sea.get("eps") or [])
            _pick = None
            if want:
                _pick = next((e for e in _flat
                              if int(e.get("ep") or 0) == want
                              and e.get("routes")), None)
            if _pick is None:
                _pick = next((e for e in _flat if e.get("routes")), None)
            _cs = []
            if _pick:
                for r in (_pick.get("routes") or []):
                    lk = str(r.get("push") or "")
                    if lk and lk not in _cs and _pk.pan_of(lk):
                        _cs.append(lk)
                for s in (_p.get("sources") or []):
                    lk = str(s.get("link") or "")
                    if lk and lk not in _cs and _pk.pan_of(lk):
                        _cs.append(lk)
            # ---- 同一部片常有多个分享（正片 / 剧场版 / 不同站源）----------
            # 2026-11-03 实测：仙逆「第 2 集」被挑成 `Renegade Immortal
            # Movie 2.4K.mp4`（剧场版分享只有一个文件，文件名里的 2 被当成集号，
            # 单文件分享的兜底逻辑直接把它返回了）。
            # 这里按「这个分享里到底有没有这一集 / 有几集」重排候选：
            #   有该集的多集分享 → 优先级最高；集数越多越可信；剧场版垫底。
            _cnt = {}
            for s in (_p.get("sources") or []):
                lk = str(s.get("link") or "")
                if not lk:
                    continue
                _eps = []
                for _sea in (s.get("seasons") or []):
                    for _e in (_sea.get("eps") or []):
                        try:
                            _eps.append(int(_e.get("ep") or 0))
                        except (TypeError, ValueError):
                            pass
                _cnt[lk] = (1 if (want and want in _eps) else 0, len(_eps))
            if _cnt:
                _cs.sort(key=lambda x: (-_cnt.get(x, (0, 0))[0],
                                        0 if "quark" in x.lower() else 1,
                                        -_cnt.get(x, (0, 0))[1]))
            # ---- 用户在浮层里明确点了哪条线路，就以哪条为准（2026-11-04）----
            # 不然服务端会按「有没有这一集 / 夸克优先 / 集数多」自己挑，
            # 结果挑到剧场版或另一个站源的同集（仙逆第1集被挑成
            # `Renegade Immortal Movie 2.1080P.mp4`），用户点的白点。
            _want_link = str(request.args.get("link") or "").strip()
            if _want_link:
                _cs = [_want_link] + [x for x in _cs if x != _want_link]
            return _flat, _pick, _cs

        flat, pick, cands = _collect(p)
        if pick and not cands:
            # 缓存里没有一个能算的网盘 → 作废缓存 + 强制重聚合一次
            app.logger.info("eps cache has no usable pan for %s, refetch", vid)
            _eps_forget(vid)
            try:
                p = _eps_payload(vid, force=True)
                flat, pick, cands = _collect(p)
            except Exception:
                pass

        if pick:
            # 候选里没有夸克 —— 最常见的成因：`collect_full` 串行问四个站源，
            # 玩偶（夸克）在 11 秒 budget 里没回来就被丢掉，只剩木偶（UC）。
            # 而 UC 单连接只有 ~220 KB/s，1080p 都喂不动（用户看到的就是
            # 「播放错误」）。这里宁可多花十几秒重问一次，也别让他去播 UC。
            if not any("quark" in x.lower() for x in cands):
                app.logger.info("no quark candidate for %s, refetch panwall", title)
                # 2026-11-03 修：以前这里无条件 `_eps_forget(vid)` —— 于是
                # 「只有百度源」的片子（如仙逆）每播一次就把 30 分钟缓存作废、
                # 重扫三个站源，20~40 秒全花在这上面。只有**一条可算的链都没有**
                # 时才需要作废重建。
                if not cands:
                    _eps_forget(vid)
                try:
                    from core import panwall as _pw
                    g2 = _pw.collect_full(title, year, budget=15.0)
                    for _gn, _us in (g2 or []):
                        _u = str(_us).split("#")[0].split("$", 1)[-1].strip()
                        if "quark" in _u.lower() and _u not in cands:
                            cands.insert(0, _u)
                except Exception as e:
                    app.logger.info("refetch panwall fail: %s", e)

            # 联调：&pan=uc 只走 UC（验证第二条腿）
            _want_pan = (request.args.get("pan") or "").strip().lower()
            if _want_pan:
                cands = [x for x in cands if _pk.pan_of(x) == _want_pan]
            # 顺序已由 _collect 排好（有没有这一集 → 夸克优先 → 集数多）。
            # **不要**在这里再按平台重排：那会把「有这一集的正片分享」压到
            # 「只有剧场版的夸克分享」后面，第 2 集又挂成剧场版。
            cks = _pk.load() or {}

            def _mk(link, pan, res, kbps):
                """把一条可用直链拼成 haiplay:// 并记账（缓存 + 进度）。"""
                hdr = dict(_pk.dl_headers(pan))
                hdr["Cookie"] = cks.get(pan) or ""
                base = _ext_base().rstrip("/")
                is_hls = (res.get("mode") == "preview")
                if is_hls:
                    # 百度在线播放（PCS streaming）给的是 m3u8：必须让播放器
                    # 看到 `.m3u8` 才会选 HlsMediaSource。
                    #
                    # 2026-11-03：默认走 `/hai/hls`（分片由服务端并发预取代取）。
                    # 理由：百度分片单连接只有 60~140KB/s，而一段 2 秒分片
                    # 要 1.29MB（需 645KB/s 才能连播）⇒ 直连必然越播越卡。
                    # 服务端并发 22~32 路预取实测能到 2.1~2.8MB/s，够 4K。
                    # 想看「分片直连百度」的效果可加 `?hlsraw=1`。
                    tok = _pp_make(res["url"], hdr,
                                   int(res.get("size") or 0))
                    if request.args.get("hlsraw"):
                        path = "/hai/hlsraw/" + tok + ".m3u8"
                    else:
                        path = "/hai/hls/" + tok + ".m3u8"
                elif request.args.get("relay"):
                    tok = _pp_make(res["url"], hdr)
                    path = "/hai/pp/" + tok
                else:
                    path = "/hai/dp/" + _dp_pack(res["url"], hdr,
                                                int(res.get("size") or 0))
                # 2026-11-05 回退：走 app **内建硬编码**的 push_agent。
                # 自定义 HaiDirect 在本机 APK 上取详情会被当 HTTP 站源（null api）
                # → requireNonNull(HttpUrl) NPE → 「找不到数据」。push_agent 不走 HTTP、
                # 不依赖配置解析，直接用 id 造 Vod。id 需 `push://<中枢直链>`，ep 用真实集号。
                _ep = int(pick.get("ep") or 1)
                href = ("haiplay://play?site=" + site_dir + "&id="
                        + urllib.parse.quote(
                            ("push://" if site_dir == "push_agent" else "") + base + path,
                            safe="")
                        + "&ep=" + str(_ep)
                        + "&name=" + urllib.parse.quote(
                            "%s %s" % (title, pick.get("label") or ""), safe=""))
                data = dict(ok=1, mode="direct",
                            direct=(0 if request.args.get("relay") else 1),
                            href=href, ep=pick.get("ep") or 0,
                            label=pick.get("label") or "",
                            pan=res.get("pan") or _pk.pan_of(link),
                            file=res.get("name") or "",
                            size=res.get("size") or 0,
                            total=len(flat), kbps=int(kbps or 0),
                            play_mode="hls" if is_hls else "origin")
                if kbps and kbps < 220:
                    data["note"] = ("这条链路实测只有 %d KB/s（%s），"
                                    "可能卡顿/起播慢" % (kbps, _PAN_CN.get(pan, pan)))
                _pl_put(vid, want, data)
                _pg(vid, want, "ready",
                    "已就绪 · %s%s · %s%s" % (
                        _PAN_CN.get(pan, pan),
                        "（在线播放）" if is_hls else "",
                        _fmt_size(res.get("size")),
                        "（%d KB/s，偏慢）" % kbps if 0 < kbps < 220 else ""),
                    pan=data["pan"], file=data["file"], size=data["size"],
                    kbps=data["kbps"], play_mode=data["play_mode"])
                return jsonify(**data)

            for _i, link in enumerate(cands):
                pan = _pk.pan_of(link)
                _pg(vid, want, "resolve",
                    "正在解析第 %s 集（%s）…" % (pick.get("ep") or 1,
                                                _PAN_CN.get(pan, pan)))
                res = _pk.ep_direct(link, int(pick.get("ep") or 0), title,
                                    pan=pan)
                if not res.get("ok"):
                    app.logger.info("direct miss %s(%s): %s", link[:50], pan,
                                    res.get("error"))
                    continue
                # 起播前**只量第一条**成功的链（128KB，约 0.1~1.3 秒），拿到的
                # 数字只用来**标注**「这条只有 N KB/s，可能卡顿」。
                #
                # 为什么不拿速度去挑候选（2026-11-03 实测教训）：仙逆第 2 集
                # 有两条百度分享 —— 花卷（110 集，正片 XN.S01E02.mp4）和玩偶
                # （3 个文件，剧场版）。两条都是 ~118 KB/s，按“快的优先”排序后
                # 剧场版那点微小差异赢了，第 2 集又挂回剧场版。
                # **正确性 > 速度**：候选顺序已经按「有没有这一集 → 夸克优先 →
                # 集数多」排好，第一条成功的就用它。
                _kbps = 0
                if _i == 0 and res.get("mode") != "preview":
                    _kbps = _link_kbps(res["url"], dict(_pk.dl_headers(pan),
                                                        Cookie=cks.get(pan) or ""))
                return _mk(link, pan, res, _kbps)
        # 算不出直链（非夸克源 / CK 过期 / 分享挂了）就退回 push 那条路

    # ---- ⓪ push 直入：**跳过站源选集页**（2026-11-01 真机实证，最优路径）----
    #
    # 地面事实（电视 adb 实测，见 memory 2026-11-01）：
    #   `VideoActivity(key=push_agent, id=push://<分享链接>)` 会**直接**渲染
    #   网盘文件列表（站源名「推送」/ 类型 UC网盘），跳过了 TMDBWall 那一层
    #   站源选集页 —— 那正是用户抱怨的「先跳海海哥哥.海报墙，再跳推送」里的
    #   第一跳。点第一个文件即出画面，取流 11 MB/s。
    #
    # 为什么只剩这一层：网盘分享要转存 + 要 CK，解析全在 app/jar 内部完成，
    # 外部拿不到终址（反查 pg.jar 的 classes.dex 已确认，没有 dwnz/do=quark
    # 任何端点）。所以「文件列表」是目前的地板，能不能再压掉靠 apk 侧自动点。
    if request.args.get("push"):
        try:
            want = int(request.args.get("ep") or 0)
        except ValueError:
            want = 0
        p = {}
        try:
            p = _eps_payload(vid)
        except Exception:
            p = {}
        flat = []
        for sea in (p.get("merged") or []):
            flat.extend(sea.get("eps") or [])
        pick = None
        if want:
            pick = next((e for e in flat
                         if int(e.get("ep") or 0) == want and e.get("routes")), None)
        if pick is None:
            pick = next((e for e in flat if e.get("routes")), None)
        if pick:
            r0 = pick["routes"][0]
            link = ""
            for s in (p.get("sources") or []):
                if (s.get("site") == r0.get("site")
                        and s.get("pan") == r0.get("pan") and s.get("link")):
                    link = s["link"]
                    break
            if link:
                # id 必须整体 urlencode —— 分享链接自带 `?public=1`，
                # 裸拼会被 Uri 的 query 解析从 `&`/`?` 处截断。
                href = ("haiplay://play?site=push_agent&id="
                        + urllib.parse.quote("push://" + link, safe="")
                        + "&ep=" + str(int(r0.get("ep") or 1))
                        + "&name=" + urllib.parse.quote(
                            "%s %s" % (title, pick.get("label") or ""), safe=""))
                return jsonify(ok=1, mode="push", href=href, link=link,
                               ep=pick.get("ep") or 0,
                               label=pick.get("label") or "",
                               total=len(flat), site=r0.get("site") or "",
                               pan=r0.get("pan") or "")
        # 一集都列不出来时往下走 TMDBWall 兜底，别让按钮点下去没反应

    # ---- ① 直链模式（2026-11-01 海哥：播放链条不许露 PG 原生界面）--------
    #
    # 老实现返回 site_key+id，app 会打开该站的**选集页**（PG 的灰底列表），
    # 这就是「露原生界面」。带 direct=1 时直接从网盘聚合结果里挑一集，
    # 拼成 `haiplay://play?site=HaiDirect&id=<127.0.0.1:9978 直链>`，
    # 播放器起来就是画面，中间没有任何站源页面。
    if request.args.get("direct"):
        try:
            want = int(request.args.get("ep") or 0)
        except ValueError:
            want = 0
        p = {}
        try:
            p = _eps_payload(vid)
        except Exception:
            p = {}
        flat = []
        for sea in (p.get("merged") or []):
            flat.extend(sea.get("eps") or [])
        pick = None
        if want:
            pick = next((e for e in flat
                         if int(e.get("ep") or 0) == want and e.get("routes")), None)
        if pick is None:
            pick = next((e for e in flat if e.get("routes")), None)
        # 注意：这里**绝不能**再 `return routes[0]["href"]`。那条地址是
        # `127.0.0.1:9978/proxy?do=quark&...` —— 9978 是 app 内置 server，
        # 播放器把它当普通 URL 直接拉会拿到空响应，界面上就是「播放无数据」
        # （老版本正是栽在这一句上，2026-11-01 15:59 删除）。
        # 算不出夸克直链就让它继续往下走 push 兜底。

    # ---- ⓪ 先用 TMDBWall 自己（2026-11-01 真机验证）----------------------
    #
    # 这是 app 里**必定存在**的站点，而且它的 detail() 本来就带完整线路
    # （夸克/百度/天翼，push:// 形式）。所以只要按片名在它自己的库里搜到
    # 完全同名的一条，`haiplay://play?site=TMDBWall&id=<vod_id>` 就能直接
    # 打开选集页并播放 —— 实测「极速：伪装者」这条链 100% 通。
    #
    # 豆瓣源没有 TMDB 的 vod_id，但片名是共用的，所以这一步对两个源都适用，
    # 也是唯一一条不依赖第三方网盘站死活的播放入口。
    # 2026-11-01 20:xx 删掉了原来那段「按片名在 TMDBWall 库里搜同名」兜底。
    # 它返回的 `site=TMDBWall` 在这台电视上**必定 NPE**（app 从不加载自建 js
    # 站点），用户看到的就是「无数据」。宁可如实返回失败原因。

    try:
        from core import panwall
        r = panwall.resolve_play(title, year)
    except Exception as e:
        return jsonify(ok=0, error="%s: %s" % (type(e).__name__, e))
    if not r:
        # 别只丢一句「没有完全同名的片源」—— 用户看到的就一句「无数据」。
        # 这里把真正卡住的那一步说出来：是站源没这部片，还是只有我们
        # 解析不了的网盘（百度/115/阿里/天翼）。
        why = "站源里没搜到这部片"
        pans = []
        try:
            for s0 in (p.get("sources") or []):
                pn = (s0.get("pan") or "").strip()
                if pn and pn not in pans:
                    pans.append(pn)
            if pans:
                ok_pans = [x for x in pans if x in ("夸克", "UC")]
                if not ok_pans:
                    why = "这部片只有 %s 源，暂不支持" % "/".join(pans)
                else:
                    why = "网盘解析失败（%s），可能是空间不足或分享失效" % "/".join(pans)
        except Exception:
            pass
        return jsonify(ok=0, error=why, title=title, pans=pans)
    resp = dict(ok=1, mode="site", site_key=r["site_key"], site=r["site_name"],
                id=r["id"], name=r.get("name") or title,
                pic=_pic_u, title_hit=True)
    if request.args.get("debug"):
        from core import panwall as _pw
        resp["debug"] = {"title": title, "year": year,
                         "site": r["site_name"], "num_id": r.get("num_id"),
                         "order": [s["name"] for s in _pw.play_sites()]}
    return jsonify(**resp)


# 网盘优先级：夸克最稳（不封外链、直链失效慢），UC 次之。同集有多条线路时
# 「自动」就按这个顺序取第一条能播的（2026-11-01）。
# 网盘优先级：同集有多条线路时「自动」就按这个顺序取第一条。
#
# 2026-11-01 真机改判：**UC 排第一**。原来夸克打头（理由是「不封外链、直链
# 失效慢」），但那是给「本机 9978 直链」那套假设写的，而那条路已被证伪。
# 现在走的是 `site=push_agent&id=push://<分享>` —— app 内置的 Push 解析器
# 实测对 UC 分享能列出文件并起播（11MB/s），对**夸克分享直接解析不出来**：
# 页面只剩「推送 / 播放 / 原始链接」三行，等于播不了。所以 UC 优先。
_PAN_RANK = {"夸克": 0, "百度": 1, "阿里": 2, "115": 3, "UC": 4}
# 2026-11-04 改判：**UC 降到倒数** —— 实测这个账号在 UC 上只有**试看权限**
# （分享页报 1,412,118,820 = 1.35GB，UC 直链的 Content-Range /total 只有
# 15,340,287 = 15MB，转存到自己盘也一样）⇒ 优先夸克/百度，
# 别再让用户点 UC 然后卡在启动画面出不来。
# （老注释说"UC 优先"是 2026-11-01 的判断，当时没验证过文件完整性。）

# 直链短化表：token -> (真实 9978 直链, 过期时间)
#
# 为什么不在 haiplay:// 里直接塞直链（2026-11-01 真机）：一条 127.0.0.1:9978
# 的地址编码后 800+ 字符，WebView 拦截 → Uri 解析 → 播放器任何一环都可能截断
# 或超限，出问题时日志里也是一团乱。换成 12 位 token，真地址留在服务端。
# 直链走哪个站点：
#   "HaiDirect"  —— **我们自己的站源**（assets/js/paidirect.js）：
#                   detail() 把 id 原样当播放地址交出去、play() 直接返回 url，
#                   **不启任何原生 Activity** ⇒ 全屏播放、一次返回就回详情页。
#   "push_agent" —— PG 内建的推送站源：会把 URL 渲染成一个**原生「推送」详情页**
#                   （紫底 + 直连/嗅探/解析 标签），播放器只是它的小窗。
#                   全屏时看不出来，**一按返回就露出来** —— 这就是"播放流程
#                   冒出原生页面"的根源（2026-11-04 真机截图坐实）。
#   "TMDBWall"   —— 自建 spider，play() 能直接返回「直链+请求头」，
#                   播放器直连 CDN（理论上满速），但 app 未必加载它
_SITE_DIRECT = "push_agent"

_PP: dict = {}
_PP_TTL = 8 * 3600
_PP_PATH = os.path.join(C.DATA_DIR, "pp.json")
_PP_LOADED = [False]


def _pp_load() -> None:
    """把短链表从盘上捞回来。

    为什么必须落盘（2026-11-01 16:20 修）：token 原先只存内存，服务一重启
    （改代码 / systemd restart）整张表就空。电视上播放器此时拿到的还是
    「几秒前生成的那个 token」→ 410 → 界面直接「播放无数据」。
    一整个下午的重启把这条踩了无数遍。
    """
    if _PP_LOADED[0]:
        return
    _PP_LOADED[0] = True
    try:
        with open(_PP_PATH, "r", encoding="utf-8") as f:
            d = json.load(f) or {}
        now = time.time()
        for k, v in d.items():
            if isinstance(v, list) and len(v) >= 2 and float(v[1]) > now:
                _PP[k] = (v[0], float(v[1]), v[2] if len(v) > 2 else {},
                          int(v[3]) if len(v) > 3 else 0)
    except Exception:
        pass


def _pp_save() -> None:
    try:
        tmp = _PP_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(_PP, f, ensure_ascii=False)
        os.replace(tmp, _PP_PATH)
    except Exception:
        pass


def _dp_pack(url: str, hdr: dict, size: int = 0) -> str:
    """生成 `/hai/dp/<token>` 里的 token。

    起先试过把「直链 + 请求头」整体 base64 塞进 URL（自包含、APK 侧不用
    发请求），但实测 b64 长度 4098 —— 加上 haiplay:// 和 push:// 前缀
    整条 URL 超过 4K，Uri/Intent 传递不可靠。

    改成复用 `_pp` 短链表：token 12 位，APK 侧拦截器看到 `/hai/dp/` 就先
    问一次 `/hai/dp/<token>?hdr=1` 拿 {url, header}，再改写请求直连 CDN。
    多一次本地 HTTP 往返（几毫秒），换来一个 40 字符的短地址。
    """
    return _pp_make(url, hdr, size)


def _dp_unpack(b: str) -> dict:
    # 播放器那边可能带 `.m3u8` 后缀（HLS 要靠后缀选解码器），这里剥掉
    b = str(b or "").split(".")[0]
    """token → {u, h}。供 /hai/dp/<b> 兜底（拦截器没生效时）使用。"""
    g = _pp_get(b)
    if not g:
        return {}
    return {"u": g[0], "h": g[1], "size": g[2]}


def _pp_make(url: str, hdr: dict = None, size: int = 0) -> str:
    """登记一条 CDN 直链，返回 12 位短 token。

    存成 `(url, exp, hdr, size)` 四元组 —— size 是给 APK 侧多线程数据源用的：
    它要按 Range 切段就得先知道文件总长（不然播放器发 `bytes=0-` 时没法算
    Content-Range）。读取端统一用 `_pp_get()`，兼容老的三元组。
    """
    _pp_load()
    key = str(url) + "|" + "|".join(sorted((hdr or {}).keys()))
    t = hashlib.md5(key.encode("utf-8")).hexdigest()[:12]
    now = time.time()
    if len(_PP) > 4000:                     # 简单清理，别让字典无限涨
        for k, v in list(_PP.items()):
            if v[1] < now:
                _PP.pop(k, None)
    _PP[t] = (str(url), now + _PP_TTL, dict(hdr or {}), int(size or 0))
    _pp_save()
    return t


def _pp_get(t: str):
    """(url, hdr, size) 或 None —— 兼容历史上只存三元组的 pp.json。"""
    _pp_load()
    hit = _PP.get(t)
    if not hit or hit[1] < time.time():
        return None
    size = int(hit[3]) if len(hit) > 3 else 0
    return hit[0], (hit[2] if len(hit) > 2 else {}) or {}, size


_PP_POOL = {"ex": None}


def _pp_pool():
    """拉夸克的共享线程池（8 路并发 × 最多 3 条流，留点余量）。"""
    if _PP_POOL["ex"] is None:
        from concurrent.futures import ThreadPoolExecutor
        _PP_POOL["ex"] = ThreadPoolExecutor(max_workers=24)
    return _PP_POOL["ex"]


def _fetch_seg(url: str, hdr: dict, s: int, e: int) -> bytes:
    """并发分段用：拉 [s, e] 这一段，整段读满再返回。

    放在模块级（不嵌在视图里）是为了能丢进线程池。
    """
    need = e - s + 1
    for attempt in (0, 1):
        try:
            h = dict(hdr)
            h["Range"] = "bytes=%d-%d" % (s, e)
            with urllib.request.urlopen(
                    urllib.request.Request(url, headers=h), timeout=20) as f:
                # 直接 read() 读完这一段。别用 read1(256KB) 小块循环 ——
                # 实测那样单段只有 ~200 KB/s（每次 read1 都要过一遍底层缓冲），
                # 8 路并发加起来还不到 1.3 MB/s，比单流还慢。
                return f.read() or b""
        except Exception:
            if attempt:
                return b""
    return b""


def _stream(url: str, hdr: dict):
    """带鉴权头的流式中转 —— 兼治夸克的「开区间限速」。

    为什么必须自己当中转（2026-11-01 实证）：夸克 CDN 直链有防盗链，
    **只带 UA 会 412 Precondition Failed**，必须 UA + Referer + Cookie
    同时在请求头上。而电视上的 ExoPlayer 只会发一条光杆 GET（顶多带
    Range），既不会带 Cookie 也不会带 Referer —— 302 过去必挂。

    为什么还要**改写 Range**（同日实证，这是黑屏的真凶）：
        Range: bytes=5992-          → 102 KB/s     ← 开区间，被限速
        Range: bytes=0-             → 102 KB/s
        Range: bytes=5992-104857599 → 2.2 MB/s     ← 限定范围，满速
    夸克对「没有结束位置的 Range」一律掐到 100 KB/s。而 ExoPlayer 解析
    mkv 时发的正是一条 `bytes=5992-`（读到文件末尾），于是黑屏、68 KB/s。
    解法：对外照原样答应（Content-Range 仍写 start-末尾），内部按 64MB
    分段去要，逐段拼给播放器 —— 分段请求带 end，就不触发限速。
    """
    rng = (request.headers.get("Range") or "").strip()
    start, end = 0, None
    if rng.lower().startswith("bytes="):
        p = rng[6:].split("-", 1)
        try:
            start = int((p[0] or "0").strip() or 0)
        except ValueError:
            start = 0
        if len(p) > 1 and p[1].strip():
            try:
                end = int(p[1].strip())
            except ValueError:
                end = None

    # ---- 探测块的**大小要试**（2026-11-03 百度 3003 的真凶）-----------------
    #
    # 百度非会员（is_vip=0）只给 `rt=pr` 预览通道，除了限速 ~100 KB/s 之外，
    # 还**限制单次 Range 的跨度**：
    #     bytes=0-1MB   → 206
    #     bytes=0-4MB   → 206（56 秒，≈75 KB/s）
    #     bytes=0-8MB   → 403 {"error_code":31326,"hitcode":104}
    # 而原来这里固定按 64MB 探测 → 百度必 403 → 我们回一段 12 字节的
    # "upstream 403" 文本 → 播放器解不出媒体 → 界面就是 **3003**。
    # 现在按 64→32→8→4→2MB 依次退让，谁先成功就用谁的步长往下吐。
    CH = 64 * 1024 * 1024
    up = None
    _last = None
    for _mb in (64, 32, 8, 4, 2):
        _chunk = _mb * 1024 * 1024
        _fe = (start + _chunk - 1) if end is None else min(start + _chunk - 1, end)
        _h = dict(hdr)
        _h["Range"] = "bytes=%d-%d" % (start, _fe)
        try:
            up = urllib.request.urlopen(urllib.request.Request(url, headers=_h),
                                        timeout=30)
            CH = _chunk
            break
        except urllib.error.HTTPError as e:
            _last = e
            if e.code == 416:      # 越界：跟块大小无关，直接回给播放器
                return Response("416", status=416,
                                headers={"Content-Range":
                                         e.headers.get("Content-Range") or ""})
            if e.code not in (400, 403, 503, 500):
                return Response("upstream %s" % e.code, status=min(e.code, 599))
            try:
                e.read(2048)
            except Exception:
                pass
            continue
        except Exception as e:
            return Response("upstream %s: %s" % (type(e).__name__, e), status=502)
    if up is None:
        code = getattr(_last, "code", 502) or 502
        return Response("upstream %s" % code, status=min(code, 599))

    cr = up.headers.get("Content-Range") or ""
    m = re.match(r"bytes\s+(\d+)-(\d+)/(\d+)", cr)
    total = int(m.group(3)) if m else 0
    if end is None:
        end = (total - 1) if total else None
    if end is not None and total and end >= total:
        end = total - 1

    out = {"Content-Type": up.headers.get("Content-Type") or "application/octet-stream",
           "Accept-Ranges": "bytes", "Cache-Control": "no-store"}
    status = 200
    if end is not None:
        out["Content-Length"] = str(end - start + 1)
        if rng.lower().startswith("bytes="):
            status = 206
            out["Content-Range"] = "bytes %d-%d/%s" % (
                start, end, total or "*")
    if request.method == "HEAD":
        try:
            up.close()
        except Exception:
            pass
        return Response(b"", status=status, headers=out)

    # 并发预取参数（2026-11-01 实测标定）
    #
    # 夸克对**单条连接**掐在 2.2 MB/s，但**不限总带宽**：
    #     1 路 2.20 MB/s │ 4 路 8.25 MB/s │ 8 路 18.0 MB/s
    # 4K 高码率（~60 Mbps = 7.5 MB/s）单流根本喂不动，就是这个原因。
    # 所以按 4MB 切块、8 路并发去拉，主线程**按序**吐给播放器 ——
    # 对内并发，对外仍是一条连续的 206。
    #
    # 前两块故意做小（256KB）：播放器对「首字节」很敏感，等 4MB 攒齐
    # 要 1.8 秒，实测直接报「连接超时」。首块 256KB 约 120ms 就能吐。
    SIZE = 4 * 1024 * 1024
    FIRST = 256 * 1024
    WORKERS = 8
    AHEAD = 10

    def _fetch(s: int, e: int) -> bytes:
        need = e - s + 1
        for attempt in (0, 1):
            try:
                h = dict(hdr)
                h["Range"] = "bytes=%d-%d" % (s, e)
                with urllib.request.urlopen(
                        urllib.request.Request(url, headers=h), timeout=20) as f:
                    px = getattr(f, "read1", None) or f.read
                    buf, got = [], 0
                    while got < need:
                        b = px(min(262144, need - got))
                        if not b:
                            break
                        buf.append(b)
                        got += len(b)
                    return b"".join(buf)
            except Exception:
                if attempt:
                    return b""
        return b""

    def gen():
        # 上游没给总长度时退回单流顺序转发（少见：夸克都带 Content-Range）
        if end is None:
            cur = up
            try:
                while True:
                    px = getattr(cur, "read1", None) or cur.read
                    b = px(1024 * 1024)
                    if not b:
                        break
                    yield b
            except Exception:
                pass
            finally:
                try:
                    cur.close()
                except Exception:
                    pass
            return

        try:
            up.close()             # 探测用的连接不再需要
        except Exception:
            pass

        # **分段 + 有限并发**转发（2026-11-01 17:10 定稿）
        #
        # 为什么必须分段：开区间 Range（`bytes=5992-`）会被夸克/UC 掐到
        # ~100 KB/s，ExoPlayer 解析 mkv 发的正是这种；切成定长段才有 2.2 MB/s。
        #
        # 为什么必须并发：夸克**单连接**上限 2.2 MB/s，UC 只有 ~220 KB/s。
        # 实测叠加是线性的（1 路 2.2 / 4 路 8.2 / 8 路 18 MB/s）。
        # 8 路并行才够 4K；UC 那种 220 KB/s 更需要（4 路 ≈ 900 KB/s）。
        #
        # 为什么这次不会卡死：池子**全局共享**（24 线程），但每条流同时最多
        # 只挂 AHEAD=6 个任务。上一版是每流 AHEAD=16 + 共享池，一条流就把
        # 池子占满，客户端一断残留任务不取消 → 后一个请求卡到超时
        #（真机「连接超时」）。现在 4 条并发流才 24 个任务，且 finally 里
        # 全部 cancel，断流即回收。
        # 单线程分段（2026-11-01 17:20 定稿，并发版实测更慢）
        #
        # 实测对比（同一夸克直链、同一台服务器）：
        #   单线程分段 32MB、read1(1MB) 边下边吐 ......... 3.40 MB/s
        #   6 路并发 4MB 块、整块 read() 再吐 ........... 1.73 MB/s
        #   裸跑 t_multi.py 8 路（不受 Flask 影响）...... 7.80 MB/s
        # 并发版慢是因为「整块读完才吐」把流水线拖平了，而且首块之后
        # 任何一块慢都会卡住整条流。单线程边下边吐反而更稳更快。
        # 想让播放器真跑满千兆，只有让它**直连 CDN**（带 Cookie），
        # 那需要改 APK 给 ExoPlayer 注入请求头，不是服务端能解决的。
        # 32MB 单线程分段、read1(1MB) 边下边吐 —— 实测最快的形态
        #（2026-11-01 17:45 定稿，三种切片都量过）：
        #   32MB 单线程 ......... 3.40 MB/s   ← 保留这个
        #   4MB 块 × 6 路并发 ... 1.73 MB/s
        #   1MB 块 × 8 路并发 ... 2.48 MB/s
        #   裸跑 8 路（绕过 Flask）7.80 MB/s
        # 结论：Flask 中转吃不满并发，「整块读完再吐」会把流水线拖平。
        # 想真正跑满千兆只有让播放器直连 CDN（PG 的 @Headers 机制），
        # 那需要改 APK 给 OkHttpDataSource 注入请求头。
        # 用**探测成功**的块长（上限 32MB）。夸克/UC 走 32MB 满速；
        # 百度那种只认 2~4MB 的上游也能正常吐流，不会再 403。
        CHUNK = min(CH, 32 * 1024 * 1024)
        off, cur = start, None
        try:
            while off <= end:
                ce = min(off + CHUNK - 1, end)
                need = ce - off + 1
                got = 0
                try:
                    h2 = dict(hdr)
                    h2["Range"] = "bytes=%d-%d" % (off, ce)
                    cur = urllib.request.urlopen(
                        urllib.request.Request(url, headers=h2), timeout=20)
                    px = getattr(cur, "read1", None) or cur.read
                    while got < need:
                        b = px(min(1024 * 1024, need - got))
                        if not b:
                            break
                        got += len(b)
                        yield b
                except GeneratorExit:
                    raise
                except Exception:
                    break
                finally:
                    if cur is not None:
                        try:
                            cur.close()
                        except Exception:
                            pass
                        cur = None
                if got <= 0:
                    break
                off += got
        finally:
            if cur is not None:
                try:
                    cur.close()
                except Exception:
                    pass

    return Response(gen(), status=status, headers=out, direct_passthrough=True)


@bp.route("/hai/pan/play")
def hai_pan_play():
    """任意网盘分享链接 → 可直接起播的 haiplay:// 地址。

    用途：站源里搜不到、或者用户手上就有一条分享链接时，不必做成剧集，
    直接把 `pan.quark.cn/s/xxx` / `drive.uc.cn/s/xxx` 丢进来就能播。

    参数：
        url   分享链接（必填，可带 ?pwd=）
        ep    第几集（默认 1）
        name  片名（只用于播放器标题）
        raw   1 = 只返回直链 json，不拼 haiplay://
    """
    from core import panck as _pk

    u = (request.args.get("url") or "").strip()
    if not u:
        return jsonify(ok=0, error="缺少 url")
    # 用户从 App 复制的分享文案里常带前后缀，抠出干净链接
    import re as _re
    m = _re.search(r"https?://[^\s一-龥]+", u)
    if m:
        u = m.group(0).rstrip("，,。.)）")
    ep = 1
    try:
        ep = max(1, int(request.args.get("ep") or 1))
    except Exception:
        ep = 1
    name = (request.args.get("name") or "").strip()
    pan = (request.args.get("pan") or "").strip().lower()

    res = _pk.ep_direct(u, ep, name, pan=pan)
    if not res.get("ok"):
        return jsonify(ok=0, error=res.get("error") or "取链失败",
                       url=u, pan=_pk.pan_of(u))

    ck = (_pk.load() or {}).get(_pk._cfg(res["pan"])["root"]) or ""
    hdr = _pk.dl_headers(res["pan"])
    hdr["Cookie"] = ck

    if request.args.get("raw"):
        return jsonify(ok=1, mode="raw", url=res["url"], header=hdr,
                       pan=res["pan"], size=res.get("size") or 0,
                       file=res.get("name") or "")

    base = _ext_base().rstrip("/")
    tok = _dp_pack(res["url"], hdr, int(res.get("size") or 0))
    # HLS（百度试看 m3u8）必须让播放器看到 `.m3u8` 后缀，否则 ExoPlayer
    # 会走 ProgressiveMediaSource，把 m3u8 当普通文件读 → 只有音频没有画面。
    # preview = HLS：**不能**用 /hai/dp/（APK 拦截器会把它直接改写到百度域名，
    # 分片随之落到 bdcm06.baidupcs.com，被 PG 拦到 9978）。改用 /hai/hls/，
    # 拦截器不认这个前缀 → 请求落到服务端 → 服务端下发改写过的 m3u8。
    if res.get("mode") == "preview":
        # HLS 必须让播放器看到 .m3u8 才会选 HlsMediaSource。
        # PG 会把它包装成 http://127.0.0.1:9978/m3u8?url=...，由 APK 里的
        # OkHttp 拦截器（com.hai.HaiDp）剥掉包装，直接请求内层地址。
        #
        # 用 hlsraw（**不改写分片**）：实测电视能直接解析并连 bdcm06.baidupcs.com，
        # 视频数据全程不经过服务器 —— 和夸克直连 CDN 是一样的路径。
        # 需要请求头才能取的分片，把这里换成 "/hai/hls/" 即可（走 /hai/ts 中转）。
        path = "/hai/hlsraw/" + tok + ".m3u8"
    else:
        path = "/hai/dp/" + tok
    label = res.get("name") or name or "网盘"
    # 2026-11-05 回退：push_agent（app 内建特判，不走 HTTP/配置解析，不会 NPE）。
    # id = `push://<中枢直链>`，ep 真实集号。
    href = ("haiplay://play?site=push_agent&id="
            + urllib.parse.quote("push://" + base + path, safe="")
            + "&ep=" + str(int(ep or 1))
            + "&name=" + urllib.parse.quote(label, safe=""))
    return jsonify(ok=1, mode="direct", href=href, pan=res["pan"],
                   play_mode=res.get("mode") or "origin",
                   ep=ep, file=res.get("name") or "",
                   size=res.get("size") or 0, no_xfer=res.get("no_xfer") or 0,
                   note=res.get("note") or "")


# HLS 分片真地址表：{token: [url, url, ...]}。
# 每次请求 m3u8 都会重新解析并覆盖，所以不需要持久化。
_PPTS: dict = {}

# ---- HLS 分片「并发预取」缓存（2026-11-03）-------------------------------
#
# 为什么需要：百度每个分片都是**独立的一次 GET**，单条连接被限到
# 60~140 KB/s；但不同分片并发可以把总带宽叠起来。同一出口实测：
#     8 并发 → 212 KB/s    16 → 510 KB/s
#    24 并发 → 2130 KB/s   32 → 2770 KB/s（还会继续涨）
# PG 原生播百度 4K 能到 3.4 MB/s、25GB 的片子能冲到 14 MB/s，用的就是
# 同一招「多连接分片」（它内置 127.0.0.1:8096/kaiser?thread=16）。
# 那个本地代理只在它自己播网盘时才起，外部连不上，所以这里自己实现一份：
# 播放器顺序请求 /hai/ts/<tok>/<n>，后台同时把 n..n+22 分片全下好放内存，
# 播放器按序取时基本直接命中，等效带宽 = 并发数 × 单连接速度。
_TSC_LOCK = threading.Lock()
_TSC_DATA = {}        # (tok, idx) -> bytes
_TSC_EVT = {}         # (tok, idx) -> threading.Event
_TSC_WANT = set()     # 正在下载的 key
_TSC_ORDER = []       # 插入顺序（淘汰用）
# 这台机器只有 1.8GB 内存（J1900 四核，应用自身已占 ~500MB），缓存别开太大。
_TSC_CAP = int(os.environ.get("HAI_TS_CAP_MB") or 128) * 1024 * 1024
# 实测（同一出口、分享 4K 转码分片）：
#   1 连接 60~140 KB/s；8→212KB/s；16→510；24→2.1MB/s；
#   32→2.7MB/s；**64 并发 → 4.4MB/s（64/64 全成功）**
# 分片本身也支持 Range（206），所以每片再切 8 块 ⇒ 首片也能并行拉，起播不干等。
# 这里：窗口 8 片 × 每片 8 块 ≈ 64 条连接。
# 三个旋钮（可用环境变量覆盖，不必改代码）：
#   HAI_TS_WINDOW 预取窗口：一次排进队列多少个分片（排队长度，不是并发数）
#   HAI_TS_PARTS  每片切几块 Range 并行
#   HAI_TS_POOL   同时“装配”几个分片
# **真实并发连接数 ≈ POOL × PARTS**（窗口只决定队列有多长）
#   默认 24 × 6 = 144 条连接 ⇒ 按实测每连接约 62KB/s 估，约 8~9 MB/s。
_TSC_WINDOW = int(os.environ.get("HAI_TS_WINDOW") or 40)
_TS_PARTS = int(os.environ.get("HAI_TS_PARTS") or 8)
_TSC_POOL = concurrent.futures.ThreadPoolExecutor(
    max_workers=int(os.environ.get("HAI_TS_POOL") or 8))


def _tsc_put(key, data):
    ev = None
    with _TSC_LOCK:
        _TSC_DATA[key] = data
        _TSC_ORDER.append(key)
        _TSC_WANT.discard(key)
        ev = _TSC_EVT.get(key)
        total = sum(len(v) for v in _TSC_DATA.values())
        while total > _TSC_CAP and _TSC_ORDER:
            old = _TSC_ORDER.pop(0)
            d = _TSC_DATA.pop(old, None)
            if d:
                total -= len(d)
            _TSC_EVT.pop(old, None)
    if ev:
        ev.set()


def _tsc_part(url, hdr, a, b, out, slot):
    h = dict(hdr)
    h["Range"] = "bytes=%d-%d" % (a, b)
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=h),
                                    timeout=60) as f:
            out[slot] = f.read()
    except Exception:
        out[slot] = b""


def _tsc_fetch(tok, idx, url, hdr, parts=None):
    """取一个分片：先拉第一块拿总长，再按 Range 并行切剩下的块。

    单个分片约 1.29MB、单连接只有 60~140KB/s，串行要 10 秒，而它只覆盖 2 秒
    播放，播放器必然越播越卡。切成 8 块并行，1~2 秒就能凑齐。
    """
    try:
        first = 512 * 1024
        h = dict(hdr)
        h["Range"] = "bytes=0-%d" % (first - 1)
        with urllib.request.urlopen(urllib.request.Request(url, headers=h),
                                    timeout=45) as f:
            head = f.read()
            cr = f.headers.get("Content-Range") or ""
        m = re.match(r"bytes[ ]+[0-9]+-[0-9]+/([0-9]+)", cr)
        total = int(m.group(1)) if m else 0
        if not total or total <= len(head):
            _tsc_put((tok, idx), head)
            return
        # 首片影响起播速度：给它更多分块（一次 10 条连接抢）
        n = int(parts or _TS_PARTS)
        step = (total + n - 1) // n
        ranges = []
        a = len(head)
        while a < total:
            b = min(a + step - 1, total - 1)
            ranges.append((a, b))
            a = b + 1
        out = [None] * len(ranges)
        ths = []
        for k, (a, b) in enumerate(ranges):
            t = threading.Thread(target=_tsc_part,
                                 args=(url, hdr, a, b, out, k))
            t.daemon = True
            t.start()
            ths.append(t)
        for t in ths:
            t.join(75)
        buf = [head]
        for piece in out:
            if piece:
                buf.append(piece)
        data = b"".join(buf)
        # 校验：某些分片 302 到忽略 Range 的节点，会把整片重复返回。
        # 长度对不上就丢掉，退回「单条整取」这条最稳的路。
        if abs(len(data) - total) > 1:
            app.logger.info("tsc assemble mismatch idx=%s got=%s want=%s",
                            idx, len(data), total)
            with urllib.request.urlopen(
                    urllib.request.Request(url, headers=dict(hdr)),
                    timeout=60) as f2:
                data = f2.read()
        _tsc_put((tok, idx), data)
    except Exception:
        ev = None
        with _TSC_LOCK:
            _TSC_WANT.discard((tok, idx))
            ev = _TSC_EVT.pop((tok, idx), None)
        if ev:
            ev.set()


def _tsc_warm(tok, idx, segs, hdr):
    """后台预取 idx..idx+WINDOW（幂等）。

    注意：预取**只开 1 条连接**。之前每片都切 8 块，40 片一起上就是 320 条连接，
    把播放器正等的首片挤到 7 秒以上 ⇒ EXO 读超时（就是"连接超时 0KB/s"）。
    现在的策略是：播放器要哪片，那片单独走 16 块优先车道；其余慢慢来。
    """
    for i in range(idx, min(idx + _TSC_WINDOW, len(segs))):
        key = (tok, i)
        with _TSC_LOCK:
            if key in _TSC_DATA or key in _TSC_WANT:
                continue
            _TSC_WANT.add(key)
            _TSC_EVT.setdefault(key, threading.Event())
        _TSC_POOL.submit(_tsc_fetch, tok, i, segs[i], hdr, 1)


def _tsc_slice(data, rng):
    """按 Range 头切已经缓存的整片。"""
    m = re.match(r"bytes=(\d+)-(\d*)", rng or "")
    if not m:
        return data, None
    a = int(m.group(1))
    b = int(m.group(2)) if m.group(2) else len(data) - 1
    b = min(b, len(data) - 1)
    if a > b:
        return b"", None
    return data[a:b + 1], "bytes %d-%d/%d" % (a, b, len(data))


@bp.route("/hai/hlsraw/<b>")
def hai_hlsraw(b):
    """原样透传上游 m3u8（分片保持上游地址），用于验证直连可行性。"""
    tok = str(b or "").split(".")[0]
    hit = _PP.get(tok)
    if not hit or hit[1] < time.time():
        return Response("#EXTM3U", status=410,
                        mimetype="application/vnd.apple.mpegurl")
    try:
        with urllib.request.urlopen(
                urllib.request.Request(hit[0], headers=dict(hit[2] or {})),
                timeout=20) as f:
            txt = f.read().decode("utf-8", "ignore")
    except Exception:
        return Response("#EXTM3U", status=502,
                        mimetype="application/vnd.apple.mpegurl")
    return Response(txt, mimetype="application/vnd.apple.mpegurl",
                    headers={"Cache-Control": "no-cache"})


@bp.route("/hai/hls/<b>")
def hai_hls(b):
    """把上游 m3u8 改写成「分片走本机」的版本。

    为什么必须改写：百度 m3u8 里的分片是 `bdcm06.baidupcs.com`，
    **PG 会把非白名单域名的请求拦到它内置 URL 代理 127.0.0.1:9978**
    （那个服务没起）→ ExoPlayer 报 `Failed to connect to /127.0.0.1:9978`，
    状态在 BUFFERING/IDLE 之间死循环。实测日志见 2026-11-02 03:36。

    改成 `http://10.0.0.11:8099/hai/ts/<tok>/<n>`（内网 IP）后 PG 不再拦截。
    """
    tok = str(b or "").split(".")[0]
    hit = _PP.get(tok)
    if not hit or hit[1] < time.time():
        return Response("#EXTM3U\n", mimetype="application/vnd.apple.mpegurl",
                        status=410)
    url, _exp, hdr = hit[0], hit[1], hit[2]
    try:
        req = urllib.request.Request(url, headers=dict(hdr or {}))
        with urllib.request.urlopen(req, timeout=20) as f:
            txt = f.read().decode("utf-8", "ignore")
    except Exception:
        app.logger.exception("hls fetch fail")
        return Response("#EXTM3U\n", mimetype="application/vnd.apple.mpegurl",
                        status=502)

    base = _ext_base().rstrip("/")
    segs = []
    out = []
    for ln in txt.split("\n"):
        st = ln.strip()
        if st.startswith("http"):
            idx = len(segs)
            segs.append(st)
            out.append("%s/hai/ts/%s/%d" % (base, tok, idx))
        elif st.startswith("#EXT-X-KEY") and "URI=" in st:
            # 加密流：key 也换成本机代理
            m = re.search(r'URI="([^"]+)"', st)
            if m:
                idx = len(segs)
                segs.append(m.group(1))
                st = st.replace(m.group(1), "%s/hai/ts/%s/%d" % (base, tok, idx))
            out.append(st)
        else:
            out.append(ln)
    _PPTS[tok] = segs
    # 播放器拿到列表后立刻会要第 0 片：先把开头几片丢进并发队列抢时间。
    try:
        _tsc_warm(tok, 1, segs, {k: v for k, v in (hdr or {}).items()
                                 if k != "Cookie"})
    except Exception:
        app.logger.exception("ts warm fail")
    return Response("\n".join(out), mimetype="application/vnd.apple.mpegurl",
                    headers={"Cache-Control": "no-cache"})


@bp.route("/hai/ts/<b>/<int:idx>", methods=["GET", "HEAD"])
def hai_ts(b, idx):
    """HLS 分片代理：按 index 取真地址再转发（支持 Range）。"""
    tok = str(b or "").split(".")[0]
    segs = _PPTS.get(tok) or []
    if idx < 0 or idx >= len(segs):
        return Response("no seg", status=404)
    hit = _PP.get(tok)
    hdr = dict((hit[2] if hit else {}) or {})
    # 分片只认 UA，带上 Cookie 反而可能被风控
    hdr.pop("Cookie", None)
    url = segs[idx]
    rng = request.headers.get("Range")

    # ① 缓存命中：并发预取已经把这一片下好了
    key = (tok, idx)
    with _TSC_LOCK:
        data = _TSC_DATA.get(key)
        ev = _TSC_EVT.get(key)
    if data is None:
        # ② 没命中：**当前这片走优先车道**（16 块并行，直接同步拉），
        #    顺便把后面 40 片丢进后台单连接预取。
        #    以前是"等后台任务"，而后台一次开几十片 × 8 块，播放器等的这片
        #    被挤到 7 秒以上 → EXO 读超时（电视上显示"连接超时 0KB/s"）。
        _tsc_warm(tok, idx + 1, segs, hdr)
        _tsc_fetch(tok, idx, url, hdr, 16)
        with _TSC_LOCK:
            data = _TSC_DATA.get(key)
    if data is not None:
        body, cr = _tsc_slice(data, rng)
        out = {"Content-Type": "video/mp2t", "Accept-Ranges": "bytes"}
        if cr:
            out["Content-Range"] = cr
        return Response(body, status=206 if (rng and cr) else 200, headers=out)

    # ③ 并发也没拿到（上游 403/过期）：退回单条直取
    if rng:
        hdr["Range"] = rng
    try:
        with urllib.request.urlopen(
                urllib.request.Request(url, headers=hdr), timeout=30) as f:
            data = f.read()
            st = f.status
            ct = f.headers.get("Content-Type") or "video/mp2t"
            cr = f.headers.get("Content-Range")
    except Exception:
        return Response("seg fail", status=502)
    out = {"Content-Type": ct, "Accept-Ranges": "bytes"}
    if cr:
        out["Content-Range"] = cr
    return Response(data, status=st if st in (200, 206) else 200, headers=out)


@bp.route("/hai/h2ts/<b>", methods=["GET", "HEAD"])
def hai_h2ts(b):
    """HLS 分片串成一条连续 MPEG-TS（绕开 PG 对 .m3u8 的强制包装）。"""
    tok = str(b or "").split(".")[0]
    hit = _PP.get(tok)
    if not hit or hit[1] < time.time():
        return Response("expired", status=410)
    m3u_url, hdr = hit[0], dict(hit[2] or {})

    segs = _PPTS.get(tok) or []
    if not segs:
        try:
            with urllib.request.urlopen(
                    urllib.request.Request(m3u_url, headers=hdr), timeout=20) as f:
                txt = f.read().decode("utf-8", "ignore")
        except Exception:
            return Response("m3u8 fetch fail", status=502)
        segs = [ln.strip() for ln in txt.split("\n") if ln.strip().startswith("http")]
        _PPTS[tok] = segs
    if not segs:
        return Response("no segs", status=404)

    if request.method == "HEAD":
        return Response(status=200, headers={"Content-Type": "video/mp2t",
                                             "Accept-Ranges": "none"})

    seg_hdr = {k: v for k, v in hdr.items() if k.lower() != "cookie"}

    def gen():
        for u in segs:
            for attempt in (0, 1):
                try:
                    with urllib.request.urlopen(
                            urllib.request.Request(u, headers=seg_hdr),
                            timeout=30) as f:
                        while True:
                            chunk = f.read(262144)
                            if not chunk:
                                break
                            yield chunk
                    break
                except Exception:
                    if attempt:
                        break

    return Response(gen(), mimetype="video/mp2t",
                    headers={"Cache-Control": "no-cache", "Accept-Ranges": "none"})


@bp.route("/hai/dp/<b>", methods=["GET", "HEAD"])
def hai_dp(b):
    """自包含播放地址。

    APK 侧拦截器认得这个前缀会把它改写成真 CDN 地址（客户端直连、满速）；
    没改的话（比如原版包没打补丁）请求会真落在这里，这里解码后当中转，
    行为等价于 /hai/pp/。`?hdr=1` 只吐 json（调试用）。
    """
    cfg = _dp_unpack(b)
    if not cfg.get("u"):
        return Response("bad token", status=400)
    url, hdr = cfg["u"], cfg.get("h") or {}
    if request.args.get("hdr"):
        # meta.size 给 APK 侧多线程数据源切段用（它据此算 Content-Range）
        return jsonify(url=url, header=hdr,
                       meta={"size": int(cfg.get("size") or 0)})
    return _stream(url, hdr)


@bp.route("/hai/pp/<t>", methods=["GET", "HEAD"])
def hai_pp(t):
    """短链落地。

    无 header 的（本机 9978 直链那种）：默认 302，带 ?json=1 吐纯文本。
    有 header 的（夸克/UC 需要鉴权的 CDN 直链）：本函数亲自当代理转发，
    绝不 302 —— 播放器补不上 Cookie。
    """
    g = _pp_get(t)
    if not g:
        return Response("expired", status=410)
    url, hdr = g[0], g[1]
    if request.args.get("json"):
        # 给 spider 的「换真链」口子。带鉴权的直链把请求头一并吐出去，
        # 格式照 app 自己的内层接口来：`<真链>@Headers={"Cookie":"..."}`
        #（见 tmdb.js 里 9978/proxy 那段注释）。这样播放器能拿直链+头
        # **直连 CDN**，不吃中枢中转的带宽。
        txt = url
        if hdr:
            txt = url + "@Headers=" + json.dumps(hdr, ensure_ascii=False)
        return Response(txt, mimetype="text/plain; charset=utf-8",
                        headers={"Cache-Control": "no-store"})
    if hdr:
        return _stream(url, hdr)
    return redirect(url, code=302)


def _pan_rank(pan: str) -> int:
    p = str(pan or "")
    for k, v in _PAN_RANK.items():
        if k in p:
            return v
    return 9


def _eps_build(vid: str) -> dict:
    """选集数据（含**多源聚合**）—— 详情页选集区 和 「▶ 播放」共用这一份。

    聚合的意义（2026-11-01 海哥）：四个站源各自挂夸克 / UC / 百度，同一部剧
    的集数很少一致 —— 花卷只给百度（解析不了）、木偶·UC 只到 18-19 集、
    蜡笔·夸克给满 40 集。按「季 + 集号」把各源同集并成一组，前端只显示一条
    完整集列表，每集背后挂 N 条线路：点第 N 集永远播第 N 集，不必来回切站源试。

    返回结构：
      sources  —— 原始分源明细（调试 / 兼容用）
      merged   —— [{season,label,eps:[{ep,label,routes:[{site,pan,href}]}]}]
      stats    —— {srcs,eps,routes} 给页面显示「共 N 集 · M 条线路」
    """
    vid = str(vid or "").strip()
    if not vid:
        return {"ok": 0, "error": "缺少 vid", "sources": [], "merged": [], "stats": {}}
    d = tmdbwall.detail(vid, with_plays=False)
    if not d:
        return {"ok": 0, "error": "未找到影片", "sources": [], "merged": [], "stats": {}}
    title = d.get("vod_name") or ""
    year = d.get("vod_year") or ""
    try:
        from core import pan_ep
        r = pan_ep.source_eps(title, year, budget=11.0)
    except Exception as e:
        return {"ok": 0, "error": "%s: %s" % (type(e).__name__, e),
                "sources": [], "merged": [], "stats": {}}

    # 给每一集拼出**可直接起播**的 haiplay 地址（2026-11-01）
    #
    # 链路：网盘 fid + share_fid_token → app 本地代理 127.0.0.1:9978/proxy?...
    #   （logcat 实测就是 app 自己播单集时用的那条 URL）
    #   → 交给 HaiDirect 直连站（assets/js/paidirect.js，二级把 id 直接当播放
    #   地址）→ VideoActivity 起播。这样从我们自己的详情页点「第7集」就能
    #   直接播第 7 集，不必再进站源的选集页手动翻。
    q = urllib.parse.quote
    cap = 400
    total = 0
    for s in r.get("sources") or []:
        link = s.get("link") or ""
        for sea in s.get("seasons") or []:
            for ep in sea.get("eps") or []:
                if total >= cap:
                    ep["href"] = ""
                    continue
                total += 1
                # 自定义线路（data/pan_extra.json）：href 已经自己拼好了，
                # 不用 `playurl()`（那个地址是历史遗留、已停用）。
                if ep.get("custom"):
                    continue
                u = ""
                try:
                    u = pan_ep.playurl(link, ep, title)
                except Exception:
                    u = ""
                if not u:
                    ep["href"] = ""
                    continue
                # 2026-11-05 统一走 push_agent（app 内建特判，不走 HTTP，不会
                # requireNonNull NPE）。id = push://<中枢直链>，ep 真实集号。
                ep["href"] = ("haiplay://play?site=push_agent&id="
                              + q("push://" + _ext_base() + "/hai/pp/" + _pp_make(u), safe="")
                              + "&ep=" + str(int(ep.get("ep") or 1))
                              + "&name=" + q("%s %s" % (title, ep.get("label") or ""), safe=""))

    # ---- 按 (季, 集号) 聚合各站源线路 --------------------------------------
    groups: dict = {}
    srcs_ok = set()
    for s in r.get("sources") or []:
        site, pan = s.get("site") or "", s.get("pan") or ""
        if not (s.get("seasons") and s.get("ok")):
            continue
        got = False
        for sea in s["seasons"]:
            try:
                sn = int(sea.get("season") or 1)
            except (TypeError, ValueError):
                sn = 1
            for ep in sea.get("eps") or []:
                if not ep.get("href"):
                    continue
                got = True
                # ep=0 是「认不出集号的散文件」，同季里按 label 分桶
                try:
                    en = int(ep.get("ep") or 0)
                except (TypeError, ValueError):
                    en = 0
                key = (sn, en, "" if en else str(ep.get("label") or ""))
                g = groups.get(key)
                if g is None:
                    g = {"season": sn, "ep": en,
                         "label": ep.get("label") or "", "routes": []}
                    groups[key] = g
                # name/size 是给「点集 → 线路浮层」用的：用户想看清楚这集到底
                # 有几个网盘、文件多大，再决定用哪一路（2026-11-01）。
                g["routes"].append({"site": site, "pan": pan,
                                    "href": ep["href"],
                                    # 该源原始分享链接（push:// 直入要用）。
                                    # 前端拼 `site=push_agent&id=push://<它>`
                                    # 就能直接开网盘文件列表，跳过站源选集页。
                                    "push": s.get("link") or "",
                                    "name": ep.get("name") or "",
                                    "size": ep.get("size") or 0})
        if got:
            srcs_ok.add(site + "·" + pan)
    for g in groups.values():
        g["routes"].sort(key=lambda x: (_pan_rank(x["pan"]), x["site"]))

    by_sea: dict = {}
    for g in groups.values():
        by_sea.setdefault(g["season"], []).append(g)
    merged = []
    for sn in sorted(by_sea):
        eps = sorted(by_sea[sn], key=lambda x: (x["ep"] or 10 ** 6, x["label"]))
        merged.append({"season": sn,
                       "label": ("第%d季" % sn) if len(by_sea) > 1 else "全集",
                       "eps": eps})

    r["merged"] = merged
    r["stats"] = {"srcs": len(srcs_ok),
                  "eps": sum(len(x["eps"]) for x in merged),
                  "routes": sum(len(e["routes"]) for x in merged for e in x["eps"])}
    # 线路来源（"木偶·UC / 玩偶·夸克"），选集标题右边显示一行，让人一眼知道
    # 这 N 集是从哪几个网盘扫出来的。
    r["srclist"] = sorted(srcs_ok)
    return r


# 同一部片会被并发问两次：详情页一打开，选集区拉 /eps、播放按钮拉 /play。
# 不合并的话站源要被完整抓两遍（花卷/木偶/蜡笔各一次，≈10 秒 ×2），
# 既慢又容易被站源当爬虫。这里做 in-flight 去重：同 vid 并发只跑一次，
# 后来的直接等第一个的结果（2026-11-01）。
_eps_lock = threading.Lock()
_eps_inflight: dict = {}


def _eps_forget(vid: str) -> None:
    """把这部剧的两级缓存都作废（磁盘 eps_cache + panwall 内存缓存）。

    什么时候要作废：缓存里一个能算直链的网盘都没有 —— 最常见的原因是
    `source_eps` 内部 `collect_full(budget=11.0)` 写死 11 秒，夸克那个站
    11 秒内没回来就被丢掉了，只留下百度/115（我们还解析不了）。
    这份残缺结果会卡住整整 30 分钟，用户看到的就是一直「播放无数据」。
    """
    try:
        fp = os.path.join(C.DATA_DIR, "eps_cache", str(vid) + ".json")
        if os.path.exists(fp):
            os.remove(fp)
    except Exception:
        pass
    try:
        from core import panwall as _pw
        with _pw._lock:
            for k in list(_pw._cache):
                if k.startswith("panfull:") or k.startswith("pan:"):
                    _pw._cache.pop(k, None)
    except Exception:
        pass


def _eps_payload(vid: str, force: bool = False) -> dict:
    vid = str(vid or "").strip()
    if not vid:
        return {"ok": 0, "error": "缺少 vid", "sources": [], "merged": [], "stats": {}}
    # ---- 落盘缓存（2026-11-01）------------------------------------------
    # 首次解析一部剧要跑完三个网盘站 + 列目录，实测 20~40 秒。用户滚到选集区
    # 时经常还在转圈，而且**服务一重启内存缓存就没了**。这里把最终结果写进
    # data/eps_cache/<vid>.json，30 分钟内同一个 vid 直接秒开（跨重启有效）。
    hit = None if force else _eps_disk_get(vid)
    if hit is not None:
        return hit
    with _eps_lock:
        slot = _eps_inflight.get(vid)
        mine = slot is None
        if mine:
            slot = {"ev": threading.Event(), "val": None}
            _eps_inflight[vid] = slot
    if not mine:
        slot["ev"].wait(40)
        return slot["val"] or {"ok": 0, "error": "等待超时", "sources": [],
                               "merged": [], "stats": {}}
    try:
        slot["val"] = _eps_build(vid)
        _eps_disk_put(vid, slot["val"])
        return slot["val"]
    finally:
        slot["ev"].set()
        with _eps_lock:
            _eps_inflight.pop(vid, None)


_EPS_CDIR = os.path.join(C.DATA_DIR, "eps_cache")
_EPS_TTL = 1800
# 缓存结构版本（2026-11-01 加）：routes 里新增了 push 字段，旧缓存里没有，
# 读出来前端就拼不出 push:// 直入地址 —— 而且 TTL 内不会自己更新，表现为
# 「改了后端，页面行为却还是老的」。版本号一变，旧缓存自动作废。
_EPS_VER = 2


def _eps_disk_get(vid: str):
    """读 data/eps_cache/<vid>.json；过期、损坏、版本不符都当没命中。"""
    try:
        p = os.path.join(_EPS_CDIR, urllib.parse.quote(vid, safe="") + ".json")
        if not os.path.exists(p):
            return None
        if time.time() - os.path.getmtime(p) > _EPS_TTL:
            return None
        with open(p, "r", encoding="utf-8") as f:
            d = json.load(f)
        if not (isinstance(d, dict) and d.get("merged")):
            return None
        if int(d.get("_v") or 0) != _EPS_VER:
            return None
        return d
    except Exception:
        return None


def _eps_disk_put(vid: str, val) -> None:
    """只有解析出集数才落盘 —— 0 集的结果不要缓存，下次还得重试。"""
    try:
        if not isinstance(val, dict) or not val.get("merged"):
            return
        if not os.path.isdir(_EPS_CDIR):
            os.makedirs(_EPS_CDIR, exist_ok=True)
        p = os.path.join(_EPS_CDIR, urllib.parse.quote(vid, safe="") + ".json")
        tmp = p + ".tmp"
        out = dict(val)
        out["_v"] = _EPS_VER
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False)
        os.replace(tmp, p)
    except Exception:
        pass


@bp.route("/hai/panck", methods=["GET", "POST"])
def hai_panck():
    """网盘 CK 读写口（2026-11-01）。

    GET  —— 只回摘要（每家「是否已配 / 长度 / 尾 4 位」），**绝不回显整条 CK**。
    POST —— body 是 {"quark": "...", "uc": "...", "baidu": "..."}，空串表示删。

    为什么要这个口：PG 的 CK 在 native 层加密（真机反查过，两个包的私有目录
    都只有 WebView 那一个文件），抓不到也不该抓。要做「点完就播」不露 PG
    界面，只能自己存 CK、自己算直链。

    安全：只允许内网来源。公网出口（CF tunnel / gost）上这个路径会被挡；
    就算漏了，GET 也只回长度和尾 4 位，拿不到可用凭据。
    """
    from core import panck
    if request.method == "POST":
        ip = request.remote_addr or ""
        if not (ip.startswith("10.") or ip.startswith("127.")
                or ip.startswith("192.168.") or ip == "::1"):
            return jsonify(ok=0, error="只允许内网写入"), 403
        d = request.get_json(silent=True) or {}
        panck.save(d)
    return jsonify(ok=1, ck=panck.summary())


@bp.route("/hai/poster/eps")
def hai_poster_eps():
    """详情页「选集」数据口（2026-10-31 建，11-01 加多源聚合）

    详情页首屏不等它：页面渲染完由 JS 后台 fetch，拿到就画出季/集按钮，
    点某一集直接 `haiplay://play?site=HaiDirect&id=<定向直链>` —— 走 app
    本地代理（127.0.0.1:9978）直接起播那一集，不再进站源的选集页。

    为什么集数要从网盘侧拿：四个网盘站详情页只给整包分享链接，
    一个「第N集」都没有 —— 集数只存在于分享内的文件名里（`...S01E05...`）。
    详见 core/pan_ep.py 顶部注释。
    """
    return jsonify(**_eps_payload(str(request.args.get("vid") or "").strip()))


@bp.route("/hai/poster/watch", methods=["POST", "GET"])
def hai_poster_watch():
    """「一键追剧」按钮的落地页

    用**普通表单 POST + 302 跳回**而不是 fetch/AJAX：电视 WebView 版本老，
    fetch 不一定有；而且这样点完立刻回到详情页、按钮变成「✓ 已追剧」，
    不需要额外脚本去刷状态。
    """
    from core import watchlist

    vid = str(request.values.get("vid") or "").strip()
    act = str(request.values.get("act") or "add").strip().lower()
    back = str(request.values.get("back") or "/hai/poster/").strip()
    # 只允许跳回本站路径，避免变成开放重定向
    if not back.startswith("/hai/"):
        back = "/hai/poster/"

    if vid:
        try:
            if act == "del":
                watchlist.remove(vid)
            elif act == "toggle":
                watchlist.toggle(vid, tmdbwall.detail(vid, with_plays=False))
            else:
                # 顺手把片名/海报固化进列表：追剧页不依赖 TMDB 也能画
                watchlist.add(vid, tmdbwall.detail(vid, with_plays=False))
        except Exception:
            pass
    return redirect(back)


@bp.route("/hai/watch", methods=["GET", "POST"])
def hai_watch_api():
    """追剧列表的 JSON 接口（手机端/自建页面/调试都能用）

    GET  ?act=list
    GET  ?act=add&vid=m550      / ?act=del&vid=m550   / ?act=toggle&vid=m550
    """
    from core import watchlist

    act = str(request.values.get("act") or "list").strip().lower()
    vid = str(request.values.get("vid") or "").strip()
    try:
        if act == "add" and vid:
            watchlist.add(vid, tmdbwall.detail(vid, with_plays=False))
        elif act == "del" and vid:
            watchlist.remove(vid)
        elif act == "toggle" and vid:
            watchlist.toggle(vid, tmdbwall.detail(vid, with_plays=False))
    except Exception:
        pass
    return jsonify({
        "code": 1,
        "total": len(watchlist.items()),
        "has": watchlist.has(vid) if vid else False,
        "list": watchlist.items(),
    })


@bp.route("/hai/img/<path:rest>")
def hai_img(rest):
    """TMDB 图片代理 —— 电视从局域网取图，别去直连境外图床

    路径与 TMDB 一致：`/hai/img/t/p/w342/xxxx.jpg`。
    为什么需要：电视端直连 images.tmdb.org 一张海报 300KB、2~3 秒起，
    海报墙几十张图就卡成一片灰；走中枢代理后是局域网 + 本地缓存，秒开。
    代理拿不到就 302 回原地址，宁可直接连也别让端上一片空白。
    """
    safe = "/".join(x for x in rest.split("/") if x and x not in (".", ".."))
    if not re.match(r"^t/p/[A-Za-z0-9_]+/[A-Za-z0-9\-_]+\.(jpg|jpeg|png|webp)$", safe or ""):
        return Response("bad path", status=400)

    ext = safe.rsplit(".", 1)[-1].lower()
    name = hashlib.sha1(safe.encode("utf-8")).hexdigest()[:20] + "." + ext
    path = os.path.join(_IMG_DIR, name)
    if os.path.exists(path):
        return send_from_directory(_IMG_DIR, name, max_age=2592000)

    try:
        req = urllib.request.Request(_IMG_HOST + "/" + safe,
                                     headers={"User-Agent": "oktv-hub/1.0"})
        with urllib.request.urlopen(req, timeout=12) as r:
            data = r.read()
        if not data:
            raise ValueError("empty body")
        os.makedirs(_IMG_DIR, exist_ok=True)
        tmp = path + ".part"
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
        _img_gc()
        return Response(data,
                        mimetype="image/jpeg" if ext in ("jpg", "jpeg") else "image/" + ext,
                        headers={"Cache-Control": "public, max-age=2592000"})
    except Exception as exc:                       # noqa: BLE001
        app.logger.info("img proxy fallback %s: %s", safe, exc)
        return Response(status=302, headers={"Location": _IMG_HOST + "/" + safe})


@bp.route("/hai/dbimg/<path:rest>")
def hai_dbimg(rest):
    """豆瓣图床代理 —— 直连必 418，只有带 Referer 才给图（实测）

    路径与豆瓣一致：`/hai/dbimg/view/photo/m_ratio_poster/public/pXXXX.jpg`。
    豆瓣图片散落在 img1/img2/img3/img9.doubanio.com 四个域名，但**路径唯一**，
    所以只按路径做 key，抓图时依次试几个域名（某个 DNS 抽风也能出图）。
    和 TMDB 代理共用同一个磁盘缓存目录（key 前缀 d 区分，不会撞）。
    """
    safe = "/".join(x for x in rest.split("/") if x and x not in (".", ".."))
    # 豆瓣的图有三类路径，都要放行（2026-11-01 加后两类）：
    #   海报  view/photo/m_ratio_poster/public/pXXXX.jpg
    #   剧照  view/photo/l/public/pXXXX.jpg
    #   头像  view/personage/m/public/xxxx.jpg
    #         view/celebrity/m/public/p1588679059.75.jpg  ← 文件名带两个点，必须放行
    if not re.match(r"^(?:view/[A-Za-z0-9_]+/[A-Za-z0-9_]+/[A-Za-z0-9_]+/"
                    r"[A-Za-z0-9_.\-]+\.(?:jpg|jpeg|png|webp)"
                    r"|icon/[A-Za-z0-9_.\-]+\.(?:jpg|jpeg|png|webp))$", safe or ""):
        return Response("bad path", status=400)

    ext = safe.rsplit(".", 1)[-1].lower()
    name = "d" + hashlib.sha1(safe.encode("utf-8")).hexdigest()[:19] + "." + ext
    path = os.path.join(_IMG_DIR, name)
    if os.path.exists(path):
        return send_from_directory(_IMG_DIR, name, max_age=2592000)

    data = b""
    for host in ("https://img3.doubanio.com", "https://img1.doubanio.com",
                 "https://img2.doubanio.com", "https://img9.doubanio.com"):
        try:
            req = urllib.request.Request(host + "/" + safe, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                              "AppleWebKit/537.36 Chrome/138.0.0.0 Safari/537.36",
                "Referer": "https://movie.douban.com/",
            })
            with urllib.request.urlopen(req, timeout=10) as r:
                data = r.read()
            if data:
                break
        except Exception:                          # noqa: BLE001
            continue
    if not data:
        # 抓不到就给个 1x1 透明图：豆瓣裂图时别让整墙变灰块
        return Response(
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06"
            b"\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00"
            b"\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82",
            mimetype="image/png",
            headers={"Cache-Control": "public, max-age=120"})
    try:
        os.makedirs(_IMG_DIR, exist_ok=True)
        tmp = path + ".part"
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
        _img_gc()
    except Exception:                              # noqa: BLE001
        pass
    return Response(data,
                    mimetype="image/jpeg" if ext in ("jpg", "jpeg") else "image/" + ext,
                    headers={"Cache-Control": "public, max-age=2592000"})


def _img_gc(limit: int = 3000) -> None:
    """缓存图片数量上限，超了删最旧的 —— 海报墙翻几页就上千张，别把盘塞满"""
    try:
        if not os.path.isdir(_IMG_DIR):
            return
        files = [(os.path.getmtime(os.path.join(_IMG_DIR, n)), n)
                 for n in os.listdir(_IMG_DIR) if n.endswith((".jpg", ".png", ".webp"))]
        if len(files) <= limit:
            return
        files.sort()
        for _, n in files[:len(files) - limit]:
            try:
                os.remove(os.path.join(_IMG_DIR, n))
            except OSError:
                pass
    except Exception:                              # noqa: BLE001
        pass


def _ext_base() -> str:
    """当前请求对外的真实基地址：`http(s)://host[:port]`，无尾斜杠。

    为什么不能直接用 `request.host_url`（2026-11-01 外网手机实测）：
    请求走到源站时已经被 Cloudflare / gost 脱掉 TLS，Flask 看到的 scheme
    永远是 http。于是接口吐出来的图片来源是 `http://…`，而外网页面是
    https —— 混合内容直接被浏览器/WebView 拦掉，海报墙整墙灰块。同理
    Host 也要认 X-Forwarded-Host（CF 会把源站 host 换掉）。

    本机/局域网直连没有这些头，自动退回 request.scheme / request.host。
    """
    proto = (request.headers.get("X-Forwarded-Proto") or "").split(",")[0].strip().lower()
    if proto not in ("http", "https"):
        proto = request.scheme
    host = (request.headers.get("X-Forwarded-Host") or "").split(",")[0].strip()
    if not host:
        host = request.host
    return "%s://%s" % (proto, host)


@bp.route("/hai/tmdb/api.php/provide/vod/", methods=["GET", "POST"])
def hai_tmdb_vod():
    """「TMDB 海报墙」数据源（苹果CMS V10 兼容）

    app 里配成 type=1 站点即可原生渲染：分类页是海报墙、详情页是深色信息栏，
    数据来自 TMDB、播放源实时聚合暴风/量子/非凡。

    为什么不改豆瓣那个白色网页：页面 URL/UA/注入 CSS 全在 pg.jar 里硬编码，
    jar 受 manifest 哈希红线保护 —— 只能另起一个原生站点替代它。
    """
    try:
        body = tmdbwall.handle({k: v for k, v in request.args.items()}, push=True)
    except Exception as exc:                       # 绝不让 500 把 app 的搜索打断
        app.logger.warning("tmdbwall handle failed: %s", exc)
        body = {"code": 1, "msg": "数据列表", "page": 1, "pagecount": 0,
                "limit": 20, "total": 0, "list": [],
                "class": tmdbwall._classes()}
    # 海报改走中枢代理：端直连 images.tmdb.org 一张图 300KB、2~3 秒，
    # 海报墙几十张就成一片灰。换成「端访问接口时用的那个地址」取图
    # （请求 Host 就是它），局域网内秒开，也不会在外网入口图裂。
    #
    # 2026-10-31 修：这里必须**先复制再改**。list_page 的结果是缓存里的同一个
    # 对象，之前直接就地改写，第一个访问者（比如本机 curl）把地址改写成
    # 127.0.0.1 后就钉在缓存里 30 分钟 —— 电视拿到的全是 127.0.0.1 的图，
    # 整墙海报变灰块。复制一份再改，缓存里永远是原始 TMDB 地址。
    base = _ext_base()
    host = base + "/hai/img"
    hostdb = base
    body["list"] = [dict(it) for it in (body.get("list") or [])]
    for it in body["list"]:
        for k in ("vod_pic", "vod_pic_slide"):
            v = str(it.get(k) or "")
            if v.startswith(_TMDB_IMG):
                it[k] = host + v[len(_TMDB_IMG):]
            elif v.startswith("/hai/dbimg/"):
                # 豆瓣条目：doubanx 已经转成相对代理路径，这里补成绝对地址
                it[k] = hostdb + v

    payload = json.dumps(body, ensure_ascii=False)
    return Response(payload, mimetype="application/json; charset=utf-8",
                    headers={"Cache-Control": "no-cache",
                             "Access-Control-Allow-Origin": "*"})

@bp.route("/hai/lib/<path:p>", methods=list(_WRITE_METHODS))
def hai_lib(p):
    if p.replace("\\", "/").lower() == "tokenm.json":
        return _tokenm(p)
    d = os.path.join(_pkg_dir(), "lib")
    # 2026-11-01：tmdb.js / tmdb.json 里 HOST 是写死的内网 IP，外网手机取到
    # 这个文件后所有数据请求都发不出去（海报墙整页空白）—— 按请求 Host 现算。
    _n = p.replace("\\", "/")
    if _n in ("tmdb.js", "tmdb.json"):
        return _lib_hostfix(os.path.join(d, _n))
    # max_age：包内 lib 资源是静态的（换包才变），给 5 分钟缓存能让设备
    # 重新打开时直接命中本地缓存，少一轮往返。tokenm.json 走上面的动态分支，
    # 不受影响（它是 CK/代理的载体，必须每次现取）。
    return _safe_send(d, p, max_age=300)


def _lib_hostfix(path: str):
    """把 tmdb.js / tmdb.json 里写死的 HOST 换成本次请求的真实基地址。

    电视端 / 局域网走 `http://10.0.0.11:8099` 没问题，但外网手机从穿透域名
    取这个文件时，脚本里的数据源仍是内网 IP —— 一个请求都发不出去，海报墙
    整页空白。这里按 Host 现算：内网请求给内网地址，穿透请求给穿透地址。

    必须 no-store：内容随 Host 变化，一旦被 Cloudflare 边缘缓存，内网版本
    会发给外网用户（反之亦然），等于把问题固化在 CDN 上。
    """
    try:
        with open(path, encoding="utf-8") as f:
            src = f.read()
    except OSError:
        return Response("not found", status=404)
    base = _ext_base()
    src = re.sub(r"var HOST = '[^']*';", "var HOST = '%s';" % base, src, count=1)
    src = re.sub(r'"api"\s*:\s*"[^"]*"',
                 '"api": "%s/hai/tmdb/api.php/provide/vod/"' % base, src, count=1)
    mt = "application/javascript" if path.endswith(".js") else "application/json"
    return Response(src, mimetype=mt + "; charset=utf-8",
                    headers={"Cache-Control": "no-store, no-cache, must-revalidate",
                             "Vary": "X-Forwarded-Proto, X-Forwarded-Host, Host"})


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
    # 回落用渲染口径；正常情况 absbase 自己能从请求 Host 推出正确 base
    base, pfx = absbase.site_base(request, renderbase.current() or "")
    # 中枢根（不含配置根目录）：/gw/<tag>/<path> 是绝对路由，不能用 base 拼
    hub = (absbase.base_from_request(request, renderbase.current() or "")
           + C.PREFIX).rstrip("/")
    lan = C.CK_PUBLIC or _is_lan()
    body = ckvault.render_text(lan=lan, base=base, prefix=pfx, hub=hub)
    etag = '"ck-%d-%s"' % (ckvault.version(), hashlib.md5(body.encode()).hexdigest()[:10])
    if request.headers.get("If-None-Match") == etag:
        return Response(status=304, headers={"ETag": etag,
                                             "X-CK-Version": str(ckvault.version())})
    if request.method == "HEAD":
        return Response("", 200, headers={
            "Cache-Control": _tokenm_cache_ctl(),
            "ETag": etag, "X-CK-Version": str(ckvault.version()),
            "X-CK-Scope": "lan" if lan else "public-masked"})
    return Response(body, mimetype="application/json; charset=utf-8", headers={
        "Cache-Control": _tokenm_cache_ctl(),
        "ETag": etag,
        "X-CK-Version": str(ckvault.version()),
        "X-CK-Scope": "lan" if lan else "public-masked",
        "Access-Control-Allow-Origin": "*",
    })


def _tokenm_cache_ctl():
    """tokenm.json 的缓存策略。

    【为什么按开关切换 —— 2026-10-22】CK 注入与外部中转都关掉之后，
    render_text() 返回的就是**模板原样**（实测 102/102 字段零差异），内容只随
    host 变、不随请求变。但老策略一律 `no-cache`，于是端每启动一次要**现取 8 次**
    tokenm.json（实测 345 次 / 一段时间），每次都是一趟 CF 往返 ≈0.9s —— 这是
    「打开 App 半天不出数据」的主要可控项。改成 max-age 后首次之后的 7 次全走
    设备本地缓存，零往返。

    一旦重新开启 CK 注入 / 中转改写，内容就变成动态的，立刻退回 no-cache。
    """
    if getattr(C, "CK_SYNC_ENABLED", False) or getattr(C, "RELAY_REWRITE", False):
        return "no-cache, must-revalidate"
    return "public, max-age=300"


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
    # js/*.js 只有换包才变，给 5 分钟缓存：端一次启动会重复取同一批脚本，
    # 有 max-age 后 okhttp 直接吃本地缓存，不再走 CF 往返（实测端从不发
    # If-None-Match，所以光有 ETag 不会产生 304，必须给 max-age）。
    return _safe_send(d, p, max_age=300)


@bp.route("/hai/pg.jar")
def hai_jar():
    f = os.path.join(_pkg_dir(), "pg.jar")
    if not os.path.exists(f):
        return "not found", 404
    # 4.6MB，每次启动都全量拉一遍太浪费；换包后最多 5 分钟内端才拿到新版，
    # 而换包本身要求端重启 App 才生效，不冲突。
    return send_from_directory(_pkg_dir(), "pg.jar",
                               mimetype="application/java-archive",
                               max_age=300)


def _safe_send(d, p, max_age=0):
    full = os.path.normpath(os.path.join(d, p))
    if not full.startswith(os.path.normpath(d)):
        return "forbidden", 403
    return send_from_directory(d, p, max_age=max_age)


@bp.route("/lib/<path:p>")
def lib_assets(p):
    if p.replace("\\", "/").lower() == "tokenm.json":
        return _tokenm(p)
    d = os.path.join(C.BASE_DIR, "assets", "lib")
    full = os.path.normpath(os.path.join(d, p))
    if not full.startswith(os.path.normpath(d)):
        return "forbidden", 403          # 目录穿越防护
    return send_from_directory(d, p, max_age=300)


@bp.route("/js/<path:p>")
def js_assets(p):
    d = os.path.join(C.BASE_DIR, "assets", "js")
    full = os.path.normpath(os.path.join(d, p))
    if not full.startswith(os.path.normpath(d)):
        return "forbidden", 403
    return send_from_directory(d, p, max_age=300)


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
        "public_base": renderbase.current(),
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
      ?order=status     老口径（失效优先）；默认按**实时接口顺序**（下行顺序）
    """
    only = (request.args.get("status") or "").strip()
    # 用**实际下发的那一份**（get_config 就是渲染后写进 hai 包的内容）算明细，
    # 否则「是否已标注失效」会恒为 0，与实际配置对不上。
    try:
        cur = get_config()
    except Exception:
        cur = None
    rep = report.sites_report(POOL, jsm=cur if cur else None,
                              order_by=(request.args.get("order") or "config"))
    if request.args.get("brief") in ("1", "true", "yes"):
        rep.pop("items", None)
    elif only:
        rep["items"] = [x for x in rep["items"] if x["status"] == only]
    return jsonify(rep)


def _tail_lines(path, max_bytes=512 * 1024):
    """只读文件尾部若干字节并切行 —— access.jsonl 可以很大，不能整份读。"""
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            if size > max_bytes:
                f.seek(-max_bytes, os.SEEK_END)
                f.readline()          # 丢掉被切断的半行
            data = f.read()
        return data.decode("utf-8", "ignore").splitlines()
    except Exception:
        return []


@bp.route("/api/clientpull")
def api_clientpull():
    """电视/盒子「最后一次拉配置」的时间 —— 判断改完顺序到底生效没有。

    为什么非要有这个：端（FongMi 壳）**不做任何 HTTP 缓存**，
    `VodConfig.load()` 每次都走 `Decoder.getJson(url, TAG)` 真发请求，
    也不发 If-None-Match。真正拦住新版的是 `BaseConfig.ensureLoaded()`
    里的 `if (isLoaded()) return;`：进程活着、sites 非空，就再也不会拉。
    所以「电视有没有拿到新顺序」唯一的判据就是**它有没有来请求**。
    """
    rows = []
    for line in reversed(_tail_lines(F_ACCESS)):
        if "/hai/jsm.json" not in line:
            continue
        try:
            rec = json.loads(line)
        except Exception:
            continue
        ip = str(rec.get("ip") or "")
        ua = str(rec.get("ua") or "")
        # 排除本机自检（服务器自己会以 127.0.0.1 + okhttp 拉一遍）
        if ip.startswith("127.") or ip in ("::1", ""):
            continue
        if ua.lower().startswith(("python", "curl", "wget")):
            continue
        rows.append({"t": rec.get("t"), "ip": ip, "ua": ua[:40],
                     "s": rec.get("s"), "ms": rec.get("ms"), "len": rec.get("len")})
        if len(rows) >= 12:
            break
    last = rows[0] if rows else None
    ago = 0
    if last:
        try:
            ago = int(time.time() - time.mktime(
                time.strptime(str(last["t"]), "%Y-%m-%d %H:%M:%S")))
        except Exception:
            ago = 0
    return jsonify({"ok": True, "now": int(time.time()), "last": last,
                    "ago": ago, "recent": rows,
                    "note": "端不做 HTTP 缓存，每次加载都是真请求；App 进程没重启就不会再拉。"})


@bp.route("/api/siteorder", methods=["GET", "POST"])
def api_siteorder():
    """接口顺序与显隐的手动编辑（维护页「接口明细」卡片的编辑模式）。

    GET   返回当前生效顺序（含隐藏项）+ 配置摘要
    POST  动作：
      {"action":"move","key":"xxx","dir":"up|down"}   上移/下移一位
      {"action":"hide","key":"xxx"}                   隐藏（不再下发）
      {"action":"show","key":"xxx"}                   恢复显示
      {"action":"order","keys":[...]}                 整体覆盖顺序
      {"action":"reset","what":"all|order|hidden"}    重置

    每次动作都会**立即重渲染**再返回（用户要求「完成编辑后实时更新」），
    所以客户端下一次拉 jsm.json 就是新顺序；已经加载过的设备要重启 App
    才会重新拉配置（端侧缓存，见 memory 第 32 条）。
    """
    global _last_render
    if request.method == "GET":
        return jsonify(_siteorder_snapshot())

    body = request.get_json(silent=True) or {}
    act = str(body.get("action") or "").strip()
    key = str(body.get("key") or "").strip()
    msg = ""

    with _SITEORDER_LOCK:
        if act == "move":
            direction = "up" if str(body.get("dir")) in ("up", "1", -1) else "down"
            cur = [str(s.get("key") or "") for s in (get_config() or {}).get("sites") or []]
            if not cur:
                return jsonify({"ok": False, "error": "当前配置为空，先渲染一次"}), 409
            new = siteorder.move(key, direction, cur)
            if new is None:
                return jsonify({"ok": False, "error": "接口不在当前列表里：%s" % key}), 404
            msg = "已%s移一位" % ("上" if direction == "up" else "下")
        elif act in ("hide", "show"):
            if not key:
                return jsonify({"ok": False, "error": "缺少 key"}), 400
            if act == "hide":
                siteorder.hide(key)
                msg = "已隐藏，客户端接口列表中不再出现"
            else:
                siteorder.show(key)
                msg = "已恢复显示"
        elif act == "order":
            keys = [str(k) for k in (body.get("keys") or [])]
            if not keys:
                return jsonify({"ok": False, "error": "keys 不能为空"}), 400
            siteorder.set_order(keys)
            msg = "顺序已保存"
        elif act == "reset":
            what = str(body.get("what") or "all")
            siteorder.reset(what)
            msg = "已重置（%s），可点「撤销上次重置」恢复" % what
        elif act == "undo":
            r = siteorder.undo()
            if not r:
                return jsonify({"ok": False, "error": "没有可撤销的记录"}), 404
            msg = "已撤销，恢复到 顺序 %d 项 / 隐藏 %d 项" % (
                len(r.get("order") or []), len(r.get("hidden") or []))
        else:
            return jsonify({"ok": False, "error": "未知 action：%s" % act}), 400

        # 立即重渲染 —— 「实时更新在线接口排序和状态」
        try:
            do_render()
        except Exception as e:
            return jsonify({"ok": False, "error": "重渲染失败：%s: %s"
                            % (type(e).__name__, e)}), 500

    snap = _siteorder_snapshot()
    snap.update({"ok": True, "message": msg})
    return jsonify(snap)


def _siteorder_snapshot():
    """当前生效的顺序 + 隐藏名单 + 每条的展示信息（前端一次拿够）"""
    cur = []
    try:
        cur = (get_config() or {}).get("sites") or []
    except Exception:
        cur = []
    keys = [str(s.get("key") or "") for s in cur]
    d = siteorder.load()
    hk = set(d["hidden"])
    rep = report.sites_report(POOL, jsm={"sites": cur} if cur else None)
    return {
        "ok": True,
        "total": len(keys),
        "hidden_count": len(hk),
        "order_count": len(d["order"]),
        "updated": d["updated"],
        "order": d["order"],
        "hidden": d["hidden"],
        "history": siteorder.history(),
        "current": [{"key": x.get("key"), "name": x.get("name"),
                     "hidden": bool(x.get("hidden")), "status": x.get("status"),
                     "latency": x.get("latency") or 0, "tag": x.get("tag") or "",
                     "usable": x.get("usable") or 0, "total": x.get("total") or 0}
                    for x in (rep.get("items") or [])],
    }


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
    # 【2026-10-18 废弃】CK 多端同步停用：上报不再入库，免得造成"以为在同步"的错觉。
    if not getattr(C, "CK_SYNC_ENABLED", False):
        return jsonify({
            "ok": False, "deprecated": True,
            "error": "CK 多端同步已废弃：请直接在 OK影视 里扫码登录，凭据保存在端上 "
                     "/storage/emulated/0/TV/.<渠道>_user.pg 并只对该设备生效。",
        }), 410
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
    """【2026-10-18 废弃】原来是「在中枢注入代理地址下发到 tokenm」。

    废弃原因：tokenm.json 由中枢下发，注入的值会盖掉端上用户自己在 App
    「网盘及弹幕配置」里填的代理。PG 原生用自己配的就能正常起播，所以恢复让
    端上的原设置完全生效 —— 这个接口不再写入任何东西。
    """
    if request.method == "GET":
        return jsonify({"ok": True, "deprecated": True,
                        "proxy": "",
                        "note": "CK 同步与代理注入已废弃；代理请在 App 里配置，"
                                "由 PG 原版设置直接生效"})
    return jsonify({"ok": False, "deprecated": True,
                    "error": "已废弃：中枢不再向 tokenm 注入任何字段。"
                             "代理请在 OK影视 App「网盘及弹幕配置」里填写。"}), 410


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
        resp = Response(f.read(), mimetype="text/html; charset=utf-8")
    # 监控页必须实时：禁止浏览器/代理缓存旧页面
    # （踩过的坑：卡片明明上线了，浏览器吃缓存 ⇒ 用户以为没上线）
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    resp.headers["Pragma"] = "no-cache"
    return resp


# ----------------------------------------------------------------------------
# 路由：签到一条龙 · CK 提交与体检
# ----------------------------------------------------------------------------
# 【2026-10-23 归位到源码树】这三条路由原来是用补丁脚本打上去的
# （/opt/oktv-hub/tools/patch_checkin_ck.py），而部署会整体覆盖 app.py
# ⇒ 每部署一次就丢一次，表现为「签到列表一直读取中…」（前端 fetch 404）。
# 现在进源码树，部署即携带。


@bp.route("/api/checkin/sites")
def api_checkin_sites():
    """签到站点清单（是否已配置 + 脱敏预览）"""
    return jsonify({"sites": checkin_ck.sites_payload()})


@bp.route("/api/checkin/save", methods=["POST"])
def api_checkin_save():
    """保存某站点 CK 到 DailyCheckIn 配置（/opt/dailycheckin/config/config.json）"""
    data = request.get_json(silent=True) or {}
    try:
        return jsonify(checkin_ck.save_ck(data.get("key", ""),
                                          data.get("value", "")))
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


@bp.route("/api/checkin/verify")
def api_checkin_verify():
    """CK 体检：?key=TIEBA 单站，否则全部"""
    key = request.args.get("key")
    if key:
        return jsonify(checkin_ck.verify_one(key))
    return jsonify({"results": checkin_ck.verify_all()})


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


@bp.route("/api/endpoints", methods=["GET", "POST"])
def api_endpoints():
    """对外入口地址清单（内网 → FRP → CF）+ 测速 + 渲染口径。

    GET  → {lan, list:[…], bench:{…}, render:{…}}
    POST → {action: add|remove|reset|set_lan|render_base|bench}

    **三条链路必须分清，别再混**：
      · `list`    清单 = 「客户端该填哪个地址」→ 决定电视拉包快慢
      · `bench`   测速 = 「现在哪条最快」的实测
      · `render`  渲染口径 = 「jsm.json 里 /gw/<TAG>/ 用哪个域名」
        它**只影响 gateway/dual 模式**，当前 mode=direct 下发配置里一个
        /gw/ 都没有，所以对客户端速度零影响（详见 core/renderbase.py）。
    """
    if request.method == "GET":
        rows = endpoints.list_ordered()
        bench = None
        if request.args.get("bench") in ("1", "true", "yes"):
            bench = epbench.latest()
        else:
            bench = store.load(C.F_EPBENCH, None)
        return jsonify({
            "ok": True,
            "list": rows,
            "count": len(rows),
            "file": endpoints.F_ENDPOINTS,
            "bench": bench,
            "render": renderbase.info(),
            "render_base": renderbase.current(),
            "render_bases": list(getattr(C, "PUBLIC_BASES", []) or []),
        })

    body = request.get_json(silent=True) or {}
    act = str(body.get("action") or "add").lower()
    if act == "add":
        res = endpoints.add(body.get("url"), kind=str(body.get("kind") or ""),
                            label=str(body.get("label") or ""))
    elif act in ("remove", "del", "delete"):
        res = endpoints.remove(body.get("url"))
    elif act == "set_lan":
        res = endpoints.set_lan(body.get("url"))
    elif act == "reset":
        res = endpoints.reset()
    elif act == "render_base":
        # url 空 = 交回自动/默认；mode: 0关闭 1仅故障切换 2最快优先
        before = renderbase.current()
        res = renderbase.set_manual(body.get("url"), reason="维护页手动设置")
        if res.get("ok") and body.get("mode") is not None:
            d = renderbase.load()
            d["mode"] = int(body.get("mode") or 0)
            renderbase.save(d)
            res["state"] = d
        if res.get("ok") and renderbase.current() != before:
            # 口径一改立刻重渲染，让下一次下发就带上（direct 模式下内容不变，
            # 但 gateway/dual 下必须重渲染，否则要等 45s 后台线程兜底）
            do_render()
        res["render"] = renderbase.info()
    elif act == "bench":
        rounds = body.get("rounds")
        res = {"ok": True, "bench": epbench.run(
            rounds=int(rounds) if rounds else None)}
        res["render"] = renderbase.info()
    elif act == "render_decide":
        # 手动触发一次「按测速结果决定要不要切」
        bench = epbench.run()
        res = {"ok": True, "decide": renderbase.decide(bench), "bench": bench}
    else:
        return jsonify({"ok": False, "error": "未知 action：%s" % act}), 400
    if res.get("ok"):
        store.log_event("endpoints", "入口地址变更（%s）：%s" % (act, body.get("url") or ""))
    return jsonify(res), (200 if res.get("ok") else 400)


@bp.route("/api/upstream")
def api_upstream():
    """上游源体检：源清单 + 最近一次同步的「单源贡献度」。

    用户问「为什么一直没有新接口进来」时，答案必须可查 ——
    这张表能直接看出：哪个源还活着、覆盖几个 TAG、本次带来几个新域名。
    """
    st = store.get_state()
    # 上游里「PG 没有的站点」登记表（只登记不注入，见 sync.collect_new_sites）
    new_sites = {}
    try:
        p = os.path.join(C.DATA_DIR, "new_sites.json")
        if os.path.exists(p):
            with open(p, "r", encoding="utf-8") as f:
                new_sites = json.load(f)
    except Exception:
        new_sites = {}
    # 手动同步（/api/sync）写 last_sync_stat；每日巡检写 last_daily_stat.sync。
    # 两者都要认，否则「刚点完同步却看到空表」。
    lss = dict(st.get("last_sync_stat")
               or (st.get("last_daily_stat") or {}).get("sync") or {})
    # 字段名对不上会静默出空表：后端叫 source_detail，页面读的是 detail。
    # 两个都给，避免以后再踩。
    if "source_detail" in lss and "detail" not in lss:
        lss["detail"] = lss["source_detail"]

    # 饭太硬导航页快照 + 各直连源接口地址变化（站源域名 / 接口地址有无更新）
    nav_sum, feed_st = {}, {}
    try:
        nav_sum = navwatch.summary()
    except Exception as e:
        nav_sum = {"error": "%s: %s" % (type(e).__name__, e)}
    try:
        feed_st = navwatch.feed_state()
    except Exception as e:
        feed_st = {"error": "%s: %s" % (type(e).__name__, e)}

    # 新站源的「能不能加入」静态判定（能力闸，秒回，不联网）
    # 与 /api/newsite 同源 —— 页面一次拉齐，不必再发一次请求
    try:
        newsite = addsite.candidates()
    except Exception as e:
        newsite = {"items": [], "added": [], "jar": {},
                   "error": "%s: %s" % (type(e).__name__, e)}

    return jsonify({
        "ok": True,
        "repos": list(getattr(C, "UPSTREAM_REPOS", []) or []),
        "feeds": [{"name": f.get("name"), "urls": f.get("urls") or [],
                   "dead": bool(f.get("dead")), "why": f.get("why", "")}
                  for f in (getattr(C, "UPSTREAM_FEEDS", []) or [])],
        "urls": list(getattr(C, "UPSTREAM_URLS", []) or []),
        "dead": list(getattr(C, "UPSTREAM_DEAD", []) or []),
        "harvest_sources": list(getattr(C, "HARVEST_SOURCES", []) or []),
        "pkg_mirrors": list(getattr(C, "UPSTREAM_PKG_MIRRORS", []) or []),
        "last_sync": st.get("last_sync", 0),
        "last_sync_source": st.get("last_sync_source", ""),
        "last_sync_stat": lss,
        "new_sites": new_sites,
        "newsite": newsite,
        "nav": nav_sum,
        "feed_state": feed_st,
        "timeout": getattr(C, "UPSTREAM_TIMEOUT", 10),
    })


@bp.route("/api/newsite")
def api_newsite():
    """上游新站源清单 + 每条的「能不能加入」判定

    加入要过三道闸（见 core/addsite.py）：
      ① 能力闸 —— 本包 jar 里有没有对应的解析类（实测 217 个上游 csp_ 类
         只有 20 个在本包里，没有的加进去端上必然报「解析器不存在」）；
      ② 探测闸 —— 接口现在通不通；
      ③ 落盘闸 —— 写进 data/added_sites.json，由 patch.build() 注入。
    这里只做**静态**判定（不联网），点了「加入」才真连一次。
    """
    try:
        d = addsite.candidates()
    except Exception as e:
        return jsonify({"ok": False, "error": "%s: %s" % (type(e).__name__, e)}), 500
    return jsonify({"ok": True, "items": d.get("items") or [],
                    "added": d.get("added") or [],
                    "jar": d.get("jar") or {}})


@bp.route("/api/newsite/add", methods=["POST"])
def api_newsite_add():
    """把一个上游新站源加入 PG 包（过三道闸）

    body: {"key": "...", "force": false}
    force=true 用于「探测失败但仍想强行加入」（探测不通时返回 can_force=true）
    """
    body = request.get_json(silent=True) or {}
    key = str(body.get("key") or "").strip()
    if not key:
        return jsonify({"ok": False, "error": "缺少 key"}), 400
    try:
        res = addsite.add(key, force=bool(body.get("force")))
    except Exception as e:
        return jsonify({"ok": False, "error": "%s: %s" % (type(e).__name__, e)}), 500
    if res.get("ok"):
        # 立刻并入域名池并重渲染 —— 否则端上要等到下一轮巡检才看到新站
        addsite.seed_pool(POOL)
        _flush()
        do_render()
        res["rendered"] = True
    return jsonify(res), (200 if res.get("ok") else 400)


@bp.route("/api/newsite/remove", methods=["POST"])
def api_newsite_remove():
    """把已加入的新站源移出（不动原版，也不改包目录）"""
    body = request.get_json(silent=True) or {}
    key = str(body.get("key") or "").strip()
    if not key:
        return jsonify({"ok": False, "error": "缺少 key"}), 400
    try:
        res = addsite.remove(key)
    except Exception as e:
        return jsonify({"ok": False, "error": "%s: %s" % (type(e).__name__, e)}), 500
    if res.get("ok"):
        # remove() 会把 TAG 一起返回 —— 站点移出 added_sites.json 后就查不到
        # key → TAG 的对应关系了，池子里的条目必须靠这个 TAG 清
        addsite.drop_pool(POOL, tags=[res.get("tag")])
        _flush()
        do_render()
        res["rendered"] = True
    return jsonify(res), (200 if res.get("ok") else 400)


@bp.route("/api/newsite/probe", methods=["POST"])
def api_newsite_probe():
    """只做连通性探测，不加入（用户在点「加入」前先探一下）

    判定口径必须与 /api/newsite 清单一致 —— 清单说「不可加」而这里说
    「可以加」，用户点下去只会被拒。所以判重、能力闸、探测闸三步都走
    addsite 的公共函数，不在这里另写一套。
    """
    body = request.get_json(silent=True) or {}
    key = str(body.get("key") or "").strip()
    if not key:
        return jsonify({"ok": False, "error": "缺少 key"}), 400
    cand = None
    for s in (addsite._new_sites() or []):
        if str(s.get("key") or "") == key:
            cand = s
            break
    if not cand:
        return jsonify({"ok": False, "error": "上游清单里没有这个站点"}), 404
    api = str(cand.get("api") or "")
    dkind, dreason = addsite.dup_check(key, api)
    if dkind:
        return jsonify({"ok": True, "static": {"ok": False, "kind": "dup",
                                               "reason": dreason, "warnings": []},
                        "probe": {}, "can_add": False, "dup": True})
    site = addsite.resolve_site(cand)
    if not site:
        return jsonify({"ok": False, "error": "拿不到这个站点的完整定义，先重跑一次「上游同步」"}), 404
    site = addsite._rewrite_token(site)
    chk = addsite.static_check(site)
    pr = addsite.probe_site(site)
    return jsonify({"ok": True, "static": chk, "probe": pr,
                    "can_add": bool(chk.get("ok") and pr.get("ok"))})


@bp.route("/api/pkg/status")
def api_pkg_status():
    """当前线上包是谁、什么时候换的、有没有备份可回滚"""
    return jsonify(pkgupdate.status())


@bp.route("/api/pkg/history")
def api_pkg_history():
    return jsonify(pkgupdate.history(int(request.args.get("limit") or 20)))


@bp.route("/api/pkg/compare", methods=["POST"])
def api_pkg_compare():
    """只读比对：新包 vs 当前 baseline（不碰线上任何文件）

    body: {"path": "/root/pg-inbox/pg.xxx.zip"} 或 {"url": "https://..."}
    """
    body = request.get_json(silent=True) or {}
    path = str(body.get("path") or "").strip()
    url = str(body.get("url") or "").strip()

    if url and not path:
        path = _pkg_fetch_url(url)
        if not path:
            return jsonify({"ok": False, "error": "下载失败"}), 502

    if not path:
        return jsonify({"ok": False, "error": "缺少 path 或 url"}), 400
    if not pkgupdate.allowed_path(path):
        return jsonify({"ok": False, "error": "路径不在允许范围（只读 /root/pg-inbox 等）"}), 403
    if not os.path.isfile(path):
        return jsonify({"ok": False, "error": "文件不存在：%s" % path}), 404

    return jsonify(pkgupdate.compare(path))


@bp.route("/api/pkg/update", methods=["POST"])
def api_pkg_update():
    """执行更新：全量解压覆盖 + 重算清单 + 重渲染 + core_ok 闸门 + 失败自动回滚

    body: {"path": "/root/pg-inbox/pg.xxx.zip", "force": false, "reason": "tg:群名"}
    """
    g = _pkg_guard()
    if g:
        return g

    body = request.get_json(silent=True) or {}
    path = str(body.get("path") or "").strip()
    if not path:
        return jsonify({"ok": False, "error": "缺少 path"}), 400
    if not pkgupdate.allowed_path(path):
        return jsonify({"ok": False, "error": "路径不在允许范围"}), 403
    if not os.path.isfile(path):
        return jsonify({"ok": False, "error": "文件不存在：%s" % path}), 404

    res = pkgupdate.apply(path, reason=str(body.get("reason") or ""),
                          force=bool(body.get("force")), pool=POOL)
    code = 200 if res.get("ok") else 500
    return jsonify(res), code


@bp.route("/api/pkg/rollback", methods=["POST"])
def api_pkg_rollback():
    """回滚到某个备份快照"""
    g = _pkg_guard()
    if g:
        return g
    body = request.get_json(silent=True) or {}
    bid = str(body.get("backup") or "").strip()
    if not bid:
        return jsonify({"ok": False, "error": "缺少 backup（用 /api/pkg/status 查）"}), 400
    rb = pkgupdate._restore(bid)
    if rb.get("ok"):
        try:
            pkgupdate.libfill.fill()
            patch.regenerate(POOL)
        except Exception as e:
            rb["render_error"] = "%s: %s" % (type(e).__name__, e)
        store.log_event("pkg", "已回滚 pg 包到快照 %s" % bid)
    return jsonify(rb), (200 if rb.get("ok") else 500)


@bp.route("/api/pkg/upload", methods=["POST"])
def api_pkg_upload():
    """手动兜底通道：网页直传 zip（TG 完全不通时用）

    multipart 字段名 file；带 auto=1 时校验通过即直接更新。
    """
    g = _pkg_guard()
    if g:
        return g
    f = request.files.get("file")
    if not f or not f.filename:
        return jsonify({"ok": False, "error": "没有收到文件（字段名 file）"}), 400

    from werkzeug.utils import secure_filename
    inbox = "/root/pg-inbox"
    os.makedirs(inbox, exist_ok=True)
    name = secure_filename(os.path.basename(f.filename)) or "upload.zip"
    dst = os.path.join(inbox, name)
    f.save(dst)
    size = os.path.getsize(dst)

    ins = pkgupdate.inspect(dst)
    out = {"ok": ins["ok"], "path": dst, "size": size,
           "size_text": pkgupdate._human(size), "inspect": ins}
    if not ins["ok"]:
        return jsonify(out), 400

    if request.form.get("auto") in ("1", "true", "yes"):
        res = pkgupdate.apply(dst, reason="web-upload", pool=POOL)
        out["update"] = res
        out["ok"] = bool(res.get("ok"))
        return jsonify(out), (200 if out["ok"] else 500)
    return jsonify(out)


def _pkg_fetch_url(url):
    """把 http(s) 链接下到 /root/pg-inbox（只允许 http/https，5 分钟超时）"""
    if not url.lower().startswith(("http://", "https://")):
        return None
    import requests
    inbox = "/root/pg-inbox"
    os.makedirs(inbox, exist_ok=True)
    name = url.split("?")[0].rstrip("/").split("/")[-1] or ("pkg-%d.zip" % int(time.time()))
    if not name.lower().endswith(".zip"):
        name += ".zip"
    dst = os.path.join(inbox, name)
    try:
        r = requests.get(url, timeout=300, stream=True, headers={"User-Agent": C.UA})
        r.raise_for_status()
        with open(dst, "wb") as fp:
            for chunk in r.iter_content(1 << 20):
                if chunk:
                    fp.write(chunk)
        return dst
    except Exception as e:
        store.log_event("error", "下载更新包失败 %s: %s: %s" % (url, type(e).__name__, e))
        return None


def _pkg_guard():
    """高危操作的来源校验：只允许局域网 / 本机（/api 公网入口已被 CF 拦，这是第二道）"""
    ip = _client_ip()
    if not _is_lan(ip):
        return jsonify({"ok": False, "error": "仅允许局域网/本机调用（来源 %s）" % ip}), 403
    return None


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
# 后台预热（2026-11-03）：类目列表 + 详情元数据 + 焦点选集
# ----------------------------------------------------------------------------
# 只读缓存，多 worker 各跑一份也安全（重复请求会被 TMDB 自己的缓存吸收）。
# 目的：类目来回切不再等 0.3~1.5 秒；详情页第二次打开秒开。
try:
    warm.set_eps_loader(_eps_payload)
    warm.start()
except Exception as _e:                                  # noqa: BLE001
    try:
        store.log_event("boot", "warm 启动失败: %s" % _e)
    except Exception:
        pass


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
        # 配置渲染常驻后台：请求路径不再承担 378ms 的渲染成本
        threading.Thread(target=_render_keeper, daemon=True).start()
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
