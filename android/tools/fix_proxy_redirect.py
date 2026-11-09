#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""恢复 PG 原画播放链路依赖的 10078→10079 iptables REDIRECT 规则。

症状
----
所有站源可搜索、预览画质能播，但「原画 / BDR原画」一律失败，播放器报
「播放地址加载失败 错误代码：2001」，logcat 里是
    java.net.ConnectException: Failed to connect to /127.0.0.1:10078
    Caused by: ECONNREFUSED (Connection refused)

根因
----
PG 把原画分片 URL 写死成 http://127.0.0.1:10078/p/<seg>/<hdr>/<chunk>/<b64>/<name>，
但设备上没有任何进程监听 10078；真正监听的是 Go 二进制 go_proxy_video(10079)。
靠一条 nat 规则把本机发往 10078 的流量重定向到 10079：

    iptables -t nat -A OUTPUT -p tcp -m tcp --dport 10078 -j REDIRECT --to-ports 10079

iptables 规则不持久化，**设备重启必丢**（2026-11-10 实踩：uptime 26min，OUTPUT 链空）。
预览画质不走 10078，所以规则丢了它仍然能播 —— 这正是「只有原画挂」的原因。

本脚本做幂等检查（-C），缺则补，然后用一个真实的 /p/ 分片请求验证回源。
不需要重打包 APK、不需要改 jar、不需要动服务器。

用法
----
    python android/tools/fix_proxy_redirect.py            # 检查 + 修复 + 验证
    python android/tools/fix_proxy_redirect.py --check     # 只检查，不改任何东西
"""

import base64
import os
import subprocess
import sys

ADB = os.environ.get(
    "HAI_ADB",
    r"C:\Users\Administrator\.jingjing_tool\sdk\platform-tools\adb.exe",
)
DEV = os.environ.get("HAI_ADB_DEVICE", "10.0.0.200:5555")

CHECK = "iptables -t nat -C OUTPUT -p tcp --dport 10078 -j REDIRECT --to-ports 10079"
ADD = "iptables -t nat -A OUTPUT -p tcp -m tcp --dport 10078 -j REDIRECT --to-ports 10079"
LIST = "iptables -t nat -S OUTPUT"
PROBE_SRC = "http://10.0.0.11:8099/hai/jsm.json"


def sh(cmd, check=False):
    """在设备上执行一条 shell 命令，返回 (returncode, stdout+stderr)。"""
    env = dict(os.environ)
    env["MSYS_NO_PATHCONV"] = "1"
    p = subprocess.run(
        [ADB, "-s", DEV, "shell", cmd],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=env,
        timeout=30,
    )
    out = p.stdout.decode("utf-8", "replace").strip()
    if check and p.returncode != 0:
        raise RuntimeError("adb 执行失败: %s\n%s" % (cmd, out))
    return p.returncode, out


def main():
    check_only = "--check" in sys.argv

    # 显式 connect，网络 adb 经常会掉
    env = dict(os.environ)
    env["MSYS_NO_PATHCONV"] = "1"
    subprocess.run(
        [ADB, "connect", DEV],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=env,
        timeout=20,
    )

    rc, cur = sh(LIST)
    present = "10078" in cur and "10079" in cur and "REDIRECT" in cur
    print("当前 nat OUTPUT: %s" % (cur.replace("\n", " | ") or "(空)"))

    if present:
        print("[OK] 规则已存在，无需修复。")
    else:
        print("[!] 规则缺失 —— 这就是原画 ECONNREFUSED 的原因。")
        if check_only:
            print("--check 模式，未做修改。")
            return 2
        rc, out = sh(ADD)
        if rc != 0:
            print("[FAIL] 下发失败: %s" % out)
            return 1
        print("[OK] 已补回 10078 → 10079。")

    # 幂等性二次确认（-C 返回 0 才算真的生效）
    rc, out = sh(CHECK)
    if rc != 0:
        print("[FAIL] -C 校验未通过: %s" % out)
        return 1
    print("[OK] -C 幂等校验通过。")

    # 端到端：造一个真实的 /p/ 分片请求，看 Go 代理能否回源吐数据
    b64 = base64.b64encode(PROBE_SRC.encode()).decode()
    probe = "http://127.0.0.1:10078/p/1/null/262144/%s/test.bin" % b64
    rc, body = sh("busybox wget -qO- --timeout=12 '%s' 2>&1 | head -c 200" % probe)
    if '"sites"' in body or "spider" in body:
        print("[OK] /p/ 分片链路回源成功（拿到 jsm.json），原画链路可用。")
    else:
        print("[WARN] /p/ 探测未拿到预期内容: %s" % body[:200])

    # 顺带打印运行时健康快照
    _, ports = sh("netstat -tln 2>/dev/null | grep -E '9978|10079|10090'")
    print("监听端口:\n%s" % ports)
    _, ping = sh("busybox wget -qO- http://127.0.0.1:10090/hai/ping")
    print("ping: %s" % ping)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print("[ERROR] %s" % exc)
        sys.exit(1)
