"""把 oktv 中枢的路径规则挂到可解析的主机名上（wb 无 DNS 记录，改用 gps.*）"""
import paramiko

HOST, USER, PWD = "10.0.0.11", "root", "liuhai"
CFG = "/root/cloudflared-backup.yml"
HOSTNAME = "gps.haihaigege.dpdns.org"

RULE = (f"  # OK影视 接口中枢：只接管 /oktv 路径，其余流量不受影响\n"
        f"  - hostname: {HOSTNAME}\n"
        f"    path: ^/oktv(/.*)?$\n"
        f"    service: http://localhost:8099\n")


def sh(c, cmd, t=180):
    i, o, e = c.exec_command(cmd, timeout=t)
    return (o.read().decode("utf-8", "replace") + e.read().decode("utf-8", "replace"))


c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(HOST, username=USER, password=PWD, timeout=15)

# 从原始备份重建（去掉之前挂在 wb 上的那条无效规则）
print(sh(c, f"test -f {CFG}.oktv.bak && cp {CFG}.oktv.bak {CFG} && echo restored-from-bak || echo no-bak"))

py = (
    "p=%r\n" % CFG +
    "s=open(p,encoding='utf-8').read()\n"
    "rule=" + repr(RULE) + "\n"
    "s=s.replace('ingress:\\n','ingress:\\n'+rule,1)\n"
    "open(p,'w',encoding='utf-8').write(s)\n"
    "print('OK')\n"
)
print(sh(c, "python3 - <<'PYEOF'\n" + py + "PYEOF"))
print(sh(c, f"cat {CFG}"))
print(sh(c, "systemctl restart cloudflared-backup; sleep 8; systemctl is-active cloudflared-backup"))
c.close()
