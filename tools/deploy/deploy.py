"""一键部署到 10.0.0.11

    python deploy/deploy.py            # 推送 + 安装 + 启动
    python deploy/deploy.py --no-assets  # 跳过 81MB 资产（首次之后的增量更新）

做的事：
    1. 本地打包（排除 data/logs/_work）
    2. SFTP 上传到 /opt/oktv-hub
    3. 远端建 venv 并装依赖
    4. 安装 systemd 单元并启动
    5. 冒烟检查 /api/status
"""
import os
import subprocess
import sys
import tarfile
import time

import paramiko

HOST, USER, PWD = "10.0.0.11", "root", "liuhai"
REMOTE = "/opt/oktv-hub"
LOCAL = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TARBALL = os.path.join(os.path.dirname(LOCAL), "_oktv_hub_deploy.tar.gz")


EXCLUDES = {"data", "logs", "dist", "__pycache__", ".venv", "_work"}


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


def main():
    with_assets = "--no-assets" not in sys.argv
    print("[1/6] 打包…")
    tb = make_tar(with_assets)
    size = os.path.getsize(tb) / 1024 / 1024
    print(f"      {tb}  ({size:.1f} MB)")

    print("[2/6] 连接 10.0.0.11 …")
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username=USER, password=PWD, timeout=15)
    run(c, f"mkdir -p {REMOTE}")

    print("[3/6] 上传…")
    sftp = c.open_sftp()
    sftp.put(tb, "/tmp/oktv-hub.tar.gz")
    sftp.close()

    print("[4/6] 解包…")
    code, o, e = run(c, f"tar xzf /tmp/oktv-hub.tar.gz -C /opt && "
                        f"mkdir -p {REMOTE}/data {REMOTE}/logs {REMOTE}/dist && "
                        f"ls {REMOTE}")
    print("      " + o.strip().replace("\n", " "))

    print("[5/6] 建 venv + 依赖…")
    code, o, e = run(c, f"cd {REMOTE} && "
                        f"(test -d .venv || python3 -m venv .venv) && "
                        f"./.venv/bin/pip install -q --upgrade pip && "
                        f"./.venv/bin/pip install -q flask requests urllib3 && "
                        f"./.venv/bin/python -c \"import flask,requests;print('deps ok')\"",
                     timeout=900)
    print("      " + (o or e).strip()[-200:])

    print("[6/6] 安装并启动服务…")
    run(c, f"cp {REMOTE}/deploy/oktv-hub.service /etc/systemd/system/ && "
           f"systemctl daemon-reload && systemctl enable oktv-hub >/dev/null 2>&1 && "
           f"systemctl restart oktv-hub")
    time.sleep(4)
    code, o, e = run(c, "systemctl is-active oktv-hub; "
                        "curl -s --max-time 8 http://127.0.0.1:8099/oktv/api/status | head -c 400")
    print("      " + o.strip())
    if e.strip():
        print("      [stderr] " + e.strip()[:300])

    c.close()
    print("\n完成。管理入口： https://wb.haihaigege.dpdns.org/oktv/")
    print("配置文件地址：  https://wb.haihaigege.dpdns.org/oktv/jsm.json")
    print("（还需把 deploy/cloudflared-ingress.yml 里的 /oktv 规则合入云端隧道后 restart）")


if __name__ == "__main__":
    main()
