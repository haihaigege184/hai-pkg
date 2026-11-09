# -*- coding: utf-8 -*-
"""
离线快照二次瘦身 + 收尾清洗（在 make_offline_snapshot.py 之后跑一次）。

1. dbimg/ 全部压到最大边 320px / JPEG q=52（电视 720p 卡片 1.5x 足够），
   把 81MB 图片压到 ~15MB，APK 增量才装得下；
2. 删掉遗留的 legacy 播放脚本 js/eps.js（含 haiplay://play?site=TMDBWall
   与 /hai/poster/eps?，与「播放走 PG 原链路」冲突）；
3. 清掉 settings.html / haiplay.js 里残留的 10.0.0.11 绝对地址；
4. 生成 cards.json（vid → 片名），供 jar 的兜底详情页拼 haiplay://search?q=；
5. 更新 manifest.json。
"""
import os
import re
import io
import json
import time
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "assets", "haiui"))
SIDE = 320
QUALITY = 52


def shrink_all():
    n, before, after = 0, 0, 0
    d = os.path.join(ROOT, "dbimg")
    for dp, _dirs, fs in os.walk(d):
        for f in fs:
            p = os.path.join(dp, f)
            raw = open(p, "rb").read()
            before += len(raw)
            try:
                im = Image.open(io.BytesIO(raw)).convert("RGB")
                im.thumbnail((SIDE, SIDE), Image.LANCZOS)
                out = io.BytesIO()
                im.save(out, "JPEG", quality=QUALITY, optimize=True)
                b = out.getvalue()
                if len(b) < len(raw):
                    open(p, "wb").write(b)
                    after += len(b)
                else:
                    after += len(raw)
                n += 1
            except Exception:
                after += len(raw)
    print("图片 %d 张: %.2f MB → %.2f MB" %
          (n, before / 1048576.0, after / 1048576.0), flush=True)
    return round(after / 1048576.0, 2)


def clean_leftovers():
    # legacy 单集直达脚本：整份删掉，播放只留 haiplay://search?q=
    p = os.path.join(ROOT, "js", "eps.js")
    if os.path.isfile(p):
        os.remove(p)
        print("已删除 js/eps.js（legacy 播放链）", flush=True)

    # 旧站源设置页：地址改成同源相对，绝不留 10.0.0.11
    p = os.path.join(ROOT, "settings.html")
    if os.path.isfile(p):
        s = open(p, encoding="utf-8", errors="replace").read()
        s = re.sub(r"https?://10\.0\.0\.11:8099", "", s)
        open(p, "w", encoding="utf-8").write(s)
        print("已清洗 settings.html", flush=True)

    # haiplay.js：注释/分支里的中枢地址改指本地同源服务
    p = os.path.join(ROOT, "lib", "haiplay.js")
    if os.path.isfile(p):
        s = open(p, encoding="utf-8", errors="replace").read()
        s = s.replace("http://10.0.0.11:8099", "http://127.0.0.1:10090")
        s = s.replace("10.0.0.11:8099", "127.0.0.1:10090")
        open(p, "w", encoding="utf-8").write(s)
        print("已清洗 lib/haiplay.js", flush=True)


def build_cards():
    cards = {}
    for sub in ("pages", "detail"):
        d = os.path.join(ROOT, sub)
        if not os.path.isdir(d):
            continue
        for f in os.listdir(d):
            if not f.endswith(".html"):
                continue
            s = open(os.path.join(d, f), encoding="utf-8", errors="replace").read()
            for block in s.split('<a class="card"')[1:]:
                m = re.search(r'href="/hai/poster/d/([A-Za-z0-9_]+)"', block[:400])
                t = re.search(r"<h3>(.*?)</h3>", block[:1500], re.S)
                if m and t:
                    name = re.sub(r"<[^>]+>", "", t.group(1)).strip()
                    if name:
                        cards[m.group(1)] = name
            # 详情页自己的标题
            if sub == "detail":
                vid = f[:-5]
                m = re.search(r"<title>(.*?)</title>", s, re.S | re.I)
                if m and vid not in cards:
                    nm = re.sub(r"<[^>]+>", "", m.group(1)).split("·")[0].strip()
                    if nm:
                        cards[vid] = nm
    p = os.path.join(ROOT, "cards.json")
    open(p, "w", encoding="utf-8").write(
        json.dumps(cards, ensure_ascii=False, indent=0))
    print("cards.json: %d 条 vid→片名" % len(cards), flush=True)
    return len(cards)


def main():
    mb = shrink_all()
    clean_leftovers()
    n = build_cards()
    total, cnt = 0, 0
    for dp, _dirs, fs in os.walk(ROOT):
        for f in fs:
            total += os.path.getsize(os.path.join(dp, f))
            cnt += 1
    p = os.path.join(ROOT, "manifest.json")
    m = json.load(open(p, encoding="utf-8")) if os.path.isfile(p) else {}
    m.update({
        "shrunk_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "image_max_side": SIDE, "image_quality": QUALITY,
        "images_packed_mb": mb, "cards": n,
        "assets_files": cnt,
        "assets_total_mb": round(total / 1048576.0, 2),
        "server_dependency": "none (runtime)",
    })
    open(p, "w", encoding="utf-8").write(json.dumps(m, ensure_ascii=False, indent=2))
    print("assets 合计 %d 文件 / %.2f MB" % (cnt, total / 1048576.0), flush=True)
    print(json.dumps(m, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
