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


def make_tar(with_assets=True):
    excludes = {"data", "logs", "dist", "__pycache__", ".venv"}
    def flt(ti):
        rel = os.path.relpath(ti.name, LOCAL).split(os.sep)[0]
        if rel in excludes or ti.name.endswith((".pyc", ".log", ".err")):
            return None
        if not with_assets and (os.sep + "assets" + os.sep) in ti.name + os.sep:
            return None
        return ti
    with tarfile.open(TARBALL, "w:gz") as t:
        t.add(LOCAL, arcname="oktv-hub", filter=flt)
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
