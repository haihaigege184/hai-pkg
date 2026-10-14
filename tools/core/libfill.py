"""hai 包 lib 资源补全

上游 pg 发行版故意把一部分 lib 文件做成「使用者自备」：
作者在 README.txt 里写的是「複製 lib/tokentemplate.json 成爲 lib/tokenm.json，并填寫必要的内容」。
不补的话播放器拿到的是一串 404 —— 实测 okhttp 会在 2 秒内对 tokenm.json 重试 16 次，
既拖慢配置加载，也容易让部分播放器直接判定「配置获取失败」。

这里做两件事（**只补缺失的，绝不覆盖已有文件**）：
  derive —— 有官方模板的，从模板派生一份默认配置
  stub   —— 纯清单类的，落一个空/最小合法文件，语义等于「本地没有清单，去站点拉」
"""
import gzip
import os
import shutil

from . import config as C
from . import store

# 模板 -> 目标（作者指定的派生关系）
DERIVE = {
    "tokenm.json": "tokentemplate.json",
    "sambashare.txt": "sambashare.template.txt",
    "webdav.json": "webdav.example.json",
    "alistjar.json": "alistjar.example.json",
}

# 清单类占位（空文件 = 本地无清单，由 jar 自行去站点获取）
STUB_TEXT = {
    "4kzn.txt": "",
    "guanying.txt": "",
    "hdhaven.txt": "",
    "hdhive.txt": "",
    "huamucang.txt": "",
    "panta.txt": "",
    "qianfan.txt": "",
    "woniu.txt": "",
    "yiso.txt": "",
    "ytblive.m3u": "#EXTM3U\n",
}

# 需要是「合法 gzip」的（空 gzip 也比 404 强，gunzip 不会炸）
STUB_GZ = {
    "pikpakclass.json.txt.gz": "",
}


def needed_files():
    """从当前配置里扫出所有被引用的 lib 文件，返回相对路径集合"""
    refs = set()
    try:
        import json
        import re
        with open(os.path.join(C.PKG_DIR, "jsm.json"), encoding="utf-8") as f:
            jsm = json.load(f)
        for s in jsm.get("sites", []):
            for m in re.findall(r"\./lib/([A-Za-z0-9_.\-]+)", str(s.get("ext") or "")):
                refs.add(m)
    except Exception:
        pass
    return refs


def missing():
    d = os.path.join(C.PKG_DIR, "lib")
    if not os.path.isdir(d):
        return []
    return sorted(f for f in needed_files() if not os.path.exists(os.path.join(d, f)))


def fill():
    """补全缺失的 lib 文件，返回 {created: [...], skipped: [...], unresolved: [...]}"""
    lib = os.path.join(C.PKG_DIR, "lib")
    out = {"created": [], "skipped": [], "unresolved": []}
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

    for name, body in STUB_TEXT.items():
        dst = os.path.join(lib, name)
        if os.path.exists(dst):
            out["skipped"].append(name)
        else:
            with open(dst, "w", encoding="utf-8") as f:
                f.write(body)
            out["created"].append(f"{name} (空清单占位)")

    for name, body in STUB_GZ.items():
        dst = os.path.join(lib, name)
        if os.path.exists(dst):
            out["skipped"].append(name)
        else:
            with open(dst, "wb") as f:
                f.write(gzip.compress(body.encode("utf-8")))
            out["created"].append(f"{name} (合法空 gzip)")

    if out["created"]:
        store.log_event("libfill", f"补全 hai 包 lib 资源 {len(out['created'])} 个："
                                   + "、".join(out["created"]))
    return out
