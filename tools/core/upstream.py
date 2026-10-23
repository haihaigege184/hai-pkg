"""上游 TVBox 配置的抓取与解析。

为什么单独一个模块
──────────────────
上游配置不止「标准 JSON」一种形态，实测最少四种：

  ① 标准 JSON —— 社区 GitHub 镜像，json.loads 直接可用；
  ② **带 JS 注释** —— 形如 `//{"key":"原创",...}` 把某些条目注释掉（饭太硬、
     王二小都有）。直接 json.loads 会报 `Expecting value: line 13 column 1
     (char 1875)`，看起来像「配置坏了」，其实只是注释；
  ③ **图片藏配置** —— `/tv` 返回一张 JPEG，真正的配置挂在 JPEG 的 EOI
     (`\\xff\\xd9`) 之后，形如 `bCCtSiKQ**<base64(JSON)>`。端能识别，
     我们直接读只会拿到 `b'\\xff\\xd8\\xff\\xe0...'`；
  ④ **HTML 跳转页** —— gh-proxy / ghfast 这类加速站先回一个 549 字节的
     `<script>window.location.replace(...)</script>`，真实内容在下一跳。

旧实现只做「json.loads 成功才收」，所以 ②③④ 这三类源在我们这里**等于不存在**，
这也是「上游同步一直 0 新增」的另一半原因（另一半是社区源本身与池内重合）。

本模块把四种形态统一成一个 `load(url) -> dict`。
"""
import base64
import hashlib
import json
import os
import re
import time

import requests

from . import config as C

_JPEG_SOI = b"\xff\xd8"
_JPEG_EOI = b"\xff\xd9"
_BMP_MAGIC = b"BM"
_B64_RE = re.compile(rb"[^A-Za-z0-9+/=]")
_REDIR_RE = re.compile(r"""(?:location\.(?:replace|href)\s*=\s*|location\s*=\s*)["']([^"']+)["']""", re.I)

# ── TVBox 客户端加密配置 ────────────────────────────────────────────────
# 南风 XC.json 就是这一种：文件本身是 hex 文本，解一层 hex 后得到
#     `$#248#$` + AES-CBC 密文 + 13 字节毫秒时间戳
# 端上（ApiConfig.FindResult / AES.CBC）就是这么解的 —— 我们照抄它的规则，
# 不是「硬解/破译」，所以不会因为对方换 key 就失效（key 写在数据里）。
_HEX_ONLY = re.compile(rb"^[0-9a-fA-F\s]+$")
_TVBOX_PREFIX = re.compile(rb"^\$#([^#]{1,32})#\$")

try:                                   # pycryptodome（换包机与线上都装了）
    from Crypto.Cipher import AES as _AES
except Exception:                      # pragma: no cover - 缺库时降级
    _AES = None


def _pad_key(s, size):
    """key/iv 右补 `0` 到 size 位（TVBox 客户端的规则，不是 PKCS#5 补位）。"""
    b = s if isinstance(s, bytes) else str(s).encode()
    return (b + b"0" * size)[:size]


def _aes_cbc(key, iv, data):
    k = _pad_key(key, 16 if len(str(key)) <= 16 else (24 if len(str(key)) <= 24 else 32))
    c = _AES.new(k, _AES.MODE_CBC, _pad_key(iv, 16))
    out = c.decrypt(data)
    n = out[-1] if out else 0
    if 1 <= n <= 16 and out[-n:] == bytes([n]) * n:
        out = out[:-n]
    return out


def _aes_ecb(key, data):
    k = _pad_key(key, 16 if len(str(key)) <= 16 else (24 if len(str(key)) <= 24 else 32))
    out = _AES.new(k, _AES.MODE_ECB).decrypt(data)
    n = out[-1] if out else 0
    if 1 <= n <= 16 and out[-n:] == bytes([n]) * n:
        out = out[:-n]
    return out


