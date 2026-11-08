# -*- coding: utf-8 -*-
"""给改造版 APK 的 Application.onCreate 注入 jar 服务启动薄桥（幂等 / 可回滚）。

只做**零风险的一件事**：在 App.onCreate 里（invoke-super 之后）加

    invoke-static {p0}, Lcom/hisen/hai/HaiBridge;->start(Landroid/content/Context;)V

HaiBridge 是 APK 主 dex 里的极薄壳，运行时用 DexClassLoader 加载
/data/data/com.hisen.android.tv/files/pg.jar（含 classes2.dex 的 HaiCore）并反射
调用 HaiCore.start。直接 invoke HaiCore 会因 App 主 ClassLoader 看不到 jar 内的类而
NoClassDefFoundError 崩 App；走薄桥则任何异常都静默降级，绝不影响原版观影。

用法：
    python jar/inject_hai_core.py                 # 注入到默认 dec 树 App.smali
    python jar/inject_hai_core.py --file <path>   # 指定 smali
    python jar/inject_hai_core.py --revert        # 从 .bak 恢复
    python jar/inject_hai_core.py --check         # 只检查，不写文件

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

# 默认注入目标：反编译树里的 Application（启动最早、一次性的钩子点）。
# 真正的 Application 子类是 com.fongmi.android.tv.App（改包后仍是它）。
DEFAULT_APP = os.path.join(
    "F:/tvmod/work/dec/smali/com/fongmi/android/tv/App.smali")

ONCREATE = ".method public final onCreate()V"
SUPER = "    invoke-super {p0}, Landroid/app/Application;->onCreate()V"
HOOK = "    invoke-static {p0}, Lcom/hisen/hai/HaiBridge;->start(Landroid/content/Context;)V"


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
    return s[:at] + "\n\n" + HOOK + s[at:], "已注入 HaiBridge.start"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default=DEFAULT_APP, help="目标 App.smali 文件")
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
    if args.check:
        return

    if not os.path.isfile(bak):
        shutil.copyfile(p, bak)
        print("已备份:", bak)

    s, m1 = inject_hook(s)
    print(" ", m1)

    # 栈式自检：.method / .end method 必须配对
    n_open = s.count(".method ")
    n_close = s.count(".end method")
    assert n_open == n_close, "方法括号不配对: %d vs %d" % (n_open, n_close)
    write(p, s)
    print("写入完成，方法配对 OK (%d)" % n_open)


if __name__ == "__main__":
    main()
