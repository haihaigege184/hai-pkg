"""配置生成：把基线 jsm.json 中失效的站点地址替换成「当前可用」的地址

两种模式
  direct  —— 直接把 ext 里的 baseURL 写成当前最优存活域名。
             优点：链路最短、最贴近原生体验。缺点：APP 若缓存了配置，域名再漂移就会失效。
  gateway —— 把 baseURL 写成中枢的网关地址 http://<hub>/gw/<TAG>/，
             由中枢实时反代到当前最优域名。
             优点：配置一次写死，之后域名怎么漂移都不用改 APP。缺点：多一跳。

默认 dual（推荐）：direct 为主，网关作为同名备份站点附带输出，二者都写进配置。
"""
import json
import os

from . import config as C
from . import health
from . import sources as S
from . import store

_OFFLINE_TAG = "OFFLINE"


def load_baseline():
    with open(C.BASELINE_JSM, "r", encoding="utf-8-sig") as f:
        return json.load(f)


def gateway_base(tag):
    pub = C.PUBLIC_BASE
    if not pub:
        return None
    return f"{pub}/gw/{tag}/"


def _health_state():
    """读巡检状态（含每 TAG 的连续失败轮数），用于失效标注的滞后判定"""
    f = getattr(C, "F_HEALTH_STATE", None)
    if not f:
        return {}
    try:
        return store.load(f, {}) or {}
    except Exception:
        return {}


def build(pool, mode="dual", public_base=None):
    """生成修复后的 jsm.json 内容（dict）

    mode: direct | gateway | dual
    """
    jsm = load_baseline()
    if public_base:
        C.PUBLIC_BASE = public_base.rstrip("/")

    hstate = _health_state()
    need = max(1, int(getattr(C, "OFFLINE_CONFIRM_ROUNDS", 2)))

    changed, offline, clone_sites = [], [], []
    for s in jsm.get("sites", []):
        ext = s.get("ext")
        if not isinstance(ext, str):
            continue
        info = S.parse_ext(ext)
        if not info or not info["tag"]:
            continue
        tag = info["tag"]
        ent = pool.get(tag)
        detail = health.pick_best_detail(ent) if ent else None
        # 只认 q>=2（带站点特征、或网盘聚合站的正常首页）。q1 是门户页/空壳站/
        # 人机校验页，用它去替换原地址反而是降级，所以宁可不改。
        best = detail["url"] if detail and (detail.get("quality") or 0) >= 2 else None
        # 「有东西活着」：q>=1 也算。它不用于替换地址，但用于**免于误判失效**
        # —— 人机校验页/前端空壳证明站点没死，只是我们这种数据中心 IP 探不到真相。
        alive_any = health.survivable_count(ent) if ent else 0

        if mode == "gateway":
            gb = gateway_base(tag)
            if gb:
                s["ext"] = S.set_ext_base(ext, gb)
                changed.append({"tag": tag, "key": s.get("key"), "to": gb})
            continue

        if best:
            if best.rstrip("/") + "/" != S.norm_base(info["base"]):
                changed.append({"tag": tag, "key": s.get("key"),
                                "from": info["base"], "to": best})
            s["ext"] = S.set_ext_base(ext, best)
            if mode == "dual":
                gb = gateway_base(tag)
                if gb:
                    clone = json.loads(json.dumps(s, ensure_ascii=False))
                    clone["key"] = s.get("key", tag) + "_GW"
                    clone["name"] = (s.get("name") or tag) + "·网关"
                    clone["ext"] = S.set_ext_base(ext, gb)
                    clone_sites.append(clone)
        elif alive_any > 0:
            # 有存活地址但质量存疑 —— 保留原地址，也**不判失效、不告警**。
            # 这一档专门用来吸收「人机校验页 / 前端空壳」这类探不到真相的情况。
            store.log_event("health",
                            f"{tag}({s.get('key')}) 有 {alive_any} 条地址存活但质量存疑"
                            f"（人机校验/空壳），保留原地址，不判失效")
        else:
            # 本轮没有可用地址 —— 但不立刻打「⚠️失效」。
            # 域名池本身会抖（偶发超时/5xx），一抖就改名字会让用户看到接口名
            # 一会儿一个样（用户反馈的「反复失效」「标注失效但实测能用」）。
            # 只有**连续 need 轮**都无可用才认定真失效。
            streak = int((hstate.get(tag) or {}).get("streak") or 0)
            confirmed = streak >= need
            offline.append({"tag": tag, "key": s.get("key"),
                            "streak": streak, "confirmed": confirmed})
            # 标注写进 name 会破坏与原版的逐字一致性，且端缓存后「恢复了也不消失」
            # （用户 3 号反馈）。默认只记事件、不改 name；确需直观看失效时用
            # OKTV_MARK_NAMES=1 临时打开。失效状态由 /api/sites、/live、通知表达。
            if confirmed and getattr(C, "MARK_NAMES", False):
                s["name"] = (s.get("name") or "") + "⚠️失效"
                store.log_event("offline",
                                f"{tag}({s.get('key')}) 连续 {streak} 轮无可用地址，已标注失效")
            elif confirmed:
                store.log_event("offline",
                                f"{tag}({s.get('key')}) 连续 {streak} 轮无可用地址，"
                                f"判定失效（未写入 name，见 /api/sites）")
            else:
                store.log_event("health",
                                f"{tag}({s.get('key')}) 本轮无可用地址（连续 {streak} 轮，"
                                f"未达 {need} 轮阈值，暂不标注失效）")

    if mode == "dual":
        jsm["sites"] = jsm.get("sites", []) + clone_sites

    # 合并 overrides：手工新增/删除/改名
    jsm = _apply_overrides(jsm)

    stat = {"changed": changed, "offline": offline, "clones": len(clone_sites)}
    return jsm, stat