def decode_tvbox(body, ecb_key=""):
    """把「hex + $#key#$ + AES」的加密配置还原成明文，失败返回 None。

    两种变体（都是 TVBox 安卓端原生支持的，不是我们发明的）：

      格式 2（南风用的这个）
        hex 解码 → `$#<key>#$` + 密文 + **末尾 13 位数字**（毫秒时间戳）
        key = 前缀里那串，右补 `0` 到 16 位；iv = 末尾那 13 位，同样右补 `0` 到 16 位
        → AES/CBC/PKCS7，密文长度必是 16 的倍数

      格式 3（地址上带 `;pk;<key>`）
        hex 解码后直接是密文 → AES/ECB/PKCS7，key 来自 URL 后缀

    实测（2026-10-22）：南风 `XC.json` 解出 88 个站点、1 条直播、7 个解析。
    """
    if _AES is None:
        return None
    s = body.strip()
    if not s or not _HEX_ONLY.match(s):
        return None
    try:
        raw = bytes.fromhex(re.sub(rb"\s+", b"", s).decode())
    except Exception:
        return None
    m = _TVBOX_PREFIX.match(raw)
    if m:
        key = m.group(1).decode("utf-8", "ignore")
        pay = raw[m.end():]
        if len(pay) <= 13 or (len(pay) - 13) % 16:
            return None
        body_, iv = pay[:-13], pay[-13:]
        try:
            txt = _aes_cbc(key, iv, body_).decode("utf-8", "ignore")
        except Exception:
            return None
        return txt if txt.strip().startswith(("{", "//", "[")) else None
    if ecb_key and len(raw) % 16 == 0:
        try:
            txt = _aes_ecb(ecb_key, raw).decode("utf-8", "ignore")
        except Exception:
            return None
        return txt if txt.strip().startswith(("{", "//", "[")) else None
    return None


def strip_comments(s):
    """去掉 JSON 里的 `//` 行注释与 `/* */` 块注释。

    必须**字符串感知**：`https://...` 里也有 `//`，无脑正则替换会把所有 URL
    砍成 `https:`，配置直接废掉。
    """
    out = []
    i, n = 0, len(s)
    instr = False
    esc = False
    while i < n:
        c = s[i]
        if instr:
            out.append(c)
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                instr = False
            i += 1
            continue
        if c == '"':
            instr = True
            out.append(c)
            i += 1
            continue
        if c == "/" and i + 1 < n and s[i + 1] == "/":
            while i < n and s[i] not in "\r\n":
                i += 1
            continue
        if c == "/" and i + 1 < n and s[i + 1] == "*":
            i += 2
            while i + 1 < n and not (s[i] == "*" and s[i + 1] == "/"):
                i += 1
            i += 2
            continue
        out.append(c)
        i += 1
    return "".join(out)


def decode_image(body):
    """从「图片藏配置」里取出 JSON 文本。

    实测饭太硬的格式：JPEG 正常结束(ffd9) 之后追加
        bCCtSiKQ**<base64>
    前缀那 8 个字节是随机的干扰串，`**` 才是分隔符。取 `**` 之后、
    只保留 base64 字符集，再补齐 `=` 即可解出 JSON。
    """
    i = body.rfind(_JPEG_EOI)
    if i < 0:
        return None
    tail = body[i + 2:]
    k = tail.find(b"**")
    payload = tail[k + 2:] if k >= 0 else tail
    payload = _B64_RE.sub(b"", payload)
    if not payload:
        return None
    raw = __import__("base64").b64decode(payload + b"=" * (-len(payload) % 4))
    return raw.decode("utf-8", "ignore")


def parse_text(text):
    """文本 → dict。会先剥注释再 loads，失败返回 None。

    最后一档用 `strict=False`：实测小盒子4K（xhztv.top/4k.json）里字符串中
    带**未转义的控制字符**（真实换行/制表符），标准 json 直接报
    `Invalid control character at: line 682` —— 内容本身没错，端上也能吃，
    是我们解析器太严。strict=False 放开这一条即为合法 JSON。
    """
    if not text:
        return None
    stripped = strip_comments(text)
    for candidate in (text, stripped):
        for kwargs in ({}, {"strict": False}):
            try:
                d = json.loads(candidate, **kwargs)
            except Exception:
                continue
            if isinstance(d, dict):
                return d
    return None


def parse_body(body, ecb_key=""):
    """响应体 → (dict|None, note)。自动识别各种形态。"""
    if not body:
        return None, "空响应"
    if body[:2] == _JPEG_SOI or body[:2] == _BMP_MAGIC:
        d = parse_text(decode_image(body))
        return (d, "图片藏配置") if d else (None, "图片里没解出 JSON")
    # 加密配置（hex + $#key#$ + AES）：南风 XC.json 就是这种，先于文本判定
    if _HEX_ONLY.match(body[:256]):
        txt = decode_tvbox(body, ecb_key)
        if txt:
            d = parse_text(txt)
            if d:
                return d, "加密配置(hex+AES)"
            return None, "加密已解开但 JSON 无效"
        return None, "疑似加密配置，解不开（缺 pycryptodome 或格式变了）"
    d = parse_text(body.decode("utf-8", "ignore"))
    return (d, "json") if d else (None, "")


def _cache_dir():
    d = os.path.join(getattr(C, "DATA_DIR", os.path.join(C.BASE_DIR, "data")), "upstream_cache")
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        pass
    return d


def _cache_file(url):
    return os.path.join(_cache_dir(), hashlib.md5(url.encode("utf-8")).hexdigest() + ".bin")


