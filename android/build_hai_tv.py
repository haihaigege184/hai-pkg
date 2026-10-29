# -*- coding: utf-8 -*-
"""把 OK影视(2.7.3) 原版 APK 改造成「海哥影视」共存版

为什么要改包：
    原版这个魔改客户端把 DetailActivity 整个类删了（dex 里 0 次出现，class 也不存在），
    点海报 → 直接 VideoActivity.start → 进播放器，永远没有详情页。
    manifest 里也没有任何 WebView 容器可以借用。
    ⇒ 想「点开先看简介」，只能自己加一个 Activity。

    改包同时把 applicationId 换成 com.hai.tv，让「海哥影视」和原版**共存**，
    原版不动、随时可用（这一点是海哥明确要求的，别再做「卸载原版」这种操作）。

用法（工作目录必须是纯英文路径 —— aapt2 在中文路径下打不开 res 目录）：
    python android/build_hai_tv.py --apk F:/tvmod/tv_base.apk --work F:/tvmod/work

产物：
    <work>/hai-tv-signed.apk        # 可直接 adb install
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SMALI_DIR = os.path.join(HERE, "smali")

JDK = os.environ.get("JAVA_HOME", r"C:\Users\Administrator\.jingjing_tool\jdk")
SDK_BT = r"C:\Users\Administrator\.jingjing_tool\sdk\build-tools\34.0.0"
APKTOOL = os.environ.get("APKTOOL_JAR", r"F:\tvmod\apktool.jar")
KEYSTORE = os.environ.get("HAITV_KS", r"F:\tvmod\haitv.keystore")
KS_PASS = os.environ.get("HAITV_KS_PASS", "haitv123")
KS_ALIAS = os.environ.get("HAITV_KS_ALIAS", "haitv")

NEW_PKG = "com.hai.tv"
OLD_PKG = "com.fongmi.android.tv"
APP_NAME = "海哥影视"

# 必须跟着 applicationId 一起改的三类（不改就装不上 / 装上了也和原版抢）
AUTHORITIES = ("androidx-startup", "autosize-init-provider",
               "customactivityoncrashinitprovider", "provider")
PERM_SUFFIX = ".DYNAMIC_RECEIVER_NOT_EXPORTED_PERMISSION"

VIDEO_ACT = "smali/com/fongmi/android/tv/ui/activity/VideoActivity.smali"
ACT_DIR = "smali/com/fongmi/android/tv/ui/activity"
MANIFEST = "AndroidManifest.xml"

# 5 参数 OoOo0oOoO0o0oO0O 是整个 app 里「点某一部片」的统一入口：
# 片单、海报墙、搜索、收藏，点的都是它。改成先进我们的详情页。
START_PAT = re.compile(
    r"\.method public static OoOo0oOoO0o0oO0O\("
    r"Landroid/app/Activity;Ljava/lang/String;Ljava/lang/String;"
    r"Ljava/lang/String;Ljava/lang/String;\)V.*?\.end method",
    re.S)

START_NEW = """.method public static OoOo0oOoO0o0oO0O(Landroid/app/Activity;Ljava/lang/String;Ljava/lang/String;Ljava/lang/String;Ljava/lang/String;)V
    .locals 9

    # 海哥影视：5 参入口是「点某一部片」统一入口。
    # 仅 TMDBWall 走自建豪华海报页；其余所有站点（玩偶/花卷/木偶等网盘资源类）
    # 原样进 PG 原版选集页，绝不劫持，保留原版全部播放能力。
    const-string v0, "TMDBWall"
    invoke-virtual {p1, v0}, Ljava/lang/String;->equals(Ljava/lang/Object;)Z
    move-result v0
    if-eqz v0, :cond_original

    invoke-static {p0, p1, p2, p3, p4}, Lcom/fongmi/android/tv/ui/activity/WebDetailActivity;->start(Landroid/app/Activity;Ljava/lang/String;Ljava/lang/String;Ljava/lang/String;Ljava/lang/String;)V
    return-void

    :cond_original
    const/4 v8, 0x0
    const/4 v6, 0x0
    const/4 v5, 0x0
    const/4 v7, 0x0
    move-object v0, p0
    move-object v1, p1
    move-object v2, p2
    move-object v3, p3
    move-object v4, p4
    invoke-static/range {v0 .. v8}, Lcom/fongmi/android/tv/ui/activity/VideoActivity;->OoOoO0O0oOoO0O0O(Landroid/app/Activity;Ljava/lang/String;Ljava/lang/String;Ljava/lang/String;Ljava/lang/String;Ljava/lang/String;ZZZ)V

    return-void
