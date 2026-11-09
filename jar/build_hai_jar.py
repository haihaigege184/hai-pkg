# -*- coding: utf-8 -*-
"""构建「站源照搬 + 播放链路全进 jar」的 pg.jar。

核心手法（零风险、可回滚）
--------------------------
Android 的 DexClassLoader 对同一个 jar 会依次加载
``classes.dex``、``classes2.dex``、``classes3.dex`` ...（DexPathList 标准行为）。
所以：

* 原版 ``classes.dex``（4436 smali / spider 456 类 / Init / 包名白名单）
  **一个字节都不改**，站源生态完全照搬；
* 我们的 R 加速代理、本地服务、UI 数据接口编到 ``classes2.dex`` 追加进去；
* 想回滚：删掉 classes2.dex 就是纯原版，不用重新打包 APK。

构建链：javac(android.jar) -> classes.jar -> d8 -> classes2.dex -> 合并进原版 pg.jar
输出默认落在 ``jar/out/pg.jar``，**绝不覆盖基线**；只有显式 --deploy 才上传。

用法：
    python jar/build_hai_jar.py                # 只构建 + 自检
    python jar/build_hai_jar.py --deploy       # 构建后上传 /root/hai（含服务器 tar 基线）
"""
from __future__ import annotations

import argparse
import hashlib
import io
import os
import shutil
import subprocess
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)                       # hai-repo/
SRC = os.path.join(HERE, "src")
BUILD = os.path.join(HERE, "build")
OUT = os.path.join(HERE, "out")

# 注意：本机会话里 JAVA_HOME 常指向不存在的 C:\java17（别的项目留下的），
# 所以固定用项目自带 JDK；只有它缺失时才回退环境变量。
_PROJECT_JDK = r"C:\Users\Administrator\.jingjing_tool\jdk"
JDK = _PROJECT_JDK if os.path.isfile(os.path.join(_PROJECT_JDK, "bin", "javac.exe")) \
    else os.environ.get("JAVA_HOME", _PROJECT_JDK)
JAVAC = os.path.join(JDK, "bin", "javac.exe")
JAR = os.path.join(JDK, "bin", "jar.exe")
SDK_BT = r"C:\Users\Administrator\.jingjing_tool\sdk\build-tools\34.0.0"
D8 = os.path.join(SDK_BT, "d8.bat")
ANDROID_JAR = r"C:\Users\Administrator\.jingjing_tool\sdk\platforms\android-34\android.jar"


def d8_cmd(*args):
    """.bat 在 Windows 上必须经 cmd /c 才能由 subprocess 直接执行。"""
    return ["cmd", "/c", D8] + [str(a) for a in args]

# 基线：优先「从服务器同步下来的当前包」，其次本地解包出来的原版。
#
# 2026-11-09 事故：本地 _work/src_pg/pg.jar 是**更早一版**原版
# （classes.dex sha256 21076c73…），而服务器基线包 pg.20261020-1519.zip
# 里是 9a6e9321…（dex 长度都不同：11449536 vs 11453228）。
# 用旧 base 构建并 --deploy 会把服务器 spider 整体降级，表现为「站源和
# 搜索全部无数据」。换包后必须跑一次 `--sync-base` 刷新本地基线。
BASE_SYNC = os.path.join(HERE, "base", "pg.jar")
BASE_CANDIDATES = [
    BASE_SYNC,
    r"F:\ai\OK影视\_work\src_pg\pg.jar",
    os.path.join(ROOT, "baseline", "pg.jar"),
    r"F:\ai\OK影视\baseline_apk\pg.jar",
]
SERVER_JAR = "/root/hai/pg.jar"


def _run(cmd, cwd=None, env_overrides=None):
    print("  $", " ".join(str(x) for x in cmd))
    env = None
    if env_overrides:
        env = dict(os.environ)
        env.update(env_overrides)
    p = subprocess.run(cmd, cwd=cwd, env=env, stdout=subprocess.PIPE,
                       stderr=subprocess.STDOUT, text=True, errors="replace")
    if p.returncode != 0:
        raise RuntimeError("command failed (%d):\n%s" % (p.returncode, p.stdout[-4000:]))
    if p.stdout.strip():
        print("   ", p.stdout.strip()[-1200:].replace("\n", "\n    "))
    return p.stdout