def cache_put(url, body):
    """把抓成功的原始响应落盘。

    为什么值得存：站方一挂（换域名/被墙/DNS 被删）我们这条源就直接归零，
    但配置内容本身不会一夜之间作废。留一份最近成功的原文，抓不到时继续用，
    note 里标「缓存(日期)」，维护页能一眼看出这条源已经多久没更新。
    """
    try:
        p = _cache_file(url)
        tmp = p + ".tmp"
        with open(tmp, "wb") as f:
            f.write(body)
        os.replace(tmp, p)
    except Exception:
        pass


def cache_get(url):
    """读缓存，返回 (body, 时间戳) 或 (None, 0)。"""
    p = _cache_file(url)
    if not os.path.exists(p):
        return None, 0
    try:
        with open(p, "rb") as f:
            return f.read(), int(os.path.getmtime(p))
    except Exception:
        return None, 0


def _get(url, timeout, ua=None):
    return requests.get(
        url,
        timeout=timeout,
        verify=False,
        headers={"User-Agent": ua or getattr(C, "UPSTREAM_UA", "okhttp/5.0.0-alpha.14"),
                 "Accept": "application/json,text/plain,*/*"},
        allow_redirects=True,
    )


def _fetch_once(url, timeout, ua, hops, ecb_key=""):
    cur = url
    for _ in range(max(1, hops + 1)):
        try:
            r = _get(cur, timeout, ua)
        except Exception as e:
            return None, cur, "%s: %s" % (type(e).__name__, str(e)[:80])
        if r.status_code != 200 or not r.content:
            return None, cur, "HTTP %s" % r.status_code
        body = r.content
        d, note = parse_body(body, ecb_key)
        if d:
            cache_put(cur, body)
            return d, cur, note
        # 跳转页：<script>location.replace("...")</script>
        txt = body.decode("utf-8", "ignore") if len(body) < 8192 else ""
        m = _REDIR_RE.search(txt) if txt else None
        if m:
            nxt = m.group(1).replace("\\/", "/")
            if nxt.startswith("//"):
                nxt = "https:" + nxt
            if nxt.startswith("http"):
                cur = nxt
                continue
        if note:
            return None, cur, note
        return None, cur, "非 JSON（%d 字节，头部 %r）" % (len(body), body[:32])
    return None, cur, "跳转过多"


def fetch_config(url, timeout=None, hops=2):
    """抓一个上游配置，返回 (dict|None, final_url, note)。

    【UA 是硬门槛 —— 2026-10-22 实测】
      饭太硬/王二小这类站**按 UA 分流**：
        okhttp/*  → 真配置（JSON / 图片藏配置）
        浏览器 UA → 公告页（936B GBK 文本）或导航页 HTML（12KB）
      用普通浏览器 UA 去抓，永远只会拿到「大家好！我是唐三…」这种公告。
      所以这里先按设备 UA（okhttp）试，解析不出来再退回浏览器 UA。

    note 里写清是怎么解出来的，便于维护页排查：
        json / 图片藏配置 / 加密配置(hex+AES) / 跳转→json / 缓存(10-21) ...

    地址里带 `;pk;<key>` 时按「格式 3」（hex + AES/ECB）解，抓取时会先把它从
    URL 上摘掉 —— 那个后缀只是给客户端看的密钥标记，不是真路径。
    """
    timeout = timeout or getattr(C, "UPSTREAM_TIMEOUT", 10)
    ecb_key = ""
    if ";pk;" in url:
        url, _, ecb_key = url.partition(";pk;")
    uas = [getattr(C, "UPSTREAM_UA", "okhttp/5.0.0-alpha.14"), C.UA]
    last = (None, url, "未尝试")
    for i, ua in enumerate(uas):
        d, final, note = _fetch_once(url, timeout, ua, hops, ecb_key)
        if d:
            return d, final, note if i == 0 else note + "（浏览器 UA）"
        last = (d, final, note)
        # 只有「明确拿到页面但不是 JSON」才值得换 UA 再试；
        # 网络错误、DNS 没了、加密解不开，换 UA 都没用
        if not (note.startswith("非 JSON") or note.startswith("图片里没解出")):
            break
    # 全挂了 → 退回最近一次成功的缓存（站方挂了不等于内容作废）
    raw, ts = cache_get(url)
    if raw:
        d, note = parse_body(raw, ecb_key)
        if d:
            day = time.strftime("%m-%d %H:%M", time.localtime(ts))
            return d, url, "缓存(%s)｜本次失败：%s" % (day, last[2][:40])
    return last


def sites_of(cfg):
    """取 sites 列表（顺手把非 dict 项剔掉）。"""
    if not isinstance(cfg, dict):
        return []
    out = []
    for s in cfg.get("sites") or []:
        if isinstance(s, dict):
            out.append(s)
    return out
