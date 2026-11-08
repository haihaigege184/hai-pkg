# -*- coding: utf-8 -*-
"""把 core/posterweb.py 里的静态 CSS/JS 抽成 APK 内置 UI 资源包。

架构定案（2026-11-08）：UI 美化全部走 APK 实现，不依赖服务器渲染。
本脚本是第一步「离线化」的可复现工具 —— 现有 2886 行 Python 服务端
拼 HTML 的代码里，CSS 与 9 段客户端 JS 本来就是纯静态字符串，可以
零改动搬进 APK assets，后续只需把「服务端拼 HTML」换成「JS 客户端
渲染 + jar 本地服务提供 JSON」。

用法：
    python extract_ui_assets.py
输出：
    ../assets/haiui/style.css
    ../assets/haiui/js/*.js
    ../assets/haiui/manifest.json   （含每段的行数 / 待替换占位符）
"""
import ast
import io
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.normpath(os.path.join(
    HERE, "..", "..", "..", "oktv-hub", "core", "posterweb.py"))
OUT = os.path.normpath(os.path.join(HERE, "..", "assets", "haiui"))

JS_MAP = {
    "_JS_NAV": "nav.js",
    "_JS_HERO": "hero.js",
    "_JS_EPS": "eps.js",
    "_JS_CURTAIN": "curtain.js",
    "_JS_FACE": "face.js",
    "_JS_CAST": "cast.js",
    "_JS_SELFHEAL": "selfheal.js",
    "_JS_SRCMENU": "srcmenu.js",
    "_JS_WALL": "wall.js",
}


def load_consts():
    tree = ast.parse(io.open(SRC, encoding="utf-8").read())
    vals = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if not isinstance(target, ast.Name):
                continue
            key = target.id
            if key != "_CSS" and not key.startswith("_JS_"):
                continue
            try:
                vals[key] = ast.literal_eval(node.value)
            except Exception as exc:
                print("  skip %-18s (%s)" % (key, exc))
    return vals


def strip_script(s):
    s = re.sub(r"^\s*<script[^>]*>", "", s)
    s = re.sub(r"</script>\s*$", "", s)
    return s.strip("\n")


def main():
    print("source:", SRC)
    vals = load_consts()
    os.makedirs(os.path.join(OUT, "js"), exist_ok=True)
    manifest = {"_source": "core/posterweb.py", "assets": {}}

    css = vals.get("_CSS", "")
    if css:
        io.open(os.path.join(OUT, "style.css"), "w", encoding="utf-8",
                newline="").write(css)
        manifest["assets"]["style.css"] = {
            "from": "_CSS", "lines": css.count("\n") + 1, "bytes": len(css)}
        classes = sorted(set(re.findall(r"\.([A-Za-z][A-Za-z0-9_-]*)", css)))
        manifest["css_classes"] = classes

    for key, fname in JS_MAP.items():
        val = vals.get(key)
        if val is None:
            continue
        val = strip_script(val)
        io.open(os.path.join(OUT, "js", fname), "w", encoding="utf-8",
                newline="").write(val)
        manifest["assets"]["js/" + fname] = {
            "from": key,
            "lines": val.count("\n") + 1,
            "bytes": len(val),
            "placeholders": sorted(set(re.findall(r"%[A-Z][A-Z0-9_]*%", val))),
        }

    io.open(os.path.join(OUT, "manifest.json"), "w", encoding="utf-8",
            newline="").write(json.dumps(manifest, ensure_ascii=False, indent=2))
    print("output:", OUT)
    for name, info in manifest["assets"].items():
        print("  %-22s %5d lines  %7d bytes  %s" % (
            name, info["lines"], info["bytes"],
            ",".join(info.get("placeholders") or []) or "-"))
    print("css classes:", ", ".join(manifest.get("css_classes", [])[:40]))


if __name__ == "__main__":
    main()
