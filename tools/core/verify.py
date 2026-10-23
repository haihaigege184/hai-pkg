"""与原版 pg 包的一致性核对

目的：自证本工程对 pg 包**只做了「替换失效域名」这一件事**，
      没有精简任何直播源 / 站点 / 顶层字段 / 资源文件。

基线：baseline/manifest.json —— 由原始发行版 zip 直接生成，
      记录了全部顶层字段指纹、lives/rules/parses/proxy/hosts/doh/flags
      逐条内容、sites 的 key/type/name/api，以及 87 个资源文件的大小。

判定口径：
  * lives / rules / parses / proxy / hosts / doh / flags 逐条完全相同 -> 无精简
  * sites 的 key 集合不缺失、api / type 不变 -> 无精简
    （name 上的「⚠️失效」后缀与 ext 里的 baseURL 属于**预期的修复动作**，单独列出）
  * 原版 87 个文件全部在位且大小一致 -> 无精简
"""
import hashlib
import json
import os

from . import config as C

# 顶层字段里需要逐条比对的列表字段
_SEQ_KEYS = ("lives", "rules", "parses", "proxy", "hosts", "doh", "flags")

# 包内非原版自带、由本工程生成的文件（校验时豁免）
_IGNORE_EXTRA = {"jsm.json", "VERSION", "README.txt"}


def _dumps(x):
    return json.dumps(x, ensure_ascii=False, sort_keys=True)


def _seq_diff(orig, now):
    o = [str(x) for x in (orig or [])]
    n = [str(x) for x in (now or [])]
    so, sn = set(o), set(n)
    return {
        "original": len(o),
        "now": len(n),
        "missing": sorted(so - sn)[:20],
        "added": sorted(sn - so)[:20],
        "same": so == sn,
    }


def _load(path, encoding="utf-8"):
    with open(path, encoding=encoding) as f:
        return json.load(f)


