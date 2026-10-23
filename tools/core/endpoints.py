"""对外入口地址清单：内网 / FRP 穿透 / CF 隧道

为什么要单独一层
────────────────
`config.PUBLIC_BASE` 只承担一件事：**渲染 jsm.json 里的 /gw/<TAG>/ 网关地址**
（渲染必须用一个确定的、公网可达的地址，否则端上的网关请求会打到内网 IP）。
而用户看到的「配置地址」清单完全是另一回事 —— 他要的是**按稳定性排序的多条候选**：
内网直连最快（同一网段时零穿透）、FRP 次之、CF 最后。

早先两者共用一个列表，于是「往清单里加一条 FRP 地址」就改变了渲染口径，
风险不可控。现在拆开：
  * 渲染口径：仍由 `OKTV_PUBLIC_BASE` 第一项决定，代码路径一个字没动；
  * 展示清单：本模块的 `list_ordered()`，可持久化、可在维护页增删。
"""
import json
import os
import re
import socket
import time

from . import config as C

F_ENDPOINTS = os.path.join(C.DATA_DIR, "endpoints.json")

# 排序权重：内网永远第一，FRP 第二，CF 第三，其余置后
KIND_RANK = {"lan": 0, "frp": 1, "cf": 2, "other": 3}
KIND_LABEL = {"lan": "内网", "frp": "FRP", "cf": "CF", "other": "其它"}

_CF_HINTS = ("cfargotunnel", ".dpdns.org", "trycloudflare", "cloudflare", "haihaigege")
_FRP_HINTS = ("gost", "easyfrp", "sian.one", "frp", "natapp", "chmlfrp", "gl-hk1", ".top",
              ".de5.net", "locvps", "xsian")


def lan_ip():
    """本机在局域网里的 IPv4（UDP connect 不发包，不依赖外网）"""
    s = None
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except Exception:
        return ""
    finally:
        if s:
            try:
                s.close()
            except Exception:
                pass


def guess_kind(url):
    """按主机名猜类别。只用于「首次播种」和「用户没填类型时」的默认值，
    猜错也不影响功能（用户可在维护页改）。"""
    h = (url or "").lower()
    if not h.startswith("http"):
        h = "http://" + h
    try:
        host = re.sub(r"^https?://", "", h).split("/")[0].split(":")[0]
    except Exception:
        host = h
    if host.startswith(("127.", "10.", "192.168.", "172.16.", "172.17.", "172.18.",
                        "172.19.", "172.2", "172.30.", "172.31.", "localhost")):
        return "lan"
    if any(k in host for k in _CF_HINTS):
        return "cf"
    if any(k in host for k in _FRP_HINTS):
        return "frp"
    return "other"


def _norm(url):
    u = (url or "").strip().rstrip("/")
    if not u:
        return ""
    if not re.match(r"^https?://", u, re.I):
        u = "http://" + u
    return u


def _default_lan():
    base = (C.LAN_BASE or "").rstrip("/")
    if base:
        return base
    ip = lan_ip()
    return "http://%s:%d" % (ip, C.PORT) if ip else ""


def _seed():
    """首次运行时从环境变量播种（保持既有部署「开箱即有清单」）"""
    public = []
    for u in (getattr(C, "PUBLIC_BASES", None) or []):
        u = _norm(u)
        if not u:
            continue
        public.append({"url": u, "label": "", "kind": guess_kind(u),
                       "added": int(time.time()), "origin": "env"})
    order = sorted(public, key=lambda x: KIND_RANK.get(x["kind"], 3))
    return {"lan": _default_lan(), "public": order}


def load():
    try:
        with open(F_ENDPOINTS, "r", encoding="utf-8") as f:
            d = json.load(f)
        if isinstance(d, dict):
            d.setdefault("public", [])
            return d
    except Exception:
        pass
    d = _seed()
    save(d)
    return d


def save(d):
    os.makedirs(C.DATA_DIR, exist_ok=True)
    tmp = F_ENDPOINTS + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
    os.replace(tmp, F_ENDPOINTS)


