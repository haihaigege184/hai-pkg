"""远程执行小工具（10.0.0.11）

    python deploy/sshx.py "systemctl is-active oktv-hub"
    python deploy/sshx.py --tail 120 "tail -n 120 /opt/oktv-hub/logs/access.jsonl"
"""
import sys

import paramiko

HOST, USER, PWD = "10.0.0.11", "root", "liuhai"


def conn():
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username=USER, password=PWD, timeout=15)
    return c


def run(c, cmd, timeout=180):
    _i, o, e = c.exec_command(cmd, timeout=timeout)
    out = o.read().decode("utf-8", "replace")
    err = e.read().decode("utf-8", "replace")
    return o.channel.recv_exit_status(), out, err


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    cmd = args[0] if args else "uptime"
    c = conn()
    try:
        code, out, err = run(c, cmd)
        sys.stdout.write(out)
        if err.strip():
            sys.stderr.write("[stderr]\n" + err)
        sys.exit(code)
    finally:
        c.close()


if __name__ == "__main__":
    main()