def _apply_overrides(jsm):
    ov = store.load(C.F_OVERRIDES, {}) or {}
    drop = set(ov.get("drop_keys", []))
    if drop:
        jsm["sites"] = [s for s in jsm.get("sites", []) if s.get("key") not in drop]
    for extra in ov.get("add_sites", []):
        if not any(s.get("key") == extra.get("key") for s in jsm.get("sites", [])):
            jsm["sites"].append(extra)
    return jsm


def write_dist(jsm):
    """写出成品配置：本地 dist/ 一份，长期维护的 hai 包目录（PKG_DIR）一份"""
    os.makedirs(C.DIST_DIR, exist_ok=True)
    body = json.dumps(jsm, ensure_ascii=False, indent=1)
    p = os.path.join(C.DIST_DIR, "jsm.json")
    with open(p, "w", encoding="utf-8") as f:
        f.write(body)

    pkg = _write_pkg(jsm, body)

    ver = {"generated": __import__("time").strftime("%Y-%m-%d %H:%M:%S"),
           "sites": len(jsm.get("sites", [])),
           "mode": store.get_state().get("mode"),
           "package": C.PKG_DIR if pkg else None}
    with open(os.path.join(C.DIST_DIR, "version.json"), "w", encoding="utf-8") as f:
        json.dump(ver, f, ensure_ascii=False, indent=2)
    return p


def _write_pkg(jsm, body):
    """把成品配置写进 hai 包目录；目录不存在则跳过（例如本地开发机）"""
    try:
        if not os.path.isdir(C.PKG_DIR):
            return False
        # 先写临时文件再原子替换，避免电视机正好读到半截配置
        tmp = os.path.join(C.PKG_DIR, ".jsm.json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(body)
        os.replace(tmp, os.path.join(C.PKG_DIR, "jsm.json"))
        with open(os.path.join(C.PKG_DIR, "VERSION"), "w", encoding="utf-8") as f:
            f.write(store.get_state().get("version", "1.0.0") + "\n" +
                    __import__("time").strftime("%Y-%m-%d %H:%M:%S\n"))
        return True
    except Exception as e:
        store.log_event("error", f"写 hai 包失败: {type(e).__name__}: {e}")
        return False


def regenerate(pool, mode=None):
    mode = mode or store.get_state().get("mode", "direct")
    jsm, stat = build(pool, mode=mode)
    write_dist(jsm)
    conf = sum(1 for x in stat["offline"] if x.get("confirmed"))
    store.log_event("render", f"生成配置：模式 {mode}，改写 {len(stat['changed'])} 条，"
                              f"无可用 {len(stat['offline'])} 条（已确认失效 {conf} 条），"
                              f"网关备份 {stat['clones']} 条")
    return jsm, stat
