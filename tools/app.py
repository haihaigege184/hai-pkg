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
import json
import os
import sys
import threading
import time

from flask import Blueprint, Flask, Response, jsonify, request, send_from_directory

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core import config as C          # noqa: E402
from core import gateway, health, libfill, notify, patch, sources as S, store, sync, verify  # noqa: E402

app = Flask(__name__, static_folder=None)
bp = Blueprint("oktv", __name__)

F_ACCESS = os.path.join(C.LOG_DIR, "access.jsonl")


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
            "ip": request.headers.get("X-Forwarded-For") or request.remote_addr,
            "m": request.method, "p": request.path, "s": resp.status_code,
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
    return resp

POOL = {}                 # { TAG: {tag, api, proxy, rules, name, domains:[...]} }
REGISTRY = {}
_last_render = {"ts": 0, "jsm": None}
F_HEALTH_STATE = os.path.join(C.DATA_DIR, "health_state.json")   # TAG -> 上次存活数

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
    gateway.set_pool(POOL)
    _flush()
    store.log_event("boot", f"中枢启动，登记接口源 {len(REGISTRY)} 个，域名池 {len(POOL)} 组"
                            + (f"，清理私有/占位地址 {dropped} 条" if dropped else ""))
    # 补全 lib 资源（缺失时播放器会对 404 疯狂重试）
    libfill.fill()


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
    """统计每个 TAG 当前「可用」域名数

    只看 status=alive 不够：停放页/密码页也是 200。这里只认 q>=2（带站点特征），
    这样通知里的「失效」才是真的影响观看。
    """
    return {tag: health.usable_count(ent) for tag, ent in POOL.items()}


def _name_of(tag):
    return (POOL.get(tag, {}) or {}).get("name") or (REGISTRY.get(tag, {}) or {}).get("name", "")


def _sites_of(tag):
    return (REGISTRY.get(tag, {}) or {}).get("sites", []) or [tag]


def _apply_health_notify():
    """对比上次巡检结果，接口由「有可用」变「无可用」→ 告警，反之 → 恢复通知"""
    now_map = _tag_alive()
    prev = store.load(F_HEALTH_STATE, {}) or {}
    results = {"failed": [], "recovered": []}
    for tag, n in now_map.items():
        p = prev.get(tag, {}).get("alive")
        if n == 0 and (p is None or p > 0):
            results["failed"].append(tag)
            if C.NOTIFY_ON_FAIL:
                notify.send(notify.fmt_fail(tag, _name_of(tag), _sites_of(tag)),
                            dedup_key=f"fail:{tag}", dedup_sec=3600)
        elif n > 0 and p == 0:
            best = health.pick_best(POOL.get(tag) or {})
            results["recovered"].append(tag)
            if C.NOTIFY_ON_FIX:
                notify.send(notify.fmt_fixed(tag, _name_of(tag), best or "-"),
                            dedup_key=f"fix:{tag}", dedup_sec=600)
    store.save(F_HEALTH_STATE, {t: {"alive": n, "ts": int(time.time())}
                                for t, n in now_map.items()})
    return results


def do_check(only_dead=False, tags=None, notify_changes=True):
    disc, res = health.check_all(POOL, only_dead=only_dead, tags=tags)
    _flush()
    store.set_state(last_health=int(time.time()))
    alive = sum(1 for r in res if r["status"] == "alive")
    store.log_event("health", f"巡检 {len(res)} 个地址，存活 {alive} 个")
    changes = _apply_health_notify() if notify_changes else {"failed": [], "recovered": []}
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
    check_stat = do_check()
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


@bp.route("/jsm.json")
def jsm_json():
    jsm = get_config()
    body = json.dumps(jsm, ensure_ascii=False, indent=1)
    return Response(body, mimetype="application/json; charset=utf-8",
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
    jsm = get_config()
    body = json.dumps(jsm, ensure_ascii=False, indent=1)
    return Response(body, mimetype="application/json; charset=utf-8",
                    headers={"Cache-Control": "no-cache",
                             "Access-Control-Allow-Origin": "*"})


@bp.route("/hai/version.json")
def hai_version():
    return version_json()


@bp.route("/hai/lib/<path:p>")
def hai_lib(p):
    d = os.path.join(_pkg_dir(), "lib")
    return _safe_send(d, p)


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


@bp.route("/gw/<tag>/", defaults={"path": ""},
           methods=["GET", "POST", "PUT", "DELETE", "HEAD", "OPTIONS", "PATCH"])
@bp.route("/gw/<tag>/<path:path>",
           methods=["GET", "POST", "PUT", "DELETE", "HEAD", "OPTIONS", "PATCH"])
def gw(tag, path):
    raw, code, err, hdrs = gateway.proxy(
        tag, path, request.method, dict(request.headers),
        request.get_data() if request.method in ("POST", "PUT", "PATCH") else None,
        request.query_string.decode("utf-8", "ignore"))
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
    })


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
    """一键检测修复：同步上游 -> 全池巡检 -> 重新生成 hai 包（SEA 插件「修复接口」调这个）"""
    body = request.get_json(silent=True) or {}
    return jsonify(do_repair(reason=body.get("reason", "api")))


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
