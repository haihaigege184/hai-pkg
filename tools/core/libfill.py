"""hai 包 lib 资源补全

上游 pg 发行版故意把一部分 lib 文件做成「使用者自备」：
作者在 README.txt 里写的是「複製 lib/tokentemplate.json 成爲 lib/tokenm.json，并填寫必要的内容」。
不补的话播放器拿到的是一串 404 —— 实测 okhttp 会在 2 秒内对 tokenm.json 重试 16 次，
既拖慢配置加载，也容易让部分播放器直接判定「配置获取失败」。

策略（**只补原作者明确要求使用者自备的那几个，其余一律保持原版状态**）：
  derive —— 有官方模板/示例的，从模板派生一份默认配置（等价于替使用者执行 README 的步骤）
  revert —— 撤掉早期版本擅自补出来的「空清单」占位文件

为什么撤空清单：
  4kzn.txt / guanying.txt / panta.txt / ytblive.m3u 这些是 jar 向站点拉取的**本地缓存清单**。
  原版里它们不存在（404），jar 会走自己的回退逻辑去站点拉；给它一个空文件，
  等于告诉 jar「本地清单就是空的」，反而会少内容。这就是「直播/站点被精简」的来源。
  所以这里一律恢复成"文件不存在"。
"""
import gzip
import os
import shutil

from . import config as C
from . import store

# 模板 -> 目标（作者 README 指定的派生关系）
DERIVE = {
    "tokenm.json": "tokentemplate.json",
    "sambashare.txt": "sambashare.template.txt",
    "webdav.json": "webdav.example.json",
    "alistjar.json": "alistjar.example.json",
}

# 历史遗留：曾经擅自落过的空清单占位。仅当内容与当初写入的完全一致时才删除，
# 避免误删使用者后来自己填好的文件。
LEGACY_STUBS = {
    "4kzn.txt": b"",
    "guanying.txt": b"",
    "hdhaven.txt": b"",
    "hdhive.txt": b"",
    "huamucang.txt": b"",
    "panta.txt": b"",
    "qianfan.txt": b"",
    "woniu.txt": b"",
    "yiso.txt": b"",
    "ytblive.m3u": b"#EXTM3U\n",
}
LEGACY_STUB_GZ = {"pikpakclass.json.txt.gz": b""}


def needed_files():
    """从当前配置里扫出所有被引用的 lib 文件，返回相对路径集合

    三处都要扫：
      sites[].ext / sites[].api  —— ./lib/xxx（jar 的站点参数、js 型 spider）
      lives[].url / lives[].ext  —— 直播源里的 ./lib/ytblive.m3u、./lib/biptv.txt
    """
    refs = set()
    try:
        import json
        import re
        with open(os.path.join(C.PKG_DIR, "jsm.json"), encoding="utf-8") as f:
            jsm = json.load(f)
        blobs = []
        for s in jsm.get("sites", []) or []:
            blobs.append(str(s.get("ext") or ""))
            blobs.append(str(s.get("api") or ""))
        for lv in jsm.get("lives", []) or []:
            if not isinstance(lv, dict):
                continue
            blobs.append(str(lv.get("url") or ""))
            blobs.append(str(lv.get("ext") or ""))
        for b in blobs:
            for m in re.findall(r"\./lib/([A-Za-z0-9_.\-]+)", b):
                refs.add(m)
    except Exception:
        pass
    return refs


def missing():
    d = os.path.join(C.PKG_DIR, "lib")
    if not os.path.isdir(d):
        return []
    return sorted(f for f in needed_files() if not os.path.exists(os.path.join(d, f)))


def revert_stubs():
    """撤掉历史版本擅自补的空清单占位，恢复上游发行版的原始状态

    只删内容与当初写入完全一致的文件；使用者后来填过内容的文件一律保留。
    """
    lib = os.path.join(C.PKG_DIR, "lib")
    out = []
    if not os.path.isdir(lib):
        return out

    for name, body in LEGACY_STUBS.items():
        p = os.path.join(lib, name)
        try:
            if os.path.exists(p) and open(p, "rb").read() == body:
                os.remove(p)
                out.append(name)
        except Exception:
            pass

    for name, body in LEGACY_STUB_GZ.items():
        p = os.path.join(lib, name)
        try:
            if not os.path.exists(p):
                continue
            if gzip.decompress(open(p, "rb").read()) == body:
                os.remove(p)
                out.append(name)
        except Exception:
            pass

    if out:
        store.log_event("libfill", f"撤掉空清单占位 {len(out)} 个（恢复原版行为）："
                                   + "、".join(out))
    return out


def fill():
    """补全作者要求自备的 lib 文件，并撤掉早期擅自补的空清单占位

    返回 {created, skipped, unresolved, reverted}
    """
    lib = os.path.join(C.PKG_DIR, "lib")
    out = {"created": [], "skipped": [], "unresolved": [], "reverted": []}
    if not os.path.isdir(lib):
        return out

    for name, tpl in DERIVE.items():
        dst, src = os.path.join(lib, name), os.path.join(lib, tpl)
        if os.path.exists(dst):
            out["skipped"].append(name)
        elif os.path.exists(src):
            shutil.copyfile(src, dst)
            out["created"].append(f"{name} ← {tpl}")
        else:
            out["unresolved"].append(name)

    out["reverted"] = revert_stubs()

    if out["created"]:
        store.log_event("libfill", f"补全 hai 包 lib 资源 {len(out['created'])} 个："
                                   + "、".join(out["created"]))
    return out
