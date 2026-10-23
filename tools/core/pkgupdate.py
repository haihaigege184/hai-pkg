"""pg 包自动更新管线

用途：把「TG 群/私聊/网页上传拿到的 pg zip」安全地换成本工程的线上包，
     全程可回滚，失败自动还原，绝不留下半成品。

五条硬约束（改动前必读）
──────────────────────────────────────────────────────────────
1. **先校验、后动手**：所有检查都在 staging 目录完成，任何一项不过就原地返回，
   线上 /root/hai、baseline 一个字节都不动。
2. **全量覆盖 = 严格镜像**：目标目录以新包为唯一真相 ——
   新包有的覆盖，新包没有的**删掉**（否则 verify 的 unexpected_extra 会爆）。
   但两类别删：豁免文件（jsm.json/VERSION/README.txt）与派生文件（libfill.DERIVE）。
3. **可回滚**：动手前把 PKG_DIR + baseline 打成 tar.gz 快照；
   覆盖后 core_ok 不通过 → 自动回滚，并把现场目录留成 hai.broken-<ts> 供排查。
4. **幂等**：同一个包重复 apply 结果一致（内容相同则不复制、不刷新 mtime）。
5. **core_ok 是闸门**：覆盖完必须跑 verify.run(deep=True) 且 core_ok=True，
   否则视为更新失败。这是「不精简原版」这条红线在自动流程里的落地。

为什么要严格镜像而不是「只覆盖不删」
──────────────────────────────────────────────────────────────
PG 作者每次发版可能删掉若干 lib/js 文件。只覆盖不删的话，旧文件会留在包目录里，
而新 manifest 里没有它们 → verify 的 `unexpected_extra` 非空 → core_ok 变 False。
自动流程里必须一次就对，所以按新包做镜像。

为什么派生文件必须保留
──────────────────────────────────────────────────────────────
lib/tokenm.json 等 4 个文件是作者在 README 里要求「使用者自备」的，
原包里只有模板（tokentemplate.json）。我们靠 libfill.fill() 从模板派生。
新包不带它们，镜像时若删掉，端上会 404 并疯狂重试 —— 必须保留（keep_extra）。
"""
import hashlib
import json
import os
import shutil
import tarfile
import tempfile
import time
import zipfile

from . import config as C
from . import libfill
from . import patch
from . import store
from . import verify as V

# 与 core/verify.py 保持同源：包内存在但不进 manifest、也不参与比对的文件
IGNORE_EXTRA = set(V._IGNORE_EXTRA)          # jsm.json / VERSION / README.txt
# 新包必须自带的两个文件，缺任何一个都不是可用的 PG 包
REQUIRED = ("jsm.json", "pg.jar")
# 镜像时除新包内容外还要保留的相对路径
KEEP_EXTRA = set(IGNORE_EXTRA) | {f"lib/{n}" for n in libfill.DERIVE}

BACKUP_DIR = os.path.join(C.BASE_DIR, "backups")
STAGING_DIR = os.path.join(C.BASE_DIR, "staging")
F_PKGLOG = os.path.join(C.DATA_DIR, "pkg_update.json")

# check/compare 允许读取的目录（防止被当成任意文件读取器）
ALLOW_ROOTS = ("/root/pg-inbox", "/tmp", BACKUP_DIR, STAGING_DIR)

_SEQ_KEYS = ("lives", "rules", "parses", "proxy", "hosts", "doh", "flags")


# ── 小工具 ──────────────────────────────────────────────────────
def _now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _ts():
    return time.strftime("%Y%m%d-%H%M%S")


def _sha16_bytes(b):
    return hashlib.sha256(b).hexdigest()[:16]


