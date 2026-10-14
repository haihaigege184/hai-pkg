# -*- coding: utf-8 -*-
"""外部中转（relay）：把端本该直连第三方的请求引到中枢。

为什么需要
----------
PG 的 tokenm.json 里有一批「指向外部服务」的字段，例如：

    "open_api_url": "postparam|http://api.extscreen.com/aliyundrive/token"

端会把「扫码换来的凭据」POST 到这个词条里的 URL 去换 access_token —— 这一步
直连第三方，中枢看不见。这正是「手机扫码设好了 CK，中枢却毫无反应」的根因
（扫码动作走网盘官方协议、播放流量直连网盘 CDN，两条路都不经过中枢）。

把这个 URL 改成中枢自己的 /gw/<tag>/<path>，中枢就能：

  1. 完整记录这一次请求（凭据就在 body / query / header 里）；
  2. 原样透传给真正的上游，端的登录与播放功能完全不受影响；
  3. 从请求与响应里提取凭据入库 → 其余设备下一次拉配置就自动生效。

约束
----
* **只有 data/relay.json 里显式登记过的路由才转发**，否则中枢就成了开放代理
  （SSRF）。未登记的路由一律交回原有的域名池反代逻辑。
* 纯透传：不改写响应体、不跟随重定向、不缓存。
* 透传失败不影响端：把上游的真实响应（含 4xx/5xx）原样回给端，让它自己决定
  重试还是回退。
"""
import json
import os
import re
import time
import urllib.parse

import requests
import urllib3

from . import config as C

urllib3.disable_warnings()

# 与 gateway.py 保持一致的 hop-by-hop 头集合
_HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
        "te", "trailers", "transfer-encoding", "upgrade", "host",
        "accept-encoding", "content-length"}

MAX_BODY = 4 * 1024 * 1024        # 单次中转的响应上限
MAX_LOG_TEXT = 8192               # 单条记录里 req/resp 的截断长度
LOG_MAX_BYTES = 8 * 1024 * 1024   # relay.jsonl 超过即截断保留后半

# ---------------------------------------------------------------------------
# 内置路由
#
# upstream    真正的上游地址（原版 tokenm.json 里的默认值）
# field       tokenm.json 里承载该地址的字段名（重写时用）
# verb        PG 的 ext 协议动词前缀（重写时保留）
# channel     提取得来的凭据归到哪个渠道（ckvault.CHANNELS 的 key）
# map         请求/响应 JSON 里的字段名 → tokenm.json 字段名
# ---------------------------------------------------------------------------
DEFAULT_ROUTES = {
    "ali/token": {
        "label": "阿里云盘 open_api（凭据 ⇄ access_token）",
        "enabled": True,
        "upstream": "http://api.extscreen.com/aliyundrive/token",
        "field": "open_api_url",
        "verb": "postparam",
        "channel": "aliyun",
        # 端 POST 上来的与上游返回的都是标准 OAuth 字段名，
        # 定向映射到 ckvault 认识的两个字段上（不做全局别名，避免误伤别的渠道）
        "map": {"refresh_token": "open_token",
                "access_token": "token",
                "open_token": "open_token",
                "token": "token"},
    },
}


# ------------------------------------------------------------------ 路由表


def _load_file():
    try:
        with open(C.F_RELAY, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def save_file(d):
    try:
        os.makedirs(os.path.dirname(C.F_RELAY), exist_ok=True)
        tmp = C.F_RELAY + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=1)
        os.replace(tmp, C.F_RELAY)
    except Exception:
        pass


def routes():
    """内置路由 + 文件覆盖（文件里可以改名/改上游/禁用）"""
    out = {k: dict(v) for k, v in DEFAULT_ROUTES.items()}
    f = _load_file()
    for k, v in (f.get("routes") or {}).items():
        if isinstance(v, dict):
            base = out.get(k, {})
            base.update(v)
            out[k] = base
    return out


