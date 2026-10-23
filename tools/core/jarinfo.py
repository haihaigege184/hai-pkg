"""PG 包内容索引 —— 从 pg.jar 抽出可用的 spider 类，并列出包内文件

为什么需要它
────────────
「把上游新站源一键加入 PG 包」有一个**硬门槛**：上游站点用的 `api`
（如 `csp_XXX`）必须在**当前这个 pg.jar** 里存在对应的解析类，否则配置
下发过去，端上一打开就是「解析器不存在」，站点直接报错 —— 白加。

TVBox 生态的映射是固定的：`csp_XXX` → `com.github.catvod.spider.XXX`。
pg.jar 是只含 `classes.dex` 的 zip（没有 .class），所以用正则从 dex 里抓
类型描述符即可，不必反编译。

【实测 —— 2026-10-23】上游 16 个源登记出来的 307 个新站源里，只有 28 个的
`api` 在本 jar 里能找到类，其余 279 个即使域名活着也加进来是坏的。这个检查
必须放在「加入」的前置，而不是加完再看端上报错。

包内文件清单（`js/*.js`、`lib/*.json`）同理：上游站点引用 `./js/xxx.js` 而
我们包里没有这个文件 = 端上加载失败。
"""
import json
import os
import re
import zipfile

from . import config as C
from . import store

# dex 里的类型描述符，形如 Lcom/github/catvod/spider/Wogg;
_DESC_RE = re.compile(rb"L[a-zA-Z0-9_/\$]+;")
# 只关心 spider 包下的类（TVBox 的 csp_XXX 一律映射到这里）
_SPIDER_RE = re.compile(r"^Lcom/github/catvod/spider/([A-Za-z0-9_\$]+);$")

F_JAR_INDEX = os.path.join(C.DATA_DIR, "jar_index.json")


def _jar_path():
    """成品包里的 pg.jar；本地开发机没有成品包时退回 assets/pg.jar"""
    p = os.path.join(C.PKG_DIR, "pg.jar")
    if os.path.exists(p):
        return p
    return os.path.join(C.BASE_DIR, "assets", "pg.jar")


def _jar_fingerprint(path):
    try:
        st = os.stat(path)
        return "%d:%d" % (int(st.st_mtime), int(st.st_size))
    except Exception:
        return ""


def _scan_jar(path):
    """返回 (spider 类名集合, 解析错误)"""
    classes = set()
    try:
        with zipfile.ZipFile(path) as z:
            names = z.namelist()
            blobs = []
            for n in names:
                if n.endswith(".dex"):
                    blobs.append(z.read(n))
            if not blobs:
                return classes, "包内没有 classes.dex"
            for blob in blobs:
                for raw in _DESC_RE.findall(blob):
                    m = _SPIDER_RE.match(raw.decode("ascii", "ignore"))
                    if m:
                        classes.add(m.group(1))
        return classes, ""
    except Exception as e:
        return classes, "%s: %s" % (type(e).__name__, str(e)[:120])


def spider_classes(force=False):
    """当前 pg.jar 里可用的 spider 类名集合（不含 `csp_` 前缀）。

    结果按 jar 的 mtime+size 缓存到 `data/jar_index.json` ——
    jar 有 11MB dex，每次全扫要 1~2 秒，而 jar 只在换包时才变。
    """
    path = _jar_path()
    fp = _jar_fingerprint(path)
    if not fp:
        return set(), "找不到 pg.jar（%s）" % path

    cached = store.load(F_JAR_INDEX, {}) or {}
    if not force and cached.get("fp") == fp and cached.get("classes"):
        return set(cached["classes"]), ""

    classes, err = _scan_jar(path)
    if not classes:
        return set(), err or "jar 里没扫到任何 spider 类"
    try:
        store.save(F_JAR_INDEX, {"fp": fp, "at": int(__import__("time").time()),
                                 "count": len(classes),
                                 "classes": sorted(classes)})
    except Exception:
        pass
    return classes, err


def has_spider(api):
    """`api`（如 csp_Wogg）对应的解析类是否在本 jar 里。

    非 `csp_` 形态的 api（./js/xxx.js、http://…）不在这里判，返回 True
    （由调用方按别的规则处理）。
    """
    a = str(api or "")
    if not a.startswith("csp_"):
        return True
    classes, _err = spider_classes()
    return a[4:] in classes


_PKG_FILES_CACHE = {"fp": "", "files": set()}


def _pkg_fingerprint(root):
    """包目录的指纹：根目录 + 各一级子目录的 mtime/大小。

    只看根目录 mtime 不够 —— libfill 往 lib/ 里写 tokenm.json 时，
    根目录 mtime 不会变，但文件清单变了。
    """
    try:
        parts = [str(int(os.stat(root).st_mtime))]
        for name in sorted(os.listdir(root)):
            p = os.path.join(root, name)
            if os.path.isdir(p):
                st = os.stat(p)
                parts.append("%s:%d:%d" % (name, int(st.st_mtime), len(os.listdir(p))))
    except Exception:
        return ""
    return "|".join(parts)


def pkg_files():
    """成品包里的文件清单（相对路径，'/' 分隔）。

    取不到成品包时退回 assets/（本地开发机）。

    按目录 mtime + 文件数缓存 —— 判定 300+ 个候选站点会调用上千次，
    每次 walk 一遍 90 多个文件纯属浪费。
    """
    root = C.PKG_DIR if os.path.isdir(C.PKG_DIR) else os.path.join(C.BASE_DIR, "assets")
    if not os.path.isdir(root):
        return set()
    fp = _pkg_fingerprint(root)
    if _PKG_FILES_CACHE.get("fp") == fp:
        return _PKG_FILES_CACHE["files"]
    out = set()
    for dp, _dns, fns in os.walk(root):
        for fn in fns:
            rel = os.path.relpath(os.path.join(dp, fn), root).replace(os.sep, "/")
            out.add(rel)
    _PKG_FILES_CACHE["fp"] = fp
    _PKG_FILES_CACHE["files"] = out
    return out


def has_pkg_file(rel):
    """包里是否有这个文件（rel 形如 'js/alist.json' 或 './js/alist.json'）"""
    r = str(rel or "").lstrip("./")
    if not r:
        return False
    if r in pkg_files():
        return True
    return _derived_ok(r)


def _derived_ok(rel):
    """派生文件也算「有」。

    作者在 README 里要求使用者自备几个文件（如 lib/tokenm.json），本包统一
    由 `core/libfill.py` 从模板派生（tokenm.json ← tokentemplate.json）。
    所以只要**模板**在包里，那个文件在成品包里就一定会有。

    不认这条关系的话，本地开发机（assets/ 里只有模板、没有派生结果）会比线上
    判得更严 —— 同一个站点本地说「ext 引用的文件不在包里」，线上却说「可加」，
    两边结论对不上，很难排错。
    """
    try:
        from . import libfill
    except Exception:
        return False
    files = pkg_files()
    for dst, tpl in (getattr(libfill, "DERIVE", {}) or {}).items():
        if rel == "lib/" + dst and ("lib/" + tpl) in files:
            return True
    return False


def summary():
    """给 /api/newsite 与维护页用的紧凑摘要"""
    classes, err = spider_classes()
    return {"jar": _jar_path(), "spider_classes": len(classes),
            "error": err, "files": len(pkg_files())}