def run(deep=False):
    """deep=True 时额外比对每个资源文件的 sha256（默认只比大小，省 IO）"""
    pkg = C.PKG_DIR
    if not os.path.exists(C.BASELINE_MANIFEST):
        return {"ok": False, "error": f"缺少 {C.BASELINE_MANIFEST}"}
    pkg_jsm = os.path.join(pkg, "jsm.json")
    if not os.path.exists(pkg_jsm):
        return {"ok": False, "error": f"hai 包 jsm.json 不存在：{pkg_jsm}"}

    man = _load(C.BASELINE_MANIFEST)
    jsm = _load(pkg_jsm)

    out = {"ok": True, "baseline": man.get("source"), "pkg_dir": pkg}

    # ---- 1. 顶层列表字段逐条比对 ----
    fields = {}
    for k in _SEQ_KEYS:
        fields[k] = _seq_diff(man.get(k), [_dumps(x) for x in (jsm.get(k) or [])])
    out["fields"] = fields

    # ---- 2. 顶层标量字段 ----
    scalars = {}
    for k in ("spider", "wallpaper", "disabled_wallpaper"):
        scalars[k] = {"same": man.get(k) == jsm.get(k),
                      "original": man.get(k), "now": jsm.get(k)}
    out["scalars"] = scalars

    # ---- 3. sites ----
    o_keys = list(man.get("site_keys") or [])
    n_sites = jsm.get("sites") or []
    nm = {s.get("key"): s for s in n_sites}
    n_keys = [s.get("key") for s in n_sites]

    o_names = list(man.get("site_names") or [])
    o_types = list(man.get("site_types") or [])
    o_apis = list(man.get("site_apis") or [])

    renamed, api_changed, type_changed = [], [], []
    for i, k in enumerate(o_keys):
        cur = nm.get(k)
        if not cur:
            continue
        on = o_names[i] if i < len(o_names) else None
        ot = o_types[i] if i < len(o_types) else None
        oa = o_apis[i] if i < len(o_apis) else None
        if str(cur.get("name") or "") != str(on or ""):
            renamed.append({"key": k, "original": on, "now": cur.get("name")})
        if str(cur.get("api") or "") != str(oa or ""):
            api_changed.append({"key": k, "original": oa, "now": cur.get("api")})
        if cur.get("type") != ot:
            type_changed.append({"key": k, "original": ot, "now": cur.get("type")})

    out["sites"] = {
        "original": len(o_keys),
        "now": len(n_keys),
        # 用户主动隐藏的站点不计入「缺失」——否则维护页一点「隐藏」就会让
        # core_ok 变成 false（missing_keys 非空）。豁免**只对这张名单生效**：
        # 名单外的任何缺失照旧报错，红线本身没有放宽。
        "hidden_keys": [],
        "missing_keys": [k for k in o_keys if k not in nm],
        "added_keys": [k for k in n_keys if k not in set(o_keys)],
        "renamed": renamed,
        "api_changed": api_changed,
        "type_changed": type_changed,
    }
    try:
        from . import siteorder
        hid = siteorder.hidden_keys()
        if hid:
            miss_all = out["sites"]["missing_keys"]
            out["sites"]["hidden_keys"] = sorted(k for k in miss_all if k in hid)
            out["sites"]["missing_keys"] = [k for k in miss_all if k not in hid]
    except Exception as e:
        out["site_order_error"] = f"{type(e).__name__}: {e}"

    # ---- 4. ext 里的 baseURL 改写（预期的修复动作）----
    rewritten = []
    try:
        from . import sources as S
        base_jsm = _load(C.BASELINE_JSM, "utf-8-sig")
        ob = {s.get("key"): S.parse_ext(str(s.get("ext") or "")) for s in (base_jsm.get("sites") or [])}
        for s in n_sites:
            k = s.get("key")
            b = ob.get(k)
            c = S.parse_ext(str(s.get("ext") or ""))
            if b and c and b.get("base") and b.get("base") != c.get("base"):
                rewritten.append({"key": k, "original": b["base"], "now": c["base"]})
    except Exception as e:
        out["ext_scan_error"] = f"{type(e).__name__}: {e}"
    out["domain_rewritten"] = rewritten

    # ---- 5. 资源文件 ----
    files = man.get("files") or {}
    missing, size_diff, hash_diff = [], [], []
    for rel, meta in files.items():
        p = os.path.join(pkg, rel.replace("/", os.sep))
        if not os.path.exists(p):
            missing.append(rel)
            continue
        sz = os.path.getsize(p)
        if sz != meta.get("size"):
            size_diff.append({"path": rel, "original": meta.get("size"), "now": sz})
            continue
        if deep and meta.get("sha256"):
            try:
                with open(p, "rb") as f:
                    h = hashlib.sha256(f.read()).hexdigest()[:16]
                if h != meta["sha256"]:
                    hash_diff.append({"path": rel, "original": meta["sha256"], "now": h})
            except Exception as e:
                hash_diff.append({"path": rel, "error": f"{type(e).__name__}: {e}"})

    extra = []
    known = set(files)
    for root, _dirs, fns in os.walk(pkg):
        for fn in fns:
            rel = os.path.relpath(os.path.join(root, fn), pkg).replace(os.sep, "/")
            if rel in _IGNORE_EXTRA or rel in known:
                continue
            extra.append(rel)

    # 作者 README 要求「使用者自备」的那几个，出现属预期，不算异常
    from . import libfill
    expected = set("lib/" + k for k in libfill.DERIVE)
    out["files"] = {
        "original": len(files),
        "missing": sorted(missing),
        "size_diff": size_diff,
        "hash_diff": hash_diff,
        "deep": bool(deep),
        "expected_extra": sorted(r for r in extra if r in expected),
        "unexpected_extra": sorted(r for r in extra if r not in expected),
    }

    # ---- 6. 结论 ----
    core_ok = (
        all(fields[k]["same"] for k in _SEQ_KEYS)
        and all(scalars[k]["same"] for k in ("spider", "wallpaper", "disabled_wallpaper"))
        and not out["sites"]["missing_keys"]
        and not api_changed and not type_changed
        and not missing and not size_diff and not hash_diff
        and not out["files"]["unexpected_extra"]
    )
    out["verdict"] = "与原版逐项一致（仅按需替换了失效域名）" if core_ok else "存在差异，见明细"
    out["core_ok"] = core_ok
    out["user_hidden"] = len(out["sites"].get("hidden_keys") or [])
    if out["user_hidden"]:
        out["verdict"] += "；另有 %d 个接口是用户主动隐藏（不下发，不算精简）" % out["user_hidden"]
    out["hint"] = ("直播源 lives 属原版内容，本工程从不改写；"
                   "其中 29/36 条 url 指向 127.0.0.1:10079 与 35456，"
                   "那是 pg.jar 与播放壳在设备上起的本地服务，只有在设备端 jar 正常加载时才可用。")
    return out
