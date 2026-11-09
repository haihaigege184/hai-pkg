# -*- coding: utf-8 -*-
"""补齐墙页分页 key：主页(/hai/poster/) 与无更多数据的末页。"""
import os
import re
import hashlib
import shutil

HERE = os.path.dirname(os.path.abspath(__file__))
A = os.path.abspath(os.path.join(HERE, "..", "assets", "haiui"))
PAGES = os.path.join(A, "pages")


def norm(q):
    d = {}
    for p in (q or "").split("&"):
        if not p:
            continue
        k, _, v = p.partition("=")
        d[k] = v
    d.setdefault("pg", "1")
    return "&".join("%s=%s" % (k, d[k]) for k in sorted(d))


def key(q):
    return hashlib.md5(norm(q).encode("utf-8")).hexdigest()


def with_pg(q, pg):
    d = {}
    for p in (q or "").split("&"):
        if not p:
            continue
        k, _, v = p.partition("=")
        d[k] = v
    d["pg"] = str(pg)
    return "&".join("%s=%s" % (k, v) for k, v in d.items())


wall = open(os.path.join(A, "wall.html"), encoding="utf-8", errors="replace").read()
qs = [""] + [m.replace("&amp;", "&")
             for m in re.findall(r"/hai/poster/\?([^'\"<>\s]+)", wall)]
qs = list(dict.fromkeys(qs))

# 首页快照：抓的时候 q="" 被补成 src=douban
home_src = os.path.join(PAGES, key("src=douban&pg=1") + ".html")
home_bytes = open(home_src, "rb").read() if os.path.isfile(home_src) else None
empty = ("<!DOCTYPE html><html lang='zh-CN'><head><meta charset='utf-8'>"
         "<link rel='stylesheet' href='/hai/ui/wall.css'></head><body>"
         "<div class='wall' id='wallbox'></div></body></html>").encode("utf-8")

made = []
for q in qs:
    for pg in (1, 2, 3):
        f = os.path.join(PAGES, key(with_pg(q, pg)) + ".html")
        if os.path.isfile(f):
            continue
        if pg == 1 and home_bytes:
            open(f, "wb").write(home_bytes)
            made.append(("home", q, pg))
        else:
            open(f, "wb").write(empty)
            made.append(("end", q, pg))

print("补齐 %d 个分页 key" % len(made))
for t, q, pg in made[:8]:
    print("  %-5s q=%s pg=%d" % (t, q[:70], pg))

# 全量复核
hit = miss = 0
for q in qs:
    for pg in (1, 2, 3):
        f = os.path.join(PAGES, key(with_pg(q, pg)) + ".html")
        if os.path.isfile(f):
            hit += 1
        else:
            miss += 1
print("复核: 命中 %d / 缺失 %d" % (hit, miss))
