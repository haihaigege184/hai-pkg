"""PG 包对比工具 —— 自动更新流水线的核心组件

用法：
    python tools/cmp_pkg.py <新包.zip 或 解压目录> [--baseline baseline] [--json 输出.json]

做三件事：
  1. 文件级对比：相对 baseline/manifest.json 的 新增 / 删除 / 变更
  2. jsm 级对比：站点增删、每个站点的 key/api/type/name/ext 变化、顶层配置变化
  3. 影响评估：core_ok 能否保持、域名池 TAG 会不会产生孤儿、置顶规则是否失效

退出码：0 = 无差异，1 = 有差异，2 = 无法比较
"""
import argparse
import hashlib
import json
import os
import sys
import zipfile

# 包内存在但不进 baseline/manifest 的文件（与 core/verify.py 的 _IGNORE_EXTRA 同源）
IGNORE_EXTRA = {"jsm.json", "VERSION", "README.txt"}

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# jsm.json 顶层里需要逐项比对的键
TOP_KEYS = [
    "spider", "wallpaper", "disabled_wallpaper", "lives", "parses",
    "rules", "proxy", "hosts", "doh", "flags", "ads", "ijk",
]
# 站点对象里关注的字段
SITE_FIELDS = ["key", "name", "type", "api", "ext", "jar", "searchable",
               "quickSearch", "filterable", "playerType", "style"]


