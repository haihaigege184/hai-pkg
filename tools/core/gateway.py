"""网关反代：/gw/<TAG>/<path> -> 当前最优存活域名

存在的意义：把 APP 里写死的 ext 地址指向中枢自身，
即使上游站点再次换域名，只要中枢在跑，接口就一直可用。

处理要点
  * 原样透传方法/请求体/查询串
  * Host 改写为上游主机，避免站点因 Host 校验拒绝
  * Location 重定向回写成中枢网关地址，避免 APP 被甩到真实域名（也防止其被墙）
  * Set-Cookie 去掉 Domain 属性，让 cookie 落在中枢域下，保持会话不丢
"""
import urllib.parse

import requests
import urllib3

from . import config as C
from . import health
from . import renderbase

urllib3.disable_warnings()

_HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
        "te", "trailers", "transfer-encoding", "upgrade", "host",
        "accept-encoding"}

MAX_BODY = 16 * 1024 * 1024      # 网关单次响应上限（接口都是检索/解析类，不至于更大）


_POOL = {}          # 由 app.bootstrap() 注入，避免与主模块循环依赖


def set_pool(pool):
    global _POOL
    _POOL = pool


def _pool():
    return _POOL


def resolve_target(tag):
    """返回当前最优域名; 无可用返回 None"""
    cands = candidates(tag)
    return cands[0] if cands else None


def candidates(tag):
    """按延迟升序返回该 TAG 下所有存活域名（用于故障转移）"""
    ent = _pool().get(tag)
    if not ent:
        return []
    alive = [d for d in ent.get("domains", []) if d.get("status") == "alive"]
    alive.sort(key=lambda d: (-(d.get("quality") or 0),
                              d.get("latency") or 99999, d.get("url", "")))
    return [d["url"] for d in alive]


def proxy(tag, path, method, headers, body, query):
    cands = candidates(tag)
    if not cands:
        return None, 502, "no alive domain", {}

    last_err = "unknown"
    for base in cands:
        out = _try_one(base, tag, path, method, headers, body, query)
        if out[0] is not None:
            return out
        last_err = out[2]
    return None, 502, last_err, {}


def _try_one(base, tag, path, method, headers, body, query):
    url = base.rstrip("/") + "/" + path.lstrip("/")
    if query:
        url += "?" + query

    fwd = {k: v for k, v in headers.items()
           if k.lower() not in _HOP and k.lower() != "content-length"}
    fwd["User-Agent"] = C.UA
    parsed = urllib.parse.urlparse(base)
    fwd["Host"] = parsed.hostname or ""

    try:
        r = requests.request(method, url, headers=fwd, data=body,
                             timeout=30, verify=False, allow_redirects=False,
                             stream=False)
    except Exception as e:
        return None, 502, f"upstream error: {type(e).__name__}", {}

    # 5xx 视为该域名故障，交给下一个候选
    if r.status_code >= 500:
        return None, 502, f"upstream {r.status_code}", {}

    # stream=False 时 requests 已按 Content-Encoding 解压，
    # 因此下面必须剥掉 content-encoding，否则客户端会二次解压导致乱码。
    content = r.content or b""
    if len(content) > MAX_BODY:
        return None, 502, f"upstream body too large ({len(content)}B)", {}

    out_headers = {}
    for k, v in r.headers.items():
        lk = k.lower()
        if lk in ("content-encoding", "content-length", "transfer-encoding",
                  "connection"):
            continue
        if lk == "location":
            v = _rewrite_location(v, base, tag)
        if lk == "set-cookie":
            v = _clean_cookie(v)
        out_headers[k] = v

    return content, r.status_code, None, out_headers


def _rewrite_location(loc, base, tag):
    """把上游绝对跳转改写回中枢网关路径"""
    try:
        p = urllib.parse.urlparse(loc)
        if not p.netloc:
            return loc
        # 用实际进来那个请求的 base（app.py 传的是 request 的 Host 推导值），
        # 而不是全局 PUBLIC_BASE —— 客户端从哪条入口进，跳转就该回到哪条，
        # 否则在 CF 入口下发的 Location 会指向 gost，跨隧道跟丢。
        # 拿不到 base 时才回落渲染口径。
        pre = (base or "").rstrip("/") or renderbase.current()
        if not pre:
            return loc
        tail = p.path.lstrip("/")
        if p.query:
            tail += "?" + p.query
        return f"{pre}/gw/{tag}/{tail}"
    except Exception:
        return loc


def _clean_cookie(c):
    parts = [p.strip() for p in c.split(";")]
    keep = [p for p in parts if not p.lower().startswith("domain=")]
    return "; ".join(keep)
