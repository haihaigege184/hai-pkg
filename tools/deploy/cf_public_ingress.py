"""用 Cloudflare API 给「远程托管隧道」挂一条永久公网入口

背景：网关在 Dashboard 里创建的隧道属于"远程托管"，本地 cloudflared 的
      config.yml / ingress 会被云端配置整体覆盖（日志里能看到
      `Updated to new configuration ... version=N`），所以只能走 API 改。

用法：
    python cf_public_ingress.py             # 只看当前远端配置
    python cf_public_ingress.py --apply     # 备份 + 写入 hai 规则
    python cf_public_ingress.py --rollback  # 从本地备份恢复
"""
import json
import os
import sys

import requests

TOKEN = os.environ["CF_API_TOKEN"]
ACCOUNT = os.environ.get("CF_ACCOUNT_ID", "cb7d0657abcc06e52aa8a044452f9d40")
TUNNEL = os.environ.get("CF_TUNNEL_ID", "63ac1096-e253-4e7a-9e6d-6d5fecc27ee4")

HOSTNAME = os.environ.get("OKTV_TUNNEL_HOST", "hai.haihaigege.dpdns.org")
SERVICE = os.environ.get("OKTV_TUNNEL_SERVICE", "http://localhost:8099")
# 只放行 hai 包与网关两条路径，管理界面 /api/* 不外露
PATH_RE = os.environ.get("OKTV_TUNNEL_PATH", r"^/(hai|gw)(/.*)?$")

API = f"https://api.cloudflare.com/client/v4/accounts/{ACCOUNT}/cfd_tunnel/{TUNNEL}/configurations"
H = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}
BAK = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tunnel_ingress.backup.json")


def get_config():
    r = requests.get(API, headers=H, timeout=20)
    r.raise_for_status()
    j = r.json()
    if not j.get("success"):
        raise SystemExit(f"读取失败: {json.dumps(j, ensure_ascii=False)[:600]}")
    return (j.get("result") or {}).get("config") or {}


def put_config(cfg):
    r = requests.put(API, headers=H, json={"config": cfg}, timeout=30)
    j = r.json()
    if not j.get("success"):
        raise SystemExit(f"写入失败 HTTP {r.status_code}: "
                         f"{json.dumps(j.get('errors'), ensure_ascii=False)[:800]}")
    return j


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    cfg = get_config()
    ing = list(cfg.get("ingress") or [])

    if mode == "--rollback":
        with open(BAK, encoding="utf-8") as f:
            old = json.load(f)
        put_config(old)
        print("已回滚为备份配置：")
        print(json.dumps(old.get("ingress"), ensure_ascii=False, indent=2))
        return

    print(f"当前远端 ingress（{len(ing)} 条）：")
    print(json.dumps(ing, ensure_ascii=False, indent=2))

    if mode != "--apply":
        print("\n（加 --apply 才会写入）")
        return

    with open(BAK, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    print(f"\n已备份当前配置 -> {os.path.basename(BAK)}")

    # 先把旧的同名规则摘掉，避免重复累积
    ing = [x for x in ing if x.get("hostname") != HOSTNAME]
    # 只剩 catch-all 时确保结尾兜底规则存在
    if not any("service" in x and "http_status" not in x and not x.get("hostname") for x in ing):
        ing.append({"service": "http_status:404"})

    rule = {"hostname": HOSTNAME, "path": PATH_RE, "service": SERVICE}
    ing.insert(0, rule)

    put_config({"ingress": ing})
    print("已写入：")
    print(json.dumps(ing, ensure_ascii=False, indent=2))

    now = get_config().get("ingress") or []
    ok = any(x.get("hostname") == HOSTNAME for x in now)
    print("\n回读校验：" + ("OK，规则已在远端生效" if ok else "失败，规则未生效"))


if __name__ == "__main__":
    main()