def _sha16_file(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def _sha16_zip(zf, name):
    h = hashlib.sha256()
    with zf.open(name) as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def _dumps(x):
    return json.dumps(x, ensure_ascii=False, sort_keys=True)


def _load(path, default=None, encoding="utf-8"):
    try:
        with open(path, encoding=encoding) as f:
            return json.load(f)
    except Exception:
        return default


def _human(n):
    n = float(n or 0)
    for u in ("B", "KB", "MB", "GB"):
        if n < 1024 or u == "GB":
            return ("%.1f%s" % (n, u)) if u != "B" else ("%d%s" % (n, u))
        n /= 1024


def allowed_path(p):
    """只允许读这几个目录下的文件"""
    try:
        ap = os.path.abspath(p)
    except Exception:
        return False
    return any(ap == r or ap.startswith(r.rstrip("/") + "/") for r in ALLOW_ROOTS)


# ── 只读校验 ────────────────────────────────────────────────────
def inspect(path):
    """检查一个 zip 是否是**可用的 PG 更新包**。不碰线上任何文件。"""
    r = {
        "ok": False,
        "package": os.path.basename(str(path or "")),
        "path": path,
        "problems": [],
        "warnings": [],
        "files": 0,
        "bytes": 0,
        "sites": 0,
        "jsm_sha": "",
        "jar_size": 0,
        "at": _now(),
    }
    if not path or not os.path.isfile(path):
        r["problems"].append("文件不存在或不可读")
        return r
    if not zipfile.is_zipfile(path):
        r["problems"].append("不是有效 zip（下载不完整，或根本不是更新包）")
        return r

    try:
        zf = zipfile.ZipFile(path)
    except Exception as e:
        r["problems"].append("打开 zip 失败：%s: %s" % (type(e).__name__, e))
        return r

    with zf:
        bad = zf.testzip()
        if bad:
            r["problems"].append("zip 内条目损坏：%s" % bad)

        names = [n for n in zf.namelist() if not n.endswith("/")]
        r["files"] = len(names)
        r["bytes"] = sum(i.file_size for i in zf.infolist() if not i.is_dir())

        # 路径穿越 / 绝对路径 / Windows 分隔符 —— 一律拒绝
        unsafe = [n for n in names
                  if n.startswith(("/", "\\")) or ".." in n.replace("\\", "/").split("/")
                  or "\\" in n]
        if unsafe:
            r["problems"].append("包内含非法路径 %d 条（拒绝解压）：%s"
                                 % (len(unsafe), ", ".join(unsafe[:3])))

        for need in REQUIRED:
            if need not in names:
                r["problems"].append("缺少必需文件 %s —— 不是完整 PG 包" % need)

        # jsm.json 必须能解析出站点
        if "jsm.json" in names:
            try:
                raw = zf.read("jsm.json")
                r["jsm_sha"] = _sha16_bytes(raw)
                jsm = json.loads(raw.decode("utf-8-sig"))
                sites = jsm.get("sites") or []
                r["sites"] = len(sites)
                if not isinstance(sites, list) or not sites:
                    r["problems"].append("jsm.json 的 sites 为空 —— 不是有效配置")
                else:
                    keys = [s.get("key") for s in sites]
                    if len(set(keys)) != len(keys):
                        r["warnings"].append("jsm.json 里有重复站点 key（原包就有的问题，不阻断）")
                    if not jsm.get("spider"):
                        r["warnings"].append("jsm.json 没有 spider 字段（可能不是本工程使用的包）")
            except Exception as e:
                r["problems"].append("jsm.json 解析失败：%s: %s" % (type(e).__name__, e))

        if "pg.jar" in names:
            r["jar_size"] = zf.getinfo("pg.jar").file_size
            if r["jar_size"] < 100 * 1024:
                r["problems"].append("pg.jar 只有 %s，明显不完整" % _human(r["jar_size"]))

    r["size_text"] = _human(r["bytes"])
    r["ok"] = not r["problems"]
    return r


# ── 与当前 baseline 比对（只读）──────────────────────────────────
def _manifest_files():
    man = _load(C.BASELINE_MANIFEST, {}) or {}
    return man.get("files") or {}, man


def compare(path):
    """把包与当前线上 baseline 逐文件、逐站点比对。不碰线上。"""
    ins = inspect(path)
    if not ins["ok"]:
        return {"ok": False, "package": ins["package"], "inspect": ins,
                "error": "；".join(ins["problems"])}

    old_files, man = _manifest_files()
    new_files = {}
    with zipfile.ZipFile(path) as zf:
        names = [n for n in zf.namelist() if not n.endswith("/") and n not in IGNORE_EXTRA]
        for n in names:
            new_files[n] = {"size": zf.getinfo(n).file_size, "sha256": _sha16_zip(zf, n)}
        jsm = json.loads(zf.read("jsm.json").decode("utf-8-sig"))

    added, removed, changed = [], [], []
    for p, meta in new_files.items():
        if p not in old_files:
            added.append({"path": p, "size": meta["size"]})
        elif (meta["size"] != old_files[p].get("size")
              or meta["sha256"] != old_files[p].get("sha256")):
            changed.append({"path": p,
                            "old_size": old_files[p].get("size"),
                            "new_size": meta["size"],
                            "old_sha": old_files[p].get("sha256"),
                            "new_sha": meta["sha256"]})
    for p, meta in old_files.items():
        if p not in new_files:
            removed.append({"path": p, "size": meta.get("size")})

    o_keys = list(man.get("site_keys") or [])
    n_sites = jsm.get("sites") or []
    n_keys = [s.get("key") for s in n_sites]
    o_set, n_set = set(o_keys), set(n_keys)

    blockers, warnings = [], []
    if removed:
        warnings.append("新包删除了 %d 个文件（会从包目录移除）：%s"
                        % (len(removed), ", ".join(x["path"] for x in removed[:6])))
    if added:
        warnings.append("新包新增 %d 个文件（manifest 需重算）：%s"
                        % (len(added), ", ".join(x["path"] for x in added[:6])))
    if changed:
        warnings.append("%d 个文件内容变化" % len(changed))
    if n_set - o_set:
        warnings.append("新增站点 %d 个：%s —— 域名池会自动收编，但排序置顶需确认"
                        % (len(n_set - o_set), ", ".join(sorted(n_set - o_set)[:6])))
    if o_set - n_set:
        warnings.append("站点消失 %d 个（原包有、新包没有）：%s"
                        % (len(o_set - n_set), ", ".join(sorted(o_set - n_set)[:6])))

    res = {
        "ok": True,
        "package": ins["package"],
        "baseline_source": man.get("source"),
        "inspect": ins,
        "files": {"added": added, "removed": removed, "changed": changed,
                  "old_total": len(old_files), "new_total": len(new_files)},
        "sites": {"old_total": len(o_keys), "new_total": len(n_keys),
                  "added": sorted(n_set - o_set), "removed": sorted(o_set - n_set),
                  "changed": []},
        "risk": {"blockers": blockers, "warnings": warnings},
        "verdict": "可以更新：需重算 manifest" if (added or removed or changed)
                   else "与当前 baseline 内容一致（重复包）",
    }
    return res


# ── manifest 重算 ────────────────────────────────────────────────
def _build_manifest(zf, source):
    """按 core/verify.py 的口径，从新包重新生成 manifest"""
    jsm = json.loads(zf.read("jsm.json").decode("utf-8-sig"))
    man = {
        "source": source,
        "generated_at": _now(),
        "generator": "oktv-hub/core/pkgupdate.py",
    }
    for k in _SEQ_KEYS:
        man[k] = [_dumps(x) for x in (jsm.get(k) or [])]
    for k in ("spider", "wallpaper", "disabled_wallpaper"):
        man[k] = jsm.get(k)
    sites = jsm.get("sites") or []
    man["site_keys"] = [s.get("key") for s in sites]
    man["site_names"] = [s.get("name") for s in sites]
    man["site_types"] = [s.get("type") for s in sites]
    man["site_apis"] = [s.get("api") for s in sites]

    files = {}
    for n in zf.namelist():
        if n.endswith("/") or n in IGNORE_EXTRA:
            continue
        files[n] = {"size": zf.getinfo(n).file_size, "sha256": _sha16_zip(zf, n)}
    man["files"] = files
    return man


# ── 备份 / 回滚 ──────────────────────────────────────────────────
def _backup(tag):
    os.makedirs(BACKUP_DIR, exist_ok=True)
    bid = "pkg-%s" % tag
    dst = os.path.join(BACKUP_DIR, bid + ".tar.gz")
    with tarfile.open(dst, "w:gz") as tf:
        if os.path.isdir(C.PKG_DIR):
            tf.add(C.PKG_DIR, arcname="hai")
        base = os.path.join(C.BASE_DIR, "baseline")
        if os.path.isdir(base):
            tf.add(base, arcname="baseline")
    return bid, dst


def list_backups():
    if not os.path.isdir(BACKUP_DIR):
        return []
    out = []
    for fn in sorted(os.listdir(BACKUP_DIR), reverse=True):
        if not fn.endswith(".tar.gz"):
            continue
        p = os.path.join(BACKUP_DIR, fn)
        out.append({"id": fn[:-7], "size": os.path.getsize(p),
                    "size_text": _human(os.path.getsize(p)),
                    "at": time.strftime("%Y-%m-%d %H:%M:%S",
                                        time.localtime(os.path.getmtime(p)))})
    return out


def _restore(backup_id):
    """把 PKG_DIR 与 baseline 还原到快照状态；当前现场改名保留，便于排查"""
    src = os.path.join(BACKUP_DIR, str(backup_id) + ".tar.gz")
    if not os.path.isfile(src):
        return {"ok": False, "error": "备份不存在：%s" % backup_id}

    tmp = tempfile.mkdtemp(prefix="pkgrestore-")
    try:
        with tarfile.open(src) as tf:
            tf.extractall(tmp)
    except Exception as e:
        shutil.rmtree(tmp, ignore_errors=True)
        return {"ok": False, "error": "解包备份失败：%s: %s" % (type(e).__name__, e)}

    stamp = _ts()
    broken = None
    try:
        if os.path.isdir(C.PKG_DIR):
            broken = "%s.broken-%s" % (C.PKG_DIR.rstrip("/"), stamp)
            os.rename(C.PKG_DIR, broken)
        src_hai = os.path.join(tmp, "hai")
        if os.path.isdir(src_hai):
            shutil.move(src_hai, C.PKG_DIR)
        else:
            os.makedirs(C.PKG_DIR, exist_ok=True)

        src_base = os.path.join(tmp, "baseline")
        dst_base = os.path.join(C.BASE_DIR, "baseline")
        if os.path.isdir(src_base):
            if os.path.isdir(dst_base):
                shutil.rmtree(dst_base, ignore_errors=True)
            shutil.copytree(src_base, dst_base)
    except Exception as e:
        return {"ok": False, "error": "还原过程失败：%s: %s" % (type(e).__name__, e),
                "tmp": tmp, "broken": broken}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    return {"ok": True, "backup": backup_id, "broken_dir": broken}


# ── 全量覆盖 ────────────────────────────────────────────────────
def _mirror(src_dir, dst_dir):
    """严格镜像：src 有的覆盖/新增，src 没有的删除（KEEP_EXTRA 例外）"""
    created, updated, same, removed = [], [], 0, []
    want = set()
    for root, _dirs, fns in os.walk(src_dir):
        for fn in fns:
            p = os.path.join(root, fn)
            rel = os.path.relpath(p, src_dir).replace(os.sep, "/")
            want.add(rel)
            dst = os.path.join(dst_dir, rel)
            d = os.path.dirname(dst)
            if d:
                os.makedirs(d, exist_ok=True)
            if os.path.exists(dst):
                try:
                    if os.path.getsize(dst) == os.path.getsize(p) and _sha16_file(dst) == _sha16_file(p):
                        same += 1
                        continue
                except Exception:
                    pass
                shutil.copy2(p, dst)
                updated.append(rel)
            else:
                shutil.copy2(p, dst)
                created.append(rel)

    for root, _dirs, fns in os.walk(dst_dir):
        for fn in fns:
            p = os.path.join(root, fn)
            rel = os.path.relpath(p, dst_dir).replace(os.sep, "/")
            if rel in want or rel in KEEP_EXTRA:
                continue
            try:
                os.remove(p)
                removed.append(rel)
            except Exception:
                pass

    # 清理空目录（自底向上）
    for root, dirs, fns in os.walk(dst_dir, topdown=False):
        if root == dst_dir:
            continue
        try:
            if not os.listdir(root):
                os.rmdir(root)
        except Exception:
            pass

    return {"created": len(created), "updated": len(updated),
            "same": same, "removed": removed,
            "removed_count": len(removed)}


def _readable(path):
    """目录是否可读可写（更新前自检）"""
    try:
        os.makedirs(path, exist_ok=True)
        t = os.path.join(path, ".wtest")
        with open(t, "w") as f:
            f.write("1")
        os.remove(t)
        return True
    except Exception:
        return False


# ── 主流程 ──────────────────────────────────────────────────────
def apply(path, reason="", force=False, pool=None):
    """执行一次完整更新。返回结构见 README/接口文档。

    pool: 域名池（dict）。传入则更新后立刻重渲染 jsm；不传则只换包+重算清单，
          重渲染交给下一次巡检（并在 warnings 里显式告知）。
    """
    steps = []
    t0 = time.time()

    def step(name, **kw):
        rec = {"step": name, "at": round(time.time() - t0, 2)}
        rec.update(kw)
        steps.append(rec)
        return rec

    # ① 校验
    ins = inspect(path)
    step("inspect", ok=ins["ok"], sites=ins.get("sites"), files=ins.get("files"),
         problems=ins["problems"])
    if not ins["ok"]:
        return {"ok": False, "stage": "inspect", "inspect": ins, "steps": steps,
                "error": "；".join(ins["problems"])}

    # ② 重复包判断
    man = _load(C.BASELINE_MANIFEST, {}) or {}
    same_src = (str(man.get("source") or "") == ins["package"])
    cmp_res = compare(path)
    no_change = (not cmp_res["files"]["added"] and not cmp_res["files"]["removed"]
                 and not cmp_res["files"]["changed"])
    step("compare", same_source=same_src, no_change=no_change,
         added=len(cmp_res["files"]["added"]), removed=len(cmp_res["files"]["removed"]),
         changed=len(cmp_res["files"]["changed"]))
    if no_change and same_src and not force:
        return {"ok": True, "noop": True, "stage": "compare", "inspect": ins,
                "compare": cmp_res, "steps": steps,
                "message": "当前线上就是这个包的内容，无需更新（force=True 可强制重跑）"}

    if not _readable(C.PKG_DIR):
        return {"ok": False, "stage": "precheck", "steps": steps,
                "error": "包目录不可写：%s" % C.PKG_DIR}

    stamp = _ts()
    stage_dir = os.path.join(STAGING_DIR, stamp)

    # ③ 解压到 staging（不碰线上）
    try:
        os.makedirs(stage_dir, exist_ok=True)
        with zipfile.ZipFile(path) as zf:
            zf.extractall(stage_dir)
        n_stage = sum(len(f) for _r, _d, f in os.walk(stage_dir))
        step("extract", dir=stage_dir, files=n_stage)
    except Exception as e:
        shutil.rmtree(stage_dir, ignore_errors=True)
        return {"ok": False, "stage": "extract", "steps": steps,
                "error": "解压失败：%s: %s" % (type(e).__name__, e)}

    # ④ 用 staging 的真实落盘内容重算 manifest（防止 zip 里声明与实际不一致）
    try:
        with zipfile.ZipFile(path) as zf:
            new_man = _build_manifest(zf, ins["package"])
        step("manifest", files=len(new_man["files"]), sites=len(new_man["site_keys"]))
    except Exception as e:
        shutil.rmtree(stage_dir, ignore_errors=True)
        return {"ok": False, "stage": "manifest", "steps": steps,
                "error": "重算清单失败：%s: %s" % (type(e).__name__, e)}

    # ⑤ 备份（动手前最后一步）
    try:
        bid, bpath = _backup(stamp)
        step("backup", id=bid, path=bpath, size=os.path.getsize(bpath),
             size_text=_human(os.path.getsize(bpath)))
    except Exception as e:
        shutil.rmtree(stage_dir, ignore_errors=True)
        return {"ok": False, "stage": "backup", "steps": steps,
                "error": "备份失败，已中止（线上未改动）：%s: %s" % (type(e).__name__, e)}

    rolled = False
    rollback_info = None
    try:
        # ⑥ 全量覆盖包目录
        mir = _mirror(stage_dir, C.PKG_DIR)
        step("mirror", **mir)

        # ⑦ 更新 baseline：jsm.json 母本 + manifest
        os.makedirs(os.path.join(C.BASE_DIR, "baseline"), exist_ok=True)
        shutil.copy2(os.path.join(stage_dir, "jsm.json"), C.BASELINE_JSM)
        with open(C.BASELINE_MANIFEST, "w", encoding="utf-8") as f:
            json.dump(new_man, f, ensure_ascii=False, indent=2)
        src_lib = os.path.join(stage_dir, "lib")
        if os.path.isdir(src_lib):
            dst_lib = os.path.join(C.BASE_DIR, "baseline", "lib")
            os.makedirs(dst_lib, exist_ok=True)
            for fn in os.listdir(src_lib):
                p = os.path.join(src_lib, fn)
                if os.path.isfile(p):
                    shutil.copy2(p, os.path.join(dst_lib, fn))
        step("baseline", source=ins["package"], files=len(new_man["files"]))

        # ⑧ 补派生文件（tokenm.json 等）
        fill = libfill.fill()
        step("libfill", created=fill.get("created"), reverted=fill.get("reverted"))

        # ⑨ 重渲染 jsm（顺序：排序 + 域名替换都得重来一遍，否则端拿到的还是旧顺序）
        if pool is not None:
            try:
                from . import sources as S
                from . import sync as SY
                base_jsm = patch.load_baseline()
                new_pool = S.build_sources(base_jsm)
                # 保留历史域名状态：新 TAG 用新池，老 TAG 沿用现有池
                merged = dict(new_pool)
                for tag, ents in (pool or {}).items():
                    if tag in merged:
                        merged[tag] = ents
                pool.clear()
                pool.update(merged)
                # 换包后立刻从**新包自己**采一遍域名 —— 作者发新版时会把各站
                # 当前域名写进 js/*.js 与 lib/*.json，这是唯一稳定会带来
                # 「新地址」的通道（社区上游已实测饱和，见 sync.harvest_pkg）。
                try:
                    pkg_new, pkg_scan = SY.harvest_pkg(pool)
                    step("harvest", added=pkg_new, scanned=pkg_scan)
                except Exception as e:
                    step("harvest", ok=False, error="%s: %s" % (type(e).__name__, e))
                _jsm, stat = patch.regenerate(pool)
                step("render", changed=len(stat.get("changed") or []),
                     offline=len(stat.get("offline") or []),
                     clones=stat.get("clones"))
            except Exception as e:
                step("render", ok=False, error="%s: %s" % (type(e).__name__, e))
        else:
            step("render", skipped=True, note="未传 pool，重渲染交给下次巡检")

        # ⑩ 一致性核验 —— 这是闸门
        vr = V.run(deep=True)
        step("verify", core_ok=vr.get("core_ok"), missing=len((vr.get("files") or {}).get("missing") or []),
             size_diff=len((vr.get("files") or {}).get("size_diff") or []),
             unexpected=len((vr.get("files") or {}).get("unexpected_extra") or []))

        if not vr.get("core_ok"):
            raise RuntimeError("一致性核验未通过（core_ok=False）：%s"
                               % (vr.get("verdict") or "详见 /api/verify"))

        # ⑪ 成功
        res = {
            "ok": True,
            "stage": "done",
            "package": ins["package"],
            "sites": ins["sites"],
            "files": ins["files"],
            "size_text": ins["size_text"],
            "backup": bid,
            "steps": steps,
            "compare": cmp_res,
            "verify": {"core_ok": True, "verdict": vr.get("verdict")},
            "elapsed": round(time.time() - t0, 1),
            "message": "更新完成：%s（%d 站点），设备重启 App 后生效"
                       % (ins["package"], ins["sites"]),
        }
        _save_log(res, reason)
        store.log_event("pkg", "pg 包更新成功：%s（%d 站点，%s），备份 %s"
                        % (ins["package"], ins["sites"], ins["size_text"], bid))
        try:
            shutil.rmtree(stage_dir, ignore_errors=True)
        except Exception:
            pass
        return res

    except Exception as e:
        # ⑫ 失败 → 自动回滚
        err = "%s: %s" % (type(e).__name__, e)
        step("error", message=err)
        try:
            rb = _restore(bid)
            rolled = bool(rb.get("ok"))
            rollback_info = rb
            step("rollback", **rb)
        except Exception as e2:
            rollback_info = {"ok": False, "error": "%s: %s" % (type(e2).__name__, e2)}
            step("rollback", ok=False, error=rollback_info["error"])

        res = {
            "ok": False,
            "stage": "failed",
            "package": ins["package"],
            "error": err,
            "rolled_back": rolled,
            "rollback": rollback_info,
            "steps": steps,
            "elapsed": round(time.time() - t0, 1),
            "message": ("更新失败，已自动回滚到更新前状态" if rolled
                        else "更新失败，且自动回滚未成功 —— 需人工处理（见 steps）"),
        }
        _save_log(res, reason)
        store.log_event("pkg", "pg 包更新失败：%s（回滚 %s）" % (err, "成功" if rolled else "失败"))
        try:
            shutil.rmtree(stage_dir, ignore_errors=True)
        except Exception:
            pass
        return res


# ── 状态 / 审计 ─────────────────────────────────────────────────
def _save_log(res, reason=""):
    hist = _load(F_PKGLOG, {}) or {}
    runs = hist.get("runs") or []
    runs.append({
        "at": _now(),
        "ok": bool(res.get("ok")),
        "package": res.get("package"),
        "sites": res.get("sites"),
        "error": res.get("error"),
        "rolled_back": res.get("rolled_back"),
        "backup": res.get("backup"),
        "reason": reason,
        "elapsed": res.get("elapsed"),
    })
    hist["runs"] = runs[-50:]
    hist["last"] = runs[-1]
    try:
        os.makedirs(os.path.dirname(F_PKGLOG), exist_ok=True)
        with open(F_PKGLOG, "w", encoding="utf-8") as f:
            json.dump(hist, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def history(limit=20):
    hist = _load(F_PKGLOG, {}) or {}
    runs = hist.get("runs") or []
    return {"ok": True, "runs": runs[-int(limit):][::-1], "last": hist.get("last")}


def status():
    man = _load(C.BASELINE_MANIFEST, {}) or {}
    hist = _load(F_PKGLOG, {}) or {}
    pkg_jar = os.path.join(C.PKG_DIR, "pg.jar")
    jsm = _load(C.F_SERVED, None, "utf-8-sig") or {}
    return {
        "pkg_dir": C.PKG_DIR,
        "baseline_source": man.get("source"),
        "baseline_at": man.get("generated_at") or (
            time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(os.path.getmtime(C.BASELINE_MANIFEST)))
            if os.path.exists(C.BASELINE_MANIFEST) else None),
        "manifest_files": len(man.get("files") or {}),
        "sites": len(man.get("site_keys") or []),
        "served_sites": len(jsm.get("sites") or []),
        "jar_size": os.path.getsize(pkg_jar) if os.path.exists(pkg_jar) else 0,
        "jar_mtime": (time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(os.path.getmtime(pkg_jar)))
                      if os.path.exists(pkg_jar) else None),
        "last": hist.get("last"),
        "runs": len(hist.get("runs") or []),
        "backups": list_backups()[:5],
    }