def _find_base():
    for p in BASE_CANDIDATES:
        if os.path.isfile(p):
            return p
    raise RuntimeError("找不到原版 pg.jar 基线，检查 BASE_CANDIDATES")


def sync_base():
    """从服务器当前 /root/hai/pg.jar 同步出「纯原版基线」。

    服务器上的 jar 可能已经被我们追加过 classes2.dex，所以这里要把它剔除，
    只保留原版条目；classes.dex 逐字节不动。换 PG 更新包后跑一次即可，
    之后默认构建会优先使用这份 base，不会再拿旧版本去覆盖服务器。
    """
    print("[sync-base] 从服务器拉取当前 pg.jar ...")
    py = sys.executable
    sftpx = os.path.join(os.path.dirname(ROOT), "oktv-hub", "deploy", "sftpx.py")
    if not os.path.isfile(sftpx):
        raise RuntimeError("deploy 脚本不存在: " + sftpx)
    env = dict(os.environ)
    env["MSYS_NO_PATHCONV"] = "1"
    os.makedirs(BUILD, exist_ok=True)
    tmp = os.path.join(BUILD, "server_pg.jar")
    subprocess.run([py, sftpx, "get", SERVER_JAR, tmp], check=True, env=env)

    zin = zipfile.ZipFile(tmp)
    if "classes.dex" not in zin.namelist():
        raise RuntimeError("服务器 pg.jar 里没有 classes.dex")
    os.makedirs(os.path.dirname(BASE_SYNC), exist_ok=True)
    dropped = []
    with zipfile.ZipFile(BASE_SYNC, "w") as zout:
        for it in zin.infolist():
            if it.filename.startswith("classes") and it.filename != "classes.dex":
                dropped.append(it.filename)
                continue
            zi = zipfile.ZipInfo(it.filename, date_time=it.date_time)
            zi.compress_type = it.compress_type
            zi.external_attr = it.external_attr
            zout.writestr(zi, zin.read(it.filename))
    dex_sha = hashlib.sha256(zin.read("classes.dex")).hexdigest()[:16]
    zin.close()
    print("  丢弃已有附加 dex:", dropped or "无")
    print("  classes.dex sha256 =", dex_sha, "...（将逐字节照搬）")
    print("  base =", BASE_SYNC, os.path.getsize(BASE_SYNC), "bytes")
    return BASE_SYNC


def _java_sources():
    out = []
    for dp, _dn, fn in os.walk(SRC):
        for f in fn:
            if f.endswith(".java"):
                out.append(os.path.join(dp, f))
    return sorted(out)


def compile_sources():
    print("[1/4] javac ...")
    for exe in (JAVAC, JAR, D8):
        if not os.path.isfile(exe):
            raise RuntimeError("工具缺失: " + exe)
    if not os.path.isfile(ANDROID_JAR):
        raise RuntimeError("android.jar 缺失: " + ANDROID_JAR)
    classes = os.path.join(BUILD, "classes")
    if os.path.isdir(classes):
        shutil.rmtree(classes)
    os.makedirs(classes)
    srcs = _java_sources()
    print("  sources:", len(srcs))
    # JDK17 不再接受 -bootclasspath / --release 8 + android.jar 的组合：
    # java.* 走 JDK 自身，android.* 由 -classpath 提供，d8 负责最终 desugar。
    _run([JAVAC, "-source", "8", "-target", "8", "-encoding", "UTF-8",
          "-nowarn", "-classpath", ANDROID_JAR, "-d", classes] + srcs)

    print("[2/4] classes.jar ...")
    cj = os.path.join(BUILD, "classes.jar")
    if os.path.isfile(cj):
        os.remove(cj)
    _run([JAR, "cf", cj, "-C", classes, "."])

    print("[3/4] d8 -> classes2.dex ...")
    dexdir = os.path.join(BUILD, "dex")
    if os.path.isdir(dexdir):
        shutil.rmtree(dexdir)
    os.makedirs(dexdir)
    _run(d8_cmd("--lib", ANDROID_JAR, "--min-api", "21",
                "--output", dexdir, cj), env_overrides={"JAVA_HOME": JDK})
    dex = os.path.join(dexdir, "classes.dex")
    if not os.path.isfile(dex):
        raise RuntimeError("d8 没有产出 classes.dex")
    return dex


