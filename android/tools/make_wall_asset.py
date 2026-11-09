# -*- coding: utf-8 -*-
"""把服务器渲染的海报墙整页 HTML 固化成 APK 内置资产。

背景（2026-11-09）：
    海哥要求「把服务器的电视墙放进 app，做成站源」。服务器墙页是
    posterweb.py 渲染的**整页 HTML**（约 52KB，内联 <style> + 3 段内联
    <script>，只有 1 个外链 /hai/lib/haiplay.js，零绝对服务器地址）。
    与其重写 2886 行 Python 渲染，不如把渲染产物本身固化进 APK assets：
    UI 立刻归 APK 所有，数据请求仍走 /hai/* 相对路径，由 jar(10090) 反代
    到服务器（逻辑在 jar、可热更新），后续逐个接口本地化即可渐进裁撤服务器。

为什么必须做两处改写：
    1) WebView 以 http://127.0.0.1:10090/hai/poster/d/ 加载本页（空 vid，
       由 WebDetailActivity 拼出）。JS 里 moreUrl() 用 location.pathname
       拼翻页地址，会得到 /hai/poster/d/?pg=2 —— 那是服务器「详情页」路由，
       翻页必然失败。固定成 /hai/poster/ 才对。
    2) 加 <base href="/hai/poster/"> 兜住所有非根相对路径。

同源是关键：页面由 jar 从 assets 读出、经 HTTP 10090 返回，不是 file://，
因此 /hai/* 全部解析到 127.0.0.1:10090 同源，无需 HAI_BASE 注入、
无需 setAllowUniversalAccessFromFileURLs、无需改 WebSettings。

用法：
    python android/tools/make_wall_asset.py                # 抓现网首页
    python android/tools/make_wall_asset.py --url http://... --src tmdb
产物：
    android/assets/haiui/wall.html
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "..", "assets", "haiui", "wall.html")
DEFAULT_URL = "http://10.0.0.11:8099/hai/poster/"


def fetch(url: str, timeout: int = 15) -> str:
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Linux; Android 10) AppleWebKit/537.36 Chrome/84 Mobile Safari/537.36",
        "Accept-Encoding": "identity",
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        if r.status != 200:
            raise RuntimeError("HTTP %s" % r.status)
        raw = r.read()
    for enc in ("utf-8", "gbk"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def transform(html: str):
    """返回 (改写后的 HTML, 命中统计)。命中数不符直接抛错，禁止静默跳过。"""
    stats = {}

    # 1) <base>：所有非根相对路径都按 /hai/poster/ 解析
    if "<base " not in html.lower():
        html, n = re.subn(r"(<head[^>]*>)", r'\1\n<base href="/hai/poster/">', html, count=1)
        stats["base"] = n
        if n != 1:
            raise RuntimeError("找不到 <head>，无法注入 <base>")

    # 2) 翻页路径：moreUrl() 里的 location.pathname 固定成 /hai/poster/
    #    服务器内联 JS 原文：return location.pathname + (out.length ? "?" + ... )
    pat = re.compile(r"(return\s+)location\.pathname(\s*\+)")
    html, n = pat.subn(r"\1'/hai/poster/'\2", html)
    stats["moreUrl"] = n
    if n < 1:
        raise RuntimeError("找不到 moreUrl() 的 location.pathname，翻页会拼错路由")

    # 3) 防御：若页面里出现绝对服务器地址，改成同源相对路径
    html, n = re.subn(r"https?://10\.0\.0\.11:8099", "", html)
    stats["abs_url_rewritten"] = n

    # 4) 标注来源（不影响渲染）
    html = html.replace("<head>", "<head>\n<!-- hai-wall-asset: 由 make_wall_asset.py 固化，UI 归 APK 所有 -->", 1)
    return html, stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default=DEFAULT_URL)
    ap.add_argument("--out", default=OUT)
    a = ap.parse_args()

    html = fetch(a.url)
    print("抓取 %s -> %d 字节" % (a.url, len(html)))
    if 'id="wallbox"' not in html:
        raise RuntimeError("返回内容里没有 #wallbox，可能不是海报墙首页")

    out, stats = transform(html)
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    with open(a.out, "w", encoding="utf-8", newline="") as f:
        f.write(out)
    print("改写统计:", stats)
    print("产物:", os.path.abspath(a.out), os.path.getsize(a.out), "字节")
    return 0


if __name__ == "__main__":
    sys.exit(main())
