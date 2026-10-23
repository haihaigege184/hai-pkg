"""一键部署到 10.0.0.11

    python deploy/deploy.py                  # 推送 + 安装 + 启动（含 SEA 插件）
    python deploy/deploy.py --no-assets      # 跳过 81MB 资产（首次之后的增量更新）
    python deploy/deploy.py --no-plugin      # 只更新中枢，不动 SEA 插件
    python deploy/deploy.py --no-restart     # 更新插件但不重启 sea2-bot

做的事：
    1. 本地打包（排除 data/logs/_work）
    2. SFTP 上传到 /opt/oktv-hub
    3. 远端建 venv 并装依赖
    4. 安装 systemd 单元并启动
    5. 冒烟检查 /api/status
    6. 部署 SEA 插件 + 框架补丁，重启 sea2-bot

关于第 6 步（教训）
------------------
插件改动**不会**随中枢部署自动生效 —— 它们是两个目录。曾经因为漏了这一步，
中枢与插件代码脱节了很久，表现是「机器人命令发了毫无反应」，排查绕了很远。
所以把插件部署并进主流程，且默认重启 bot（改完不重启等于没改）。
"""
import hashlib
import os
import shutil
import subprocess
import sys
import tarfile
import time

import paramiko

HOST, USER, PWD = "10.0.0.11", "root", "liuhai"
REMOTE = "/opt/oktv-hub"
LOCAL = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TARBALL = os.path.join(os.path.dirname(LOCAL), "_oktv_hub_deploy.tar.gz")

# SEA 机器人侧
PLUGIN_SRC = os.path.join(os.path.dirname(LOCAL), "hai-package-plugin", "index.js")
SEA_DIRS = ("/root/sea2", "/root/sea1")
PLUGIN_DST = "/root/sea2/plugins/hai-package/index.js"
FRAMEWORK_PATCH = os.path.join(LOCAL, "deploy", "sea", "base-adapter.js")
FRAMEWORK_DST = "/adapters/base-adapter.js"
PATCH_MARK = "userPermission: permissionLevel"


# 【为什么 baseline 也排除 —— 2026-10-22 踩过】
# baseline/manifest.json 是「原版清单」，**换包后会被 pkgupdate.apply 重算**。
# 而本机这份是上次拉回来的旧清单：一旦连 baseline 一起推上去，服务器上的
# 新清单就被旧清单覆盖 → /api/verify 立刻 hash_diff 3 / size_diff 3 →
# core_ok=False（"不精简原版"这条红线当场报警），而且看起来像是包被改坏了。
# 正确方向是「服务器 → 本机」：解包后再把远程 baseline 拉回来。
EXCLUDES = {"data", "logs", "dist", "__pycache__", ".venv", "_work", "backups", "staging", "baseline"}


def make_tar(with_assets=True):
    """打包本地产物。

    这里**不能用 `tarfile.add(..., filter=fn)`** —— 实测该回调在本机环境下
    完全不生效，结果是本地的 data/（域名池、CK 保险库、设备台账）和 logs/
    会原样覆盖到服务器上：既污染运行状态，又会让「服务器上的日志」看起来
    像是本地跑出来的（排查时极易被带偏，曾据此误解出「端在写入 CK」的假象）。
    改为显式 os.walk 收集，排除逻辑一目了然。
    """
    picked = []
    for root, dirs, files in os.walk(LOCAL):
        dirs[:] = [d for d in dirs if d not in EXCLUDES]
        for fn in files:
            if fn.endswith((".pyc", ".log", ".err")):
                continue
            full = os.path.join(root, fn)
            rel = os.path.relpath(full, LOCAL).replace("\\", "/")
            parts = rel.split("/")
            if "assets" in parts and not with_assets:
                continue
            picked.append((full, "oktv-hub/" + rel))
    with tarfile.open(TARBALL, "w:gz") as t:
        for full, arc in picked:
            t.add(full, arcname=arc, recursive=False)
    return TARBALL


def run(cli, cmd, timeout=600):
    stdin, stdout, stderr = cli.exec_command(cmd, timeout=timeout)
    out = stdout.read().decode("utf-8", "replace")
    err = stderr.read().decode("utf-8", "replace")
    code = stdout.channel.recv_exit_status()
    return code, out, err


def sync_baseline(c):
    """baseline 以**服务器**为准，方向固定为「服务器 → 本机」。

    换包会重算 manifest（core/pkgupdate.py 第 ⑦ 步），本机那份随即过期。
    * 服务器有 manifest  → 拉回本机（本机旧文件先备份到 backups/）
    * 服务器没有（首次部署）→ 把本机 baseline 推上去
    """
    remote_man = f"{REMOTE}/baseline/manifest.json"
    local_man = os.path.join(LOCAL, "baseline", "manifest.json")
    code, o, _ = run(c, f"test -f {remote_man} && echo HAS || echo NONE")
    sftp = c.open_sftp()
    try:
        if "HAS" in o:
            code, o2, _ = run(c, f"md5sum {remote_man} | cut -d' ' -f1")
            remote_hash = o2.strip().split()[0] if o2.strip() else ""
            local_hash = ""
            if os.path.exists(local_man):
                with open(local_man, "rb") as f:
                    local_hash = hashlib.md5(f.read()).hexdigest()
            if remote_hash and remote_hash == local_hash:
                print("      baseline 已同步（服务器=本机）")
                return
            if os.path.exists(local_man):
                bak = os.path.join(LOCAL, "backups",
                                   "baseline-local-" + time.strftime("%Y%m%d-%H%M%S"))
                os.makedirs(bak, exist_ok=True)
                shutil.copy2(local_man, bak)
            os.makedirs(os.path.dirname(local_man), exist_ok=True)
            sftp.get(remote_man, local_man)
            print("      baseline <- 服务器（本机旧清单已备份到 backups/baseline-local-*）")
        else:
            os.makedirs(os.path.join(LOCAL, "baseline"), exist_ok=True)
            run(c, f"mkdir -p {REMOTE}/baseline")
            sftp.put(local_man, remote_man)
            print("      baseline -> 服务器（首次部署，推送本机清单）")
    finally:
        sftp.close()