def match(tag, path):
    """把 /gw/<tag>/<path> 映射到路由；未登记返回 None"""
    key = "/".join(x for x in (tag, path) if x)
    rs = routes()
    cfg = rs.get(key)
    if cfg is None:
        # 支持 "ali/*" 前缀通配
        for k, v in rs.items():
            if k.endswith("/*") and key.startswith(k[:-1]):
                cfg = v
                break
    if not cfg or not cfg.get("enabled", True) or not cfg.get("upstream"):
        return None
    return key, cfg


def active():
    return {k: v for k, v in routes().items()
            if v.get("enabled", True) and v.get("upstream")}


def rewrite_enabled():
    """是否把 tokenm 里的外发地址改写到中枢网关。

    优先级：data/relay.json 的 rewrite 字段 > 环境变量 OKTV_RELAY_REWRITE。
    """
    f = _load_file()
    if "rewrite" in f:
        return bool(f["rewrite"])
    return bool(C.RELAY_REWRITE)


def set_rewrite(on):
    f = _load_file()
    f["rewrite"] = bool(on)
    save_file(f)
    return rewrite_enabled()


def set_route_enabled(key, on):
    f = _load_file()
    f.setdefault("routes", {})[key] = dict(f.get("routes", {}).get(key) or {},
                                           enabled=bool(on))
    save_file(f)
    return routes().get(key, {})


# ------------------------------------------------------------------ 采集


def _trim(s, n=MAX_LOG_TEXT):
    s = s if isinstance(s, str) else ""
    return s if len(s) <= n else s[:n] + f"…(+{len(s) - n}B)"


def _map_fields(text, mapping):
    """按路由声明的字段映射，从 JSON / form 文本里定向取值。"""
    if not text or not mapping:
        return {}
    t = text.strip()
    obj = None
    if t.startswith("{") and t.endswith("}"):
        try:
            obj = json.loads(t)
        except Exception:
            obj = None
    if isinstance(obj, dict):
        return {dst: str(obj[src]).strip() for src, dst in mapping.items()
                if str(obj.get(src) or "").strip()}
    if t and ("=" in t) and len(t) < 65536:
        try:
            pairs = urllib.parse.parse_qsl(t, keep_blank_values=True)
        except Exception:
            pairs = []
        flat = {}
        for k, v in pairs:
            flat.setdefault(k, v)
        return {dst: flat[src].strip() for src, dst in mapping.items()
                if str(flat.get(src) or "").strip()}
    return {}


def harvest(req_text, resp_text, cfg, source):
    """从请求体与响应体里提取凭据并入库，返回命中的字段名列表"""
    from . import ckvault            # 延迟 import，避免与 ckvault 循环依赖

    ch = cfg.get("channel")
    if ch not in ckvault.CHANNELS:
        return []
    mapping = cfg.get("map") or {}
    fields = {}
    # 响应优先级更高（是刚签发的、最新鲜的那一份）
    for text in (resp_text, req_text):
        got = _map_fields(text, mapping) if mapping else {}
        if not got:
            for c, f, _s in ckvault.extract(text or "", source=source, hint=ch):
                if c == ch:
                    got.update(f)
        for k, v in got.items():
            if v and len(v) >= 8:
                fields.setdefault(k, v)
    if not fields:
        return []
    ckvault.report(ch, fields, source=source, validate=False)
    return sorted(fields)


# ------------------------------------------------------------------ 记录


