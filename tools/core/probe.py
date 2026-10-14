"""写入探针 —— 让「CK 到底送到哪去了」变成看得见的事实

为什么需要它
------------
网盘 CK 的采集天生有两处盲区：

  * 客户端扫码设置 CK 的动作发生在它自己身上（PG 的 csp_Config 页面是端上的
    python/JS，扫码走的是网盘官方协议），**不经过中枢**；
  * 播放网盘内容时，网盘 API 是直连（pan.quark.cn / drive.uc.cn …），
    **也不经过中枢**。

中枢唯一能看见的，是「客户端主动把东西送到中枢」的那些请求。

于是这里不再猜，而是把所有**可能**携带写入/查询意图的请求原样记下来：
  - 非 GET/HEAD 的方法（PUT / POST / PATCH / DELETE）
  - 带 query string 的请求（`?quark_cookie=...` 这种最容易被漏掉）
  - 带 CK 特征请求头的请求（Cookie / Authorization / X-CK …）
  - 路径里出现 tokenm / ck / cookie 的请求
  - body 非空的请求

只有当「端把一个带 CK 的请求打过来」时，中枢才可能入库 —— 这条日志就是用来
判定这件事有没有发生的。没有它，排查只能靠猜。
"""
import json
import os
import re
import time

from . import config as C
from . import store

_PATH_HINTS = ("tokenm", "/ck", "cookie", "token", "pan")
_HEADER_HINTS = ("cookie", "authorization", "x-ck", "x-cookie", "x-token",
                 "set-cookie", "proxy-authorization")
_BODY_MAX = 8192
_METHODS_ALWAYS = ("POST", "PUT", "PATCH", "DELETE")

# 这些请求是中枢自己在自测/下发/管理，记下来只会污染证据。
# 注意不要把 /api/ck/report 加进来 —— 那是真正的客户端上报入口。
_IGNORE_PATH = ("/api/access", "/api/probe", "/api/logs", "/api/status",
                "/api/ck/drop", "/api/ck/check", "/api/ck/version",
                "/api/relay", "/api/dav")


def _enabled():
    return bool(getattr(C, "PROBE_ENABLED", True))


def _wanted(method, path, query, headers, has_body):
    p = (path or "").lower()
    if any(x in p for x in _IGNORE_PATH):
        return False
    if method in _METHODS_ALWAYS:
        return True
    if has_body:
        return True
    if query:
        return True
    for h in headers:
        hl = h.lower()
        if hl in _HEADER_HINTS:
            v = headers[h] or ""
            if hl != "cookie" or len(v) > 8:
                return True
    return any(h in p for h in _PATH_HINTS)


def _pick_headers(headers):
    """只留下有诊断价值的头，避免把整包 header 灌进日志"""
    out = {}
    for k, v in (headers or {}).items():
        kl = k.lower()
        if kl in _HEADER_HINTS or kl in ("user-agent", "referer", "content-type",
                                         "origin", "host", "x-requested-with",
                                         "accept", "accept-encoding", "connection"):
            out[k] = str(v)[:600]
    return out


def record(method, path, query, headers, body, status, ip, ua="", note=""):
    """记一条。body 为 bytes/str/None。返回记录（未记则 None）"""
    if not _enabled():
        return None
    if isinstance(body, bytes):
        try:
            body_s = body.decode("utf-8", "replace")
        except Exception:
            body_s = repr(body[:512])
    elif body is None:
        body_s = ""
    else:
        body_s = str(body)

    has_body = bool(body_s.strip())
    if not _wanted(method, path, query, headers, has_body):
        return None

    rec = {
        "t": time.strftime("%Y-%m-%d %H:%M:%S"),
        "ip": ip or "",
        "m": method,
        "p": (path or "")[:300],
        "q": (query or "")[:2000],
        "s": status,
        "ua": (ua or "")[:200],
        "ct": (headers or {}).get("Content-Type") or (headers or {}).get("content-type") or "",
        "h": _pick_headers(headers),
        "blen": len(body_s),
        "b": body_s[:_BODY_MAX],
        "note": note,
    }
    try:
        os.makedirs(C.LOG_DIR, exist_ok=True)
        f = getattr(C, "F_PROBE", os.path.join(C.LOG_DIR, "probe.jsonl"))
        if os.path.exists(f) and os.path.getsize(f) > 4 * 1024 * 1024:
            os.replace(f, f + ".1")
        with open(f, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception as e:
        store.log_event("error", "探针写入失败: %s: %s" % (type(e).__name__, e))
    return rec


def tail(n=200, only_write=False, path_contains=None):
    f = getattr(C, "F_PROBE", os.path.join(C.LOG_DIR, "probe.jsonl"))
    if not os.path.exists(f):
        return []
    try:
        with open(f, encoding="utf-8") as fh:
            lines = fh.readlines()[-4000:]
    except Exception:
        return []
    out = []
    for ln in lines:
        try:
            r = json.loads(ln)
        except Exception:
            continue
        if only_write and r.get("m") in ("GET", "HEAD"):
            continue
        if path_contains and path_contains not in (r.get("p") or ""):
            continue
        out.append(r)
    return out[-n:]


def stats():
    """一句话结论：有没有客户端把 CK 送过来过"""
    rows = tail(2000)
    writes = [r for r in rows if r.get("m") not in ("GET", "HEAD")]
    with_ck = [r for r in rows if _looks_like_ck(r)]
    return {
        "total": len(rows),
        "writes": len(writes),
        "with_ck": len(with_ck),
        "last_write": writes[-1]["t"] if writes else "",
        "last_with_ck": with_ck[-1]["t"] if with_ck else "",
        "verdict": ("已捕获到客户端写入" if writes else
                    "未捕获到任何非 GET 写入 —— 说明端没有把 CK 送回中枢"),
    }


_CK_PAT = re.compile(
    r"(?i)(__pus|__kp|__puus|BDUSS|STOKEN|_UP_A4A_11_|quark_cookie|uc_cookie|"
    r"baidu_cookie|pan115_cookie|guangya_access_token|quark|baidu)")


def looks_like_ck(rec):
    """这条记录里有没有网盘凭据的痕迹"""
    blob = " ".join([rec.get("p", ""), rec.get("q", ""), rec.get("b", ""),
                     json.dumps(rec.get("h") or {}, ensure_ascii=False)])
    return bool(_CK_PAT.search(blob))


_looks_like_ck = looks_like_ck     # 兼容旧名
