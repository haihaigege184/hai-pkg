"""设备台账 —— 谁连过中枢、它现在手里有没有 CK

「其他设备连接本地包时自动同步 CK」这件事要成立，前提是先看得见设备：

  * 哪些 IP 在用这个包（局域网直连的电视机/手机，走公网隧道的远程端）
  * 各自什么时候来的、用什么 UA（okhttp 版本能区分 Fongmi 壳版本）
  * **它有没有主动把 CK 送回来过**

台账按 IP 聚合，长期累积；只在「CK 写入」发生时额外打标，
这样一眼就能看出「这台设备是纯下载方，还是曾经贡献过 CK」。
"""
import time

from . import config as C
from . import store

F_DEVICES = None


def _f():
    global F_DEVICES
    if F_DEVICES is None:
        F_DEVICES = getattr(C, "F_DEVICES", None) or \
            (C.DATA_DIR + "/devices.json")
    return F_DEVICES


def _load():
    d = store.load(_f(), None)
    if not isinstance(d, dict) or "devices" not in d:
        return {"devices": {}, "updated": 0}
    return d


def _save(d):
    d["updated"] = int(time.time())
    store.save(_f(), d)
    return d


def touch(ip, ua="", path="", kind="read", seen_ck=False, note=""):
    """登记一次访问。

    kind: read（取配置/资源）| write（往中枢送东西）| ck（送去的东西里识别出 CK）
    """
    ip = (ip or "").strip()
    if not ip:
        return None
    d = _load()
    devs = d["devices"]
    now = int(time.time())
    it = devs.get(ip) or {
        "ip": ip, "first": now, "last": now, "reqs": 0, "writes": 0,
        "ck_in": 0, "ua": "", "paths": {}, "last_path": "", "kind": "",
    }
    it["last"] = now
    it["reqs"] = int(it.get("reqs") or 0) + 1
    if ua:
        it["ua"] = ua[:200]
    if path:
        it["last_path"] = path[:200]
        p = it.setdefault("paths", {})
        key = path.split("?")[0][:120]
        p[key] = int(p.get(key) or 0) + 1
        if len(p) > 40:
            for k in sorted(p, key=lambda x: p[x])[:len(p) - 40]:
                p.pop(k, None)
    if kind == "write":
        it["writes"] = int(it.get("writes") or 0) + 1
    if seen_ck:
        it["ck_in"] = int(it.get("ck_in") or 0) + 1
        it["ck_last"] = now
    if note:
        it["note"] = note[:200]
    it["kind"] = "contributor" if int(it.get("ck_in") or 0) > 0 else "consumer"
    devs[ip] = it
    _save(d)
    return it


def list_devices():
    d = _load()
    out = []
    for ip, it in d["devices"].items():
        it = dict(it)
        it["age"] = int(time.time()) - int(it.get("last") or 0)
        it["online"] = it["age"] < 180
        it["lan"] = ip.startswith(("10.", "192.168.", "172.1", "172.2", "172.3", "127."))
        out.append(it)
    out.sort(key=lambda x: -(x.get("last") or 0))
    return out


def contributor_ips():
    """曾经贡献过 CK 的设备 IP（用于溯源「这台机器的 CK 是从哪来的」）"""
    return [d["ip"] for d in list_devices() if int(d.get("ck_in") or 0) > 0]


def reset():
    """清空客户端台账，用于「干净地做一次真机测试」"""
    d = _load()
    n = len(d.get("devices") or {})
    _save({"devices": {}, "updated": 0})
    return {"cleared": n}