def _record(rec):
    try:
        os.makedirs(os.path.dirname(C.F_RELAY_LOG), exist_ok=True)
        try:
            if os.path.getsize(C.F_RELAY_LOG) > LOG_MAX_BYTES:
                with open(C.F_RELAY_LOG, "rb") as f:
                    f.seek(-LOG_MAX_BYTES // 2, os.SEEK_END)
                    tail = f.read()
                with open(C.F_RELAY_LOG, "wb") as f:
                    f.write(tail[tail.find(b"\n") + 1:])
        except OSError:
            pass
        with open(C.F_RELAY_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass


def recent(n=100):
    out = []
    try:
        with open(C.F_RELAY_LOG, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except Exception:
                    continue
    except Exception:
        return []
    return out[-n:][::-1]


def stats():
    rows = recent(500)
    last = rows[0] if rows else {}
    hit = [r for r in rows if r.get("hit")]
    return {
        "total": len(rows),
        "with_cred": len(hit),
        "last": last.get("t", ""),
        "last_key": last.get("key", ""),
        "last_hit": (hit[0].get("hit") if hit else []),
        "routes": sorted(active().keys()),
        "rewrite": bool(C.RELAY_REWRITE),
    }


# ------------------------------------------------------------------ 透传


def handle(tag, path, method, headers, body, query, ip=""):
    """执行一次中转。返回 (body_bytes, status, headers) 或 None（未登记路由）"""
    m = match(tag, path)
    if not m:
        return None
    key, cfg = m

    up = cfg["upstream"]
    url = up + (("?" + query) if query else "")

    fwd = {k: v for k, v in (headers or {}).items() if k.lower() not in _HOP}
    fwd["X-Relayed-By"] = "oktv-hub"
    parsed = urllib.parse.urlparse(up)
    fwd["Host"] = parsed.netloc

    t0 = time.time()
    try:
        r = requests.request(method, url, headers=fwd, data=body,
                             timeout=20, verify=False, allow_redirects=False)
        code, content = r.status_code, (r.content or b"")
        out_headers = {k: v for k, v in r.headers.items()
                       if k.lower() not in ("content-encoding", "content-length",
                                            "transfer-encoding", "connection")}
        err = ""
    except Exception as e:
        code, content, out_headers = 502, b"", {}
        err = "%s: %s" % (type(e).__name__, e)

    req_text = ""
    if body:
        try:
            req_text = body.decode("utf-8", "replace")
        except Exception:
            req_text = ""
    if not req_text and query:
        req_text = query
    resp_text = ""
    if content and len(content) <= MAX_BODY:
        try:
            resp_text = content.decode("utf-8", "replace")
        except Exception:
            resp_text = ""

    src = "relay %s" % key
    hit = []
    if not err:
        try:
            hit = harvest(req_text, resp_text, cfg, src)
        except Exception as e:
            hit = []
            from . import store
            store.log_event("error", "relay 采集异常 %s: %s" % (type(e).__name__, e))

    _record({
        "t": time.strftime("%Y-%m-%d %H:%M:%S"),
        "ip": ip,
        "key": key,
        "up": up,
        "m": method,
        "q": _trim(query, 512),
        "ct": (headers or {}).get("Content-Type", ""),
        "req": _trim(req_text),
        "status": code,
        "ms": int((time.time() - t0) * 1000),
        "resp": _trim(resp_text),
        "hit": hit,
        "err": err,
    })

    if hit:
        from . import store
        store.log_event("ck", "中转 %s 捕获凭据: %s（来源 %s）"
                              % (key, ",".join(hit), ip or "-"))

    return content, code, out_headers


# ------------------------------------------------------------------ tokenm 改写


def patch_tokenm(obj, hub):
    """把 tokenm 里指向外部的地址改写到中枢网关（仅在开关打开时）。

    hub 必须是**中枢根地址**（协议+主机[+挂载前缀]），不能带配置根目录：
    /gw/<tag>/<path> 是中枢的绝对路由，拼成 `<hub>/hai/gw/...` 会 404。

    hub 为空时不改写 —— 否则端会拿到一个相对地址，反而解析不出来。
    """
    if not rewrite_enabled() or not hub:
        return obj
    if not isinstance(obj, dict):
        return obj
    for key, cfg in active().items():
        field = cfg.get("field")
        if not field or field not in obj:
            continue
        cur = str(obj.get(field) or "")
        # 保留原有动词前缀（postparam| / postjson: / 无）
        verb = cfg.get("verb") or ""
        if not verb:
            mm = re.match(r"^([A-Za-z_]+)[|:]", cur)
            verb = mm.group(1) if mm else ""
        sep = "|" if verb else ""
        obj[field] = f"{verb}{sep}{hub.rstrip('/')}/gw/{key}"
    return obj
