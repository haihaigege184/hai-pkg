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


def build(pool, mode="dual", public_base=None):
    """生成修复后的 jsm.json 内容（dict）

    mode: direct | gateway | dual
    """
    jsm = load_baseline()
    if public_base:
        C.PUBLIC_BASE = public_base.rstrip("/")

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
        # 只认 q>=2（带站点特征的页面）。q1 是门户页/空壳站，
        # 用它去替换原地址反而是降级，所以宁可不改。
        best = detail["url"] if detail and (detail.get("quality") or 0) >= 2 else None

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
        else:
            offline.append({"tag": tag, "key": s.get("key")})
            s["name"] = (s.get("name") or "") + "⚠️失效"
            store.log_event("offline", f"{tag}({s.get('key')}) 域名池无可用地址，已标注失效")

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
    store.log_event("render", f"生成配置：模式 {mode}，改写 {len(stat['changed'])} 条，"
                              f"失效 {len(stat['offline'])} 条，网关备份 {stat['clones']} 条")
    return jsm, stat
