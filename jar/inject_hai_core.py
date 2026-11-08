# -*- coding: utf-8 -*-
"""给改造版 APK 的 smali 注入 jar 服务启动钩子（幂等 / 可回滚）。

默认只做**零风险的一件事**：在 WebDetailActivity.onCreate 里加

    invoke-static {p0}, Lcom/hisen/hai/HaiCore;->start(Landroid/content/Context;)V

这样只要打开详情页，classes2.dex 里的本地代理服务就起来了；
不改 URL、不改 WebView 行为，现网播放完全不受影响。

加 --local-ui 才做阶段 2 的「UI 彻底脱离服务器」改造：
  * 硬编码 http://10.0.0.11:8099/hai/poster/d/ → file:///android_asset/haiui/detail.html?vid=
  * WebSettings 开 file 域访问（阶段 1 的 inject.js/bridge.js 需要）

用法：
    python jar/inject_hai_core.py                 # 只注入启动钩子
    python jar/inject_hai_core.py --local-ui     # 同时切本地 UI
    python jar/inject_hai_core.py --revert       # 从 .bak 恢复
    python jar/inject_hai_core.py --check        # 只检查，不写文件

注意 smali 红线：手写片段结尾只到 .end method，不要 .end class；
invoke-* 描述符必须单行；本脚本不新增 v 寄存器，.locals 无需改。
"""
from __future__ import annotations

import argparse
import io
import os
import shutil

HERE = os.path.dirname(os.path.abspath(__file__))          # hai-repo/jar
REPO = os.path.dirname(HERE)                                # hai-repo
ANDROID = os.path.join(REPO, "android")                    # hai-repo/android
TARGET = os.path.join(ANDROID, "smali", "WebDetailActivity.smali")

ONCREATE = ".method protected onCreate(Landroid/os/Bundle;)V"
SUPER = "    invoke-super {p0, p1}, Landroid/app/Activity;->onCreate(Landroid/os/Bundle;)V"
HOOK = "    invoke-static {p0}, Lcom/hisen/hai/HaiCore;->start(Landroid/content/Context;)V"

OLD_URL = '    const-string v2, "http://10.0.0.11:8099/hai/poster/d/"'
NEW_URL = '    const-string v2, "file:///android_asset/haiui/detail.html?vid="'
JS_ON = "    invoke-virtual {v1, v2}, Landroid/webkit/WebSettings;->setJavaScriptEnabled(Z)V"
FILE_ACCESS = (
    "    invoke-virtual {v1, v2}, Landroid/webkit/WebSettings;->setAllowFileAccessFromFileURLs(Z)V\n"
    "    invoke-virtual {v1, v2}, Landroid/webkit/WebSettings;->setAllowUniversalAccessFromFileURLs(Z)V"
)


def read(p):
    return io.open(p, "r", encoding="utf-8", newline="").read()


def write(p, s):
    io.open(p, "w", encoding="utf-8", newline="").write(s)


def inject_hook(s):
    if HOOK in s:
        return s, "hook 已存在，跳过"
    i = s.find(ONCREATE)
    if i < 0:
        raise RuntimeError("找不到 onCreate")
    j = s.find(".end method", i)
    if j < 0:
        raise RuntimeError("onCreate 没有 .end method")
    k = s.find(SUPER, i, j)
    if k < 0:
        raise RuntimeError("onCreate 里找不到 invoke-super")
    at = k + len(SUPER)
    return s[:at] + "\n\n" + HOOK + s[at:], "已注入 HaiCore.start"


def inject_local_ui(s):
    msgs = []
    if NEW_URL in s:
        msgs.append("URL 已切本地，跳过")
    elif OLD_URL in s:
        s = s.replace(OLD_URL, NEW_URL, 1)
        msgs.append("URL -> file:///android_asset/haiui/detail.html?vid=")
    else:
        msgs.append("警告：未找到硬编码 URL（可能已改过）")
    if "setAllowFileAccessFromFileURLs" in s:
        msgs.append("WebSettings file 访问已开，跳过")
    elif JS_ON in s:
        s = s.replace(JS_ON, JS_ON + "\n\n" + FILE_ACCESS, 1)
        msgs.append("已开 setAllowFileAccess*（v2=1 复用现有寄存器）")
    else:
        msgs.append("警告：未找到 setJavaScriptEnabled，WebSettings 未改")
    return s, "; ".join(msgs)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default=TARGET, help="目标 smali 文件")
    ap.add_argument("--local-ui", action="store_true", help="同时把详情页切到 APK 内置 UI")
    ap.add_argument("--revert", action="store_true", help="从 .bak 恢复")
    ap.add_argument("--check", action="store_true", help="只检查不写")
    args = ap.parse_args()

    p = args.file
    if not os.path.isfile(p):
        raise RuntimeError("文件不存在: " + p)
    bak = p + ".bak-haicore"

    if args.revert:
        if not os.path.isfile(bak):
            raise RuntimeError("没有备份: " + bak)
        shutil.copyfile(bak, p)
        print("已恢复:", p)
        return

    s = read(p)
    print("目标:", p, len(s), "bytes")
    print("  hook 已存在:", HOOK in s)
    print("  硬编码 URL 存在:", OLD_URL in s)
    print("  本地 URL 存在:", NEW_URL in s)
    print("  file 访问已开:", "setAllowFileAccessFromFileURLs" in s)
    if args.check:
        return

    if not os.path.isfile(bak):
        shutil.copyfile(p, bak)
        print("已备份:", bak)

    s, m1 = inject_hook(s)
    print(" ", m1)
    if args.local_ui:
        s, m2 = inject_local_ui(s)
        print(" ", m2)

    # 栈式自检：.method / .end method 必须配对
    n_open = s.count(".method ")
    n_close = s.count(".end method")
    assert n_open == n_close, "方法括号不配对: %d vs %d" % (n_open, n_close)
    write(p, s)
    print("写入完成，方法配对 OK (%d)" % n_open)
    if not args.local_ui:
        print("（未加 --local-ui，URL 仍指向服务器；现网行为零变化）")


if __name__ == "__main__":
    main()
