"""SEA 机器人通知通道

走 NapCat 的 OneBot HTTP 接口把消息私发给管理员 QQ：
    POST http://<napcat_host>:<napcat_port>/send_private_msg
    Authorization: Bearer <napcat_token>
    {"user_id": <QQ>, "message": "<文本>"}

配置来源优先级：环境变量 > SEA 的 config.json（默认 /root/sea2/config.json）。
通知失败一律吞掉（不影响巡检主流程），但会写入事件日志便于排查。
"""
import json
import os
import threading
import time

import requests

from . import config as C
from . import store

_lock = threading.RLock()
_recent = {}          # 去重: key -> ts


def _load_sea_cfg():
    path = os.environ.get("OKTV_SEA_CONFIG", "/root/sea2/config.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def resolve_target():
    """解析出 NapCat 地址、token 与管理员 QQ"""
    sc = _load_sea_cfg()
    lic = sc.get("license") or {}
    host = os.environ.get("OKTV_NAPCAT_HOST") or sc.get("napcat_host") or "127.0.0.1"
    port = int(os.environ.get("OKTV_NAPCAT_PORT") or sc.get("napcat_port") or 4000)
    token = (os.environ.get("OKTV_NAPCAT_TOKEN") or sc.get("napcat_token")
             or lic.get("botNotifyToken") or "")
    admin = (os.environ.get("OKTV_ADMIN_QQ") or sc.get("superAdmin")
             or sc.get("developer") or "")
    return host, port, str(token), str(admin)


def enabled():
    host, port, token, admin = resolve_target()
    return bool(host and port and admin)


def send(text, to=None, dedup_key=None, dedup_sec=1800):
    """给管理员发一条私聊通知。返回 True/False。

    dedup_key 相同且未超过 dedup_sec 的消息会被丢弃，避免巡检循环刷屏。
    """
    host, port, token, admin = resolve_target()
    qq = str(to or admin)
    if not qq:
        store.log_event("notify", "跳过通知：未解析到管理员 QQ")
        return False

    if dedup_key:
        with _lock:
            last = _recent.get(dedup_key, 0)
            if time.time() - last < dedup_sec:
                return False
            _recent[dedup_key] = time.time()

    url = f"http://{host}:{port}/send_private_msg"
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        r = requests.post(url, headers=headers,
                          json={"user_id": int(qq), "message": text}, timeout=10)
        ok = r.status_code == 200 and '"status":"ok"' in r.text.replace(" ", "")
        store.log_event("notify", f"{'已发送' if ok else '发送失败'} 通知 -> {qq}: {text.splitlines()[0][:40]}")
        return ok
    except Exception as e:
        store.log_event("notify", f"通知异常: {type(e).__name__}: {e}")
        return False


def _bar(alive, total, n=10):
    if total <= 0:
        return "░" * n
    k = max(0, min(n, round(alive / total * n)))
    return "█" * k + "░" * (n - k)


def fmt_fail(tag, name, sites):
    return (f"⚠️ hai本地包 · 接口失效告警\n"
            f"───────────────\n"
            f"接口：{name or tag}（{tag}）\n"
            f"状态：域名池已无可用地址\n"
            f"影响站点：{', '.join(sites) if sites else '-'}\n"
            f"时间：{time.strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"发送「修复接口」可立即触发检测修复")


def fmt_fixed(tag, name, url, latency=0):
    return (f"✅ hai本地包 · 接口已修复\n"
            f"───────────────\n"
            f"接口：{name or tag}（{tag}）\n"
            f"新地址：{url}\n"
            f"延迟：{latency}ms\n"
            f"时间：{time.strftime('%Y-%m-%d %H:%M:%S')}")


def fmt_daily(stats, offline, recovered):
    lines = [
        "📋 hai本地包 · 每日巡检报告",
        "───────────────",
        f"时间：{time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"接口源：{stats.get('tags', 0)} 个（可用 {stats.get('usable_tags', 0)} 个）",
        f"域名池：{stats.get('alive', 0)}/{stats.get('total', 0)} 可用 "
        f"{_bar(stats.get('alive', 0), stats.get('total', 0))}",
        f"其中完整真站 {stats.get('full', 0)} 个，仅存活未确认 {stats.get('alive_all', 0) - stats.get('alive', 0)} 个",
        f"上游同步：{stats.get('sync_sources', 0)} 个源，新增 "
        f"{stats.get('new_domains', 0)} 个候选域名"
        + (f"（包内采集 {stats.get('pkg_added', 0)}）" if stats.get("pkg_added") else ""),
        f"本次自动修复：{stats.get('fixed', 0)} 个",
    ]
    if recovered:
        lines.append("已恢复：" + "、".join(recovered[:8]))
    if offline:
        lines.append("仍失效：" + "、".join(offline[:8]))
        lines.append("如需尝试兜底，发送「修复接口」")
    else:
        lines.append("结论：全部接口可用 ✅")
    lines.append("───────────────")
    # 配置地址按「内网 → FRP → CF」逐条列出（顺序由 core/endpoints.py 决定）
    try:
        from . import endpoints as _ep
        lines.extend(_ep.config_lines())
    except Exception:
        lines.append(f"配置地址：{C.PUBLIC_BASE}/hai/jsm.json")
    return "\n".join(lines)