def deploy_sea(c, restart=True):
    """部署 SEA 插件与框架补丁

    框架补丁（base-adapter.js）只做「缺了才补」：先检查目标文件是否已含修复标记，
    已含就跳过 —— 这样人工改过的、或将来上游自己修好的版本不会被覆盖回去。
    """
    print("[7/7] 部署 SEA 插件 …")
    if not os.path.exists(PLUGIN_SRC):
        print("      !! 找不到插件源文件，跳过：" + PLUGIN_SRC)
        return
    sftp = c.open_sftp()
    sftp.put(PLUGIN_SRC, PLUGIN_DST)
    print(f"      插件 -> {PLUGIN_DST}")

    if os.path.exists(FRAMEWORK_PATCH):
        for base in SEA_DIRS:
            dst = base + FRAMEWORK_DST
            code, o, e = run(c, f"test -f {dst} && echo HAS || echo NONE")
            if "HAS" not in o:
                continue
            code, o, e = run(c, f"grep -c {PATCH_MARK!r} {dst} || true")
            if o.strip().isdigit() and int(o.strip()) > 0:
                print(f"      框架已是修复版，跳过：{dst}")
                continue
            ts = time.strftime("%Y%m%d-%H%M%S")
            run(c, f"mkdir -p {base}/backups && "
                   f"cp -a {dst} {base}/backups/base-adapter.js.bak-{ts}")
            sftp.put(FRAMEWORK_PATCH, dst)
            print(f"      框架补丁已应用（原文件已备份）：{dst}")
    else:
        print("      !! 找不到框架补丁，跳过框架检查")
    sftp.close()

    code, o, e = run(c, "cd /root/sea2 && node --check plugins/hai-package/index.js && "
                        "node --check adapters/base-adapter.js && echo SYNTAX_OK")
    if "SYNTAX_OK" not in o:
        print("      语法检查：FAIL")
        print("      " + (o or e).strip()[:400])
        print("      ** 已中止重启；服务器上仍是上一版，运行不受影响。**")
        return
    print("      语法检查：OK")

    if not restart:
        print("      --no-restart：跳过重启。插件改动尚未生效，需手动 pm2 restart sea2-bot")
        return
    run(c, "pm2 restart sea2-bot >/dev/null 2>&1; sleep 6")
    code, o, e = run(c, "tail -n 150 /root/.pm2/logs/sea2-bot-out.log "
                        "| grep -a 'hai-package' | tail -2")
    for line in (o or "").strip().split("\n"):
        if line.strip():
            print("      重启后日志：" + line.strip()[:120])


def main():
    with_assets = "--no-assets" not in sys.argv
    with_plugin = "--no-plugin" not in sys.argv
    restart = "--no-restart" not in sys.argv

    print("[1/7] 打包…")
    tb = make_tar(with_assets)
    size = os.path.getsize(tb) / 1024 / 1024
    print(f"      {tb}  ({size:.1f} MB)")

    print("[2/7] 连接 10.0.0.11 …")
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username=USER, password=PWD, timeout=15)
    run(c, f"mkdir -p {REMOTE}")

    print("[3/7] 上传…")
    sftp = c.open_sftp()
    sftp.put(tb, "/tmp/oktv-hub.tar.gz")
    sftp.close()

    print("[4/7] 解包…")
    code, o, e = run(c, f"tar xzf /tmp/oktv-hub.tar.gz -C /opt && "
                        f"mkdir -p {REMOTE}/data {REMOTE}/logs {REMOTE}/dist && "
                        f"ls {REMOTE}")
    print("      " + o.strip().replace("\n", " "))

    sync_baseline(c)

    print("[5/7] 建 venv + 依赖…")
    code, o, e = run(c, f"cd {REMOTE} && "
                        f"(test -d .venv || python3 -m venv .venv) && "
                        f"./.venv/bin/pip install -q --upgrade pip && "
                        f"./.venv/bin/pip install -q flask requests urllib3 pycryptodome && "
                        f"./.venv/bin/python -c \"import flask,requests;print('deps ok')\"",
                     timeout=900)
    print("      " + (o or e).strip()[-200:])

    print("[6/7] 安装并启动服务…")
    run(c, f"cp {REMOTE}/deploy/oktv-hub.service /etc/systemd/system/ && "
           f"systemctl daemon-reload && systemctl enable oktv-hub >/dev/null 2>&1 && "
           f"systemctl restart oktv-hub")
    time.sleep(4)
    code, o, e = run(c, "systemctl is-active oktv-hub; "
                        "curl -s --max-time 8 http://127.0.0.1:8099/oktv/api/status | head -c 400")
    print("      " + o.strip())
    if e.strip():
        print("      [stderr] " + e.strip()[:300])

    if with_plugin:
        deploy_sea(c, restart=restart)
    else:
        print("[7/7] --no-plugin：跳过 SEA 插件部署")

    c.close()
    print("\n完成。管理入口： https://wb.haihaigege.dpdns.org/oktv/")
    print("配置文件地址：  https://wb.haihaigege.dpdns.org/oktv/jsm.json")
    print("（还需把 deploy/cloudflared-ingress.yml 里的 /oktv 规则合入云端隧道后 restart）")


if __name__ == "__main__":
    main()