def sha16(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def load_package(src):
    """返回 {rel_path: (size, sha16)} 与 jsm(dict)。src 可以是 zip 或目录。"""
    files, jsm = {}, None
    if os.path.isdir(src):
        for dirpath, _dirnames, filenames in os.walk(src):
            for fn in filenames:
                full = os.path.join(dirpath, fn)
                rel = os.path.relpath(full, src).replace(os.sep, "/")
                files[rel] = (os.path.getsize(full), sha16(full))
        jp = os.path.join(src, "jsm.json")
        if os.path.exists(jp):
            jsm = json.load(open(jp, encoding="utf-8"))
    else:
        z = zipfile.ZipFile(src)
        for info in z.infolist():
            if info.is_dir():
                continue
            rel = info.filename.replace("\\", "/")
            # 只算哈希，不落盘
            h = hashlib.sha256()
            with z.open(info) as f:
                for chunk in iter(lambda: f.read(1 << 20), b""):
                    h.update(chunk)
            files[rel] = (info.file_size, h.hexdigest()[:16])
        if "jsm.json" in z.namelist():
            jsm = json.loads(z.read("jsm.json").decode("utf-8"))
    # 与 core/verify.py 同源：这三个文件不进 baseline/manifest，
    # 拿 manifest 当旧侧比对时它们必然「不存在于旧侧」—— 不过滤就会被误报成"新增"，
    # 从而把「站点零变化、只换了 jar」的包误判成有风险（2026-10-21 实测踩到）。
    files = {k: v for k, v in files.items() if k not in IGNORE_EXTRA}
    return files, jsm


def diff_files(old_map, new_map):
    added, removed, changed = [], [], []
    for p in sorted(set(new_map) - set(old_map)):
        added.append({"path": p, "size": new_map[p][0]})
    for p in sorted(set(old_map) - set(new_map)):
        removed.append({"path": p, "size": old_map[p][0]})
    for p in sorted(set(old_map) & set(new_map)):
        if old_map[p][1] != new_map[p][1]:
            changed.append({
                "path": p,
                "old_size": old_map[p][0],
                "new_size": new_map[p][0],
            })
    return {"added": added, "removed": removed, "changed": changed}


def site_map(jsm):
    out = {}
    for s in (jsm or {}).get("sites") or []:
        if not isinstance(s, dict):
            continue
        out[str(s.get("key") or s.get("api") or s.get("name"))] = s
    return out


def diff_jsm(old_jsm, new_jsm):
    os_, ns = site_map(old_jsm), site_map(new_jsm)
    res = {
        "old_total": len(os_),
        "new_total": len(ns),
        "added_sites": [],
        "removed_sites": [],
        "changed_sites": [],
        "top_changes": {},
    }
    for k in sorted(set(ns) - set(os_)):
        s = ns[k]
        res["added_sites"].append({
            "key": k, "name": s.get("name"), "type": s.get("type"), "api": s.get("api"),
        })
    for k in sorted(set(os_) - set(ns)):
        s = os_[k]
        res["removed_sites"].append({
            "key": k, "name": s.get("name"), "api": s.get("api"),
        })
    for k in sorted(set(os_) & set(ns)):
        a, b = os_[k], ns[k]
        fields = {}
        for f in SITE_FIELDS:
            if a.get(f) != b.get(f):
                fields[f] = {"old": a.get(f), "new": b.get(f)}
        if fields:
            res["changed_sites"].append({"key": k, "fields": fields})

    for tk in TOP_KEYS:
        a, b = (old_jsm or {}).get(tk), (new_jsm or {}).get(tk)
        if a != b:
            if isinstance(a, list) and isinstance(b, list):
                res["top_changes"][tk] = {
                    "old_len": len(a), "new_len": len(b),
                    "only_old": [str(x)[:120] for x in a if x not in b][:10],
                    "only_new": [str(x)[:120] for x in b if x not in a][:10],
                }
            else:
                res["top_changes"][tk] = {"old": str(a)[:120], "new": str(b)[:120]}
    return res


def assess(fdiff, jdiff, baseline_manifest):
    """给出对线上运行的影响评估"""
    warnings, blockers = [], []

    if fdiff["removed"]:
        blockers.append(
            "新包缺少 %d 个旧包文件：%s" % (
                len(fdiff["removed"]),
                ", ".join(x["path"] for x in fdiff["removed"][:6]),
            )
        )
    if fdiff["added"]:
        warnings.append(
            "新包多出 %d 个文件（manifest 需重算，否则 unexpected_extra 非空）：%s" % (
                len(fdiff["added"]),
                ", ".join(x["path"] for x in fdiff["added"][:6]),
            )
        )
    if fdiff["changed"]:
        warnings.append("%d 个文件内容变化（manifest 需重算）" % len(fdiff["changed"]))

    if jdiff["removed_sites"]:
        warnings.append(
            "站点减少 %d 个：%s —— 域名池对应 TAG 会变孤儿、bases.json 记录失效" % (
                len(jdiff["removed_sites"]),
                ", ".join(x["key"] for x in jdiff["removed_sites"][:6]),
            )
        )
    if jdiff["added_sites"]:
        warnings.append(
            "站点新增 %d 个：%s —— 无历史延迟数据，排序会排在尾部直到首次巡检" % (
                len(jdiff["added_sites"]),
                ", ".join(x["key"] for x in jdiff["added_sites"][:6]),
            )
        )
    if jdiff["changed_sites"]:
        api_moves = [x for x in jdiff["changed_sites"] if "api" in x["fields"]]
        if api_moves:
            warnings.append(
                "%d 个站点的 api 变了：%s —— TAG 由 api/ext 计算，改了就认不回旧修复" % (
                    len(api_moves),
                    ", ".join(x["key"] for x in api_moves[:6]),
                )
            )
    if "lives" in jdiff["top_changes"]:
        tc = jdiff["top_changes"]["lives"]
        warnings.append("lives 变化：%s → %s 条" % (tc.get("old_len"), tc.get("new_len")))

    return {"blockers": blockers, "warnings": warnings}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("package")
    ap.add_argument("--baseline", default=os.path.join(ROOT, "baseline"))
    ap.add_argument("--json", default="")
    args = ap.parse_args()

    man_path = os.path.join(args.baseline, "manifest.json")
    if not os.path.exists(man_path):
        print("找不到 baseline manifest: %s" % man_path)
        return 2
    manifest = json.load(open(man_path, encoding="utf-8"))
    old_files = {p: (v["size"], v["sha256"]) for p, v in (manifest.get("files") or {}).items()}

    old_jsm_path = os.path.join(args.baseline, "jsm.json")
    old_jsm = json.load(open(old_jsm_path, encoding="utf-8")) if os.path.exists(old_jsm_path) else None

    new_files, new_jsm = load_package(args.package)
    # VERSION / README.txt 等在原包里也存在，manifest 是否收录未知，统一按 manifest 为准
    fdiff = diff_files(old_files, new_files)
    jdiff = diff_jsm(old_jsm, new_jsm) if (old_jsm and new_jsm) else {}
    risk = assess(fdiff, jdiff, manifest) if jdiff else {"blockers": [], "warnings": []}

    pkg_name = os.path.basename(args.package)
    result = {
        "package": pkg_name,
        "baseline_source": manifest.get("source"),
        "files": fdiff,
        "jsm": jdiff,
        "risk": risk,
        "verdict": "",
    }

    # 结论
    if risk["blockers"]:
        result["verdict"] = "不可直接替换：存在阻断项"
    elif not fdiff["added"] and not fdiff["removed"] and not fdiff["changed"] and not jdiff.get("added_sites") \
            and not jdiff.get("removed_sites") and not jdiff.get("changed_sites"):
        result["verdict"] = "与当前 baseline 完全一致，无需更新"
    else:
        result["verdict"] = "可以更新：需重算 manifest 并确认站点变化"

    # ── 人类可读输出 ──
    print("=" * 62)
    print("包：%s" % pkg_name)
    print("基线：%s（%d 个文件 / %d 个站点）" % (
        manifest.get("source"), len(old_files), jdiff.get("old_total", 0)))
    print("=" * 62)
    print("\n【文件级】")
    print("  新增 %d / 删除 %d / 变更 %d" % (
        len(fdiff["added"]), len(fdiff["removed"]), len(fdiff["changed"])))
    for x in fdiff["added"][:15]:
        print("    + %-46s %8d" % (x["path"], x["size"]))
    for x in fdiff["removed"][:15]:
        print("    - %-46s %8d" % (x["path"], x["size"]))
    for x in fdiff["changed"][:20]:
        print("    ~ %-46s %8d -> %8d" % (x["path"], x["old_size"], x["new_size"]))
    if len(fdiff["changed"]) > 20:
        print("    … 另有 %d 个变更未列出" % (len(fdiff["changed"]) - 20))

    if jdiff:
        print("\n【jsm 站点级】")
        print("  站点数：%d -> %d" % (jdiff["old_total"], jdiff["new_total"]))
        for x in jdiff["added_sites"]:
            print("    + [%s] %s  api=%s" % (x["key"], x["name"], str(x["api"])[:50]))
        for x in jdiff["removed_sites"]:
            print("    - [%s] %s  api=%s" % (x["key"], x["name"], str(x["api"])[:50]))
        if jdiff["changed_sites"]:
            print("  字段变化 %d 个站点：" % len(jdiff["changed_sites"]))
            for x in jdiff["changed_sites"][:15]:
                fs = ", ".join(x["fields"].keys())
                print("    ~ [%s] 改了 %s" % (x["key"], fs))
            if len(jdiff["changed_sites"]) > 15:
                print("    … 另有 %d 个" % (len(jdiff["changed_sites"]) - 15))

        if jdiff["top_changes"]:
            print("\n【jsm 顶层】")
            for k, v in jdiff["top_changes"].items():
                if "old_len" in v:
                    print("  %-20s %s -> %s 条" % (k, v["old_len"], v["new_len"]))
                    for o in v["only_old"][:3]:
                        print("       - %s" % o)
                    for o in v["only_new"][:3]:
                        print("       + %s" % o)
                else:
                    print("  %-20s %s -> %s" % (k, str(v["old"])[:40], str(v["new"])[:40]))

    print("\n【影响评估】")
    if risk["blockers"]:
        for b in risk["blockers"]:
            print("  ✗ " + b)
    for w in risk["warnings"]:
        print("  ! " + w)
    if not risk["blockers"] and not risk["warnings"]:
        print("  无风险项")
    print("\n结论：%s" % result["verdict"])

    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print("\n详细结果已写入：%s" % args.json)

    return 0 if result["verdict"].startswith("与当前") else 1


if __name__ == "__main__":
    sys.exit(main())