def list_ordered():
    """返回按「内网 → FRP → CF」排好序的清单，每项带 jsm 地址。

    注意：这里**只影响展示与通知**，不参与 jsm.json 渲染。
    """
    d = load()
    out = []
    lan = _norm(d.get("lan") or "") or _default_lan()
    if lan:
        out.append({"kind": "lan", "label": d.get("lan_label") or KIND_LABEL["lan"],
                    "url": lan, "jsm": lan + "/hai/jsm.json", "origin": "lan"})
    pubs = []
    for i, e in enumerate(d.get("public") or []):
        u = _norm(e.get("url") if isinstance(e, dict) else e)
        if not u:
            continue
        kind = (e.get("kind") if isinstance(e, dict) else "") or guess_kind(u)
        label = (e.get("label") if isinstance(e, dict) else "") or ""
        pubs.append({"kind": kind, "label": label or KIND_LABEL.get(kind, "其它"),
                     "url": u, "jsm": u + "/hai/jsm.json",
                     "origin": (e.get("origin") if isinstance(e, dict) else "") or "user",
                     "_i": i})
    pubs.sort(key=lambda x: (KIND_RANK.get(x["kind"], 3), x["_i"]))
    for p in pubs:
        p.pop("_i", None)
        out.append(p)
    return out


def add(url, kind="", label=""):
    u = _norm(url)
    if not u:
        return {"ok": False, "error": "地址为空"}
    if not re.match(r"^https?://[A-Za-z0-9._\-]+(:\d+)?", u):
        return {"ok": False, "error": "地址格式不对（要以 http:// 或 https:// 开头）"}
    d = load()
    pubs = d.get("public") or []
    for e in pubs:
        if _norm(e.get("url") if isinstance(e, dict) else e) == u:
            return {"ok": False, "error": "这条地址已经在清单里了", "duplicate": True}
    k = kind or guess_kind(u)
    if k not in KIND_RANK:
        k = "other"
    pubs.append({"url": u, "label": (label or "").strip(), "kind": k,
                 "added": int(time.time()), "origin": "user"})
    d["public"] = pubs
    save(d)
    return {"ok": True, "added": u, "kind": k, "list": list_ordered()}


def remove(url):
    u = _norm(url)
    d = load()
    pubs = d.get("public") or []
    keep = [e for e in pubs if _norm(e.get("url") if isinstance(e, dict) else e) != u]
    if len(keep) == len(pubs):
        return {"ok": False, "error": "清单里没有这条地址"}
    d["public"] = keep
    save(d)
    return {"ok": True, "removed": u, "list": list_ordered()}


def set_lan(url):
    u = _norm(url)
    if u and not re.match(r"^https?://[A-Za-z0-9._\-]+(:\d+)?", u):
        return {"ok": False, "error": "地址格式不对"}
    d = load()
    d["lan"] = u or _default_lan()
    save(d)
    return {"ok": True, "list": list_ordered()}


def reset():
    d = _seed()
    save(d)
    return {"ok": True, "list": list_ordered()}


def config_lines(limit=4):
    """给通知用的多行文本（内网第一、FRP 第二、CF 第三）"""
    rows = list_ordered()[:limit]
    if not rows:
        base = _render_base() or (C.PUBLIC_BASE or "").rstrip("/")
        return ["配置地址：%s/hai/jsm.json" % base if base else "配置地址：-"]
    lines = ["配置地址："]
    for r in rows:
        lines.append("  %-4s %s" % (r["label"], r["jsm"]))
    # 附一行实测速度，让用户知道哪条最快（没测过就不添，避免「-」）
    try:
        from . import epbench as _eb
        s = _eb.summary_line()
        if s:
            lines.append("  " + s)
    except Exception:
        pass
    return lines


def _render_base():
    """当前生效的渲染口径（可被手动 pin / 自动故障切换改写）。

    延迟导入：endpoints ← renderbase ← endpoints 是**双向**的
    （renderbase.candidates 要读本模块的清单），模块级互导会炸。
    延迟到函数内解析即可，见 sources.py 踩过的 NameError 那一课。
    """
    try:
        from . import renderbase
        return renderbase.current()
    except Exception:
        return ""
