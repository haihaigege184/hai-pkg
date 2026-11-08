# -*- coding: utf-8 -*-
"""把 android/src/com/hisen/hai/HaiBridge.java 生成 apktool 可用的 smali。

为什么需要这一步：HaiBridge 必须进 APK 主 dex（App.onCreate 才能直接 invoke），
但 apktool 构建只认 smali。本脚本走「javac -> d8 -> 包成极简 apk -> apktool d」
得到 HaiBridge.smali，落盘到 android/smali/com/hisen/hai/，供 build_hai_tv.py 复制进 dec 树。

只有改了 HaiBridge.java 才需要重跑本脚本；平时 build_hai_tv.py 直接复用已落盘的 smali。

用法：python android/build_hai_bridge.py
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "src", "com", "hisen", "hai", "HaiBridge.java")
OUT_SMI = os.path.join(HERE, "smali", "com", "hisen", "hai", "HaiBridge.smali")

JDK = os.environ.get("JAVA_HOME", r"C:\Users\Administrator\.jingjing_tool\jdk")
SDK_BT = r"C:\Users\Administrator\.jingjing_tool\sdk\build-tools\34.0.0"
ANDROID_JAR = r"C:\Users\Administrator\.jingjing_tool\sdk\platforms\android-34\android.jar"
APKTOOL = r"F:\tvmod\apktool.jar"


def run(cmd):
    print("  $", " ".join(cmd))
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        sys.stderr.write((p.stdout or "") + "\n" + (p.stderr or "") + "\n")
        raise SystemExit("失败：%s" % cmd[0])
    return p.stdout


def main():
    if not os.path.isfile(SRC):
        raise SystemExit("找不到 " + SRC)
    tmp = os.path.join(HERE, ".bridge_tmp")
    shutil.rmtree(tmp, ignore_errors=True)
    cls = os.path.join(tmp, "classes")
    dex = os.path.join(tmp, "dex")
    os.makedirs(cls, exist_ok=True)
    os.makedirs(dex, exist_ok=True)

    print("[1/4] javac")
    run([os.path.join(JDK, "bin", "javac.exe"), "-source", "8", "-target", "8",
         "-encoding", "UTF-8", "-nowarn", "-classpath", ANDROID_JAR,
         "-d", cls, SRC])

    cj = os.path.join(tmp, "classes.jar")
    run([os.path.join(JDK, "bin", "jar.exe"), "cf", cj, "-C", cls, "."])

    print("[2/4] d8")
    env = dict(os.environ, JAVA_HOME=JDK)
    subprocess.run([os.path.join(SDK_BT, "d8.bat"), "--lib", ANDROID_JAR,
                    "--min-api", "21", "--output", dex, cj],
                   env=env, check=True, capture_output=True, text=True)

    # 极简 manifest
    mf = os.path.join(tmp, "AndroidManifest.xml")
    with open(mf, "w", encoding="utf-8") as f:
        f.write('<?xml version="1.0" encoding="utf-8"?>\n'
                '<manifest xmlns:android="http://schemas.android.com/apk/res/android"\n'
                '    package="com.hisen.hai" android:versionCode="1" android:versionName="1">\n'
                '    <application android:label="hb"/>\n'
                '</manifest>\n')

    print("[3/4] aapt2 包极简 apk + apktool d 出 smali")
    wrap = os.path.join(tmp, "wrapper.apk")
    run([os.path.join(SDK_BT, "aapt2.exe"), "link", "-o", wrap,
         "-I", ANDROID_JAR, "--manifest", mf])
    run([os.path.join(JDK, "bin", "jar.exe"), "uf", wrap, "-C", dex, "classes.dex"])
    out = os.path.join(tmp, "out")
    run([os.path.join(JDK, "bin", "java.exe"), "-jar", APKTOOL, "d", wrap,
         "-o", out, "-f"])

    src_dir = os.path.join(out, "smali", "com", "hisen", "hai")
    if not os.path.isdir(src_dir):
        raise SystemExit("没生成 com/hisen/hai 目录")
    os.makedirs(os.path.dirname(OUT_SMI), exist_ok=True)
    n = 0
    for f in sorted(os.listdir(src_dir)):
        if f.endswith(".smali"):               # 含 HaiBridge.smali 与匿名内部类 HaiBridge$1.smali
            shutil.copyfile(os.path.join(src_dir, f),
                            os.path.join(os.path.dirname(OUT_SMI), f))
            n += 1
    print("[4/4] 已更新 %d 个 smali 到 %s" % (n, os.path.dirname(OUT_SMI)))
    shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