.end method"""

MANIFEST_ACT = ('        <activity android:configChanges="keyboard|keyboardHidden|navigation|'
                'orientation|screenLayout|screenSize|smallestScreenSize" '
                'android:name="com.fongmi.android.tv.ui.activity.WebDetailActivity" '
                'android:screenOrientation="sensorLandscape"/>\n')


def run(cmd, **kw):
    print("  $", " ".join(str(c) for c in cmd[:6]), "…" if len(cmd) > 6 else "")
    r = subprocess.run(cmd, capture_output=True, text=True, **kw)
    if r.returncode != 0:
        sys.stderr.write((r.stdout or "")[-3000:] + "\n" + (r.stderr or "")[-3000:] + "\n")
        raise SystemExit("失败：%s" % cmd[0])
    return r.stdout


def java(*args):
    return [os.path.join(JDK, "bin", "java.exe"), "-jar", APKTOOL, *args]


def step_decode(apk: str, out: str):
    if os.path.isdir(out):
        print("[1/6] 复用已解包目录", out)
        return
    print("[1/6] apktool 反编译 …")
    run(java("d", "-f", "-o", out, apk))


def step_smali(dec: str):
    print("[2/6] 注入 WebDetailActivity")
    dst = os.path.join(dec, ACT_DIR)
    os.makedirs(dst, exist_ok=True)
    for fn in ("WebDetailActivity.smali", "WebDetailActivity$1.smali"):
        shutil.copy2(os.path.join(SMALI_DIR, fn), os.path.join(dst, fn))

    p = os.path.join(dec, VIDEO_ACT)
    s = open(p, encoding="utf-8").read()
    s2, n = START_PAT.subn(START_NEW, s)
    if n != 1:
        raise SystemExit("VideoActivity.start 替换失败，命中 %d 次（版本可能对不上）" % n)
    open(p, "w", encoding="utf-8").write(s2)
    print("       VideoActivity 入口已改（1 处）")


def step_manifest(dec: str):
    print("[3/6] 改包名 / 应用名（共存）")
    p = os.path.join(dec, MANIFEST)
    s = open(p, encoding="utf-8").read()

    if 'package="%s"' % NEW_PKG not in s:
        s = s.replace('package="%s"' % OLD_PKG, 'package="%s"' % NEW_PKG, 1)
    for a in AUTHORITIES:
        s = s.replace("%s.%s" % (OLD_PKG, a), "%s.%s" % (NEW_PKG, a))
    s = s.replace("com.fongmi.quickjs.provider", "com.hai.quickjs.provider")
    s = s.replace(OLD_PKG + PERM_SUFFIX, NEW_PKG + PERM_SUFFIX)

    if "WebDetailActivity" not in s:
        anchor = re.search(r'[ \t]*<activity [^>]*ui\.activity\.VideoActivity"[^>]*/>\n', s)
        if not anchor:
            raise SystemExit("manifest 里找不到 VideoActivity 那一行，插入位置未知")
        s = s[:anchor.end()] + MANIFEST_ACT + s[anchor.end():]

    open(p, "w", encoding="utf-8").write(s)
    print("       applicationId ->", NEW_PKG, "；已注册 WebDetailActivity")

    import glob
    for sp in glob.glob(os.path.join(dec, "res", "values*", "strings.xml")):
        t = open(sp, encoding="utf-8").read()
        o = t
        for old in ("OK影视", "OK影視", "OKTV"):
            t = t.replace('<string name="app_name">%s</string>' % old,
                          '<string name="app_name">%s</string>' % APP_NAME)
        if t != o:
            open(sp, "w", encoding="utf-8").write(t)


def step_build(dec: str, work: str):
    out = os.path.join(work, "hai-tv-unsigned.apk")
    print("[4/6] 重新打包（98MB / 全量 smali，约 10~15 分钟）…")
    run(java("b", dec, "-o", out))
    return out


def step_sign(raw: str, work: str):
    print("[5/6] zipalign + 签名")
    aligned = os.path.join(work, "hai-tv-aligned.apk")
    signed = os.path.join(work, "hai-tv-signed.apk")
    run([os.path.join(SDK_BT, "zipalign.exe"), "-p", "-f", "4", raw, aligned])
    run([os.path.join(SDK_BT, "apksigner.bat"), "sign",
         "--ks", KEYSTORE, "--ks-pass", "pass:" + KS_PASS,
         "--key-pass", "pass:" + KS_PASS, "--out", signed, aligned])
    return signed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apk", required=True, help="原版 base.apk")
    ap.add_argument("--work", required=True, help="纯英文工作目录")
    ap.add_argument("--skip-decode", action="store_true")
    a = ap.parse_args()

    if re.search(r"[^\x00-\x7f]", a.work):
        raise SystemExit("--work 必须是纯英文路径（aapt2 打不开中文路径下的 res）")

    os.makedirs(a.work, exist_ok=True)
    dec = os.path.join(a.work, "dec")
    if not a.skip_decode:
        step_decode(a.apk, dec)
    step_smali(dec)
    step_manifest(dec)
    raw = step_build(dec, a.work)
    signed = step_sign(raw, a.work)
    print("[6/6] 完成:", signed)
    print("       adb install -r \"%s\"" % signed)


if __name__ == "__main__":
    main()