def merge(base_jar, our_dex, out_jar):
    print("[4/4] 合并 classes2.dex ...")
    zin = zipfile.ZipFile(base_jar)
    names = zin.namelist()
    if "classes.dex" not in names:
        raise RuntimeError("基线 jar 没有 classes.dex")
    original = zin.read("classes.dex")
    sha_before = hashlib.sha256(original).hexdigest()
    our = open(our_dex, "rb").read()

    os.makedirs(os.path.dirname(out_jar), exist_ok=True)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zout:
        for it in zin.infolist():
            zi = zipfile.ZipInfo(it.filename, date_time=it.date_time)
            zi.compress_type = it.compress_type
            zi.external_attr = it.external_attr
            zout.writestr(zi, zin.read(it.filename))
        # 追加我们的 dex，名字必须是 classes2.dex
        zi = zipfile.ZipInfo("classes2.dex")
        zi.compress_type = zipfile.ZIP_DEFLATED
        zout.writestr(zi, our)
    zin.close()
    blob = buf.getvalue()
    with open(out_jar, "wb") as f:
        f.write(blob)

    # 自检：原版 dex 逐字节一致 + 新 dex 可读 + md5 文件
    zchk = zipfile.ZipFile(out_jar)
    assert zchk.read("classes.dex") == original, "原版 classes.dex 被改动了！"
    assert hashlib.sha256(zchk.read("classes.dex")).hexdigest() == sha_before
    assert zchk.read("classes2.dex") == our
    zchk.close()
    md5 = hashlib.md5(blob).hexdigest()
    with open(out_jar + ".md5", "w", encoding="utf-8") as f:
        f.write(md5)
    print("  OK 原版 dex sha256 =", sha_before[:16], "... 完全一致")
    print("  classes.dex   =", len(original), "bytes (照搬)")
    print("  classes2.dex  =", len(our), "bytes (我们的)")
    print("  产物 =", out_jar, len(blob), "bytes, md5 =", md5)
    return md5


def deploy(out_jar):
    print("[deploy] 服务器基线 + 上传 ...")
    py = sys.executable
    sshx = os.path.join(os.path.dirname(ROOT), "oktv-hub", "deploy", "sshx.py")
    sftpx = os.path.join(os.path.dirname(ROOT), "oktv-hub", "deploy", "sftpx.py")
    if not os.path.isfile(sshx):
        raise RuntimeError("deploy 脚本不存在: " + sshx)
    env = dict(os.environ)
    env["MSYS_NO_PATHCONV"] = "1"
    subprocess.run([py, sshx,
                    "tar czf /root/oktv-pre-haijar-$(date +%Y%m%d%H%M).tar.gz "
                    + SERVER_JAR + " " + SERVER_JAR + ".md5 && echo baseline-ok"],
                   check=True, env=env)
    subprocess.run([py, sftpx, "put", out_jar, SERVER_JAR], check=True, env=env)
    subprocess.run([py, sftpx, "put", out_jar + ".md5", SERVER_JAR + ".md5"],
                   check=True, env=env)
    print("  已上传。需重启 App 才会重新加载 jar；旧 jar 在服务器 tar 基线里。")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--deploy", action="store_true", help="构建后上传到 /root/hai")
    ap.add_argument("--base", default=None, help="指定原版 pg.jar 基线")
    ap.add_argument("--sync-base", action="store_true",
                    help="换 PG 更新包后必跑：从服务器当前 pg.jar 同步纯原版基线")
    args = ap.parse_args()
    base = args.base or (sync_base() if args.sync_base else _find_base())
    print("基线 jar:", base)
    dex = compile_sources()
    out_jar = os.path.join(OUT, "pg.jar")
    merge(base, dex, out_jar)
    if args.deploy:
        deploy(out_jar)
    else:
        print("\n（未上传。确认无误后加 --deploy；现网 /root/hai/pg.jar 仍是原版）")


if __name__ == "__main__":
    main()
