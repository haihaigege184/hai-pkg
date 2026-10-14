"""网盘 CK 保险库：一处保存，全屋自动生效

为什么是这个设计
----------------
整包 79 个站点里有 59 个的 ext 第一段是 `./lib/tokenm.json`，
夸克 / UC / 百度 / 115 / 迅雷 / 移动 / PikPak / 123 / 189 / 阿里 的 CK
全部由这一个文件承载（字段全集见 lib/tokentemplate.json）。

而 `./lib/...` 是相对「配置地址」解析的 —— 每台客户端都会自己来中枢取这个文件。
所以「CK 一处保存、多端自动调用」只需要把 tokenm.json 做成动态渲染：

    GET /hai/lib/tokenm.json  ->  模板（字段全集/默认值） + 保险库当前最优 CK

覆盖策略（对应「有效新 CK 自动覆盖旧 CK」）
  * valid        覆盖一切（旧值进 history）
  * unverified   只填空位；已有 valid 时不覆盖
  * invalid      绝不覆盖 valid；若当前就是它，则回退到 history 里最近一次 valid
  * 播放成功（客户端带 CK 请求经中枢且 2xx）=> 该渠道 hits+1 并升级为 valid

安全
----
真实 CK 只回给**局域网来源**；公网来源只拿到空模板。
（公网入口 hai.haihaigege.dpdns.org 的 ingress 只放行 /hai 与 /gw，
 所以 /api/ck、/ck 页面本身在公网不可达；这条规则是为 /hai/lib/tokenm.json 兜底。）
"""
import json
import os
import re
import time
import urllib.parse

from . import ckcheck
from . import config as C
from . import store

# ------------------------------------------------------------------ 渠道定义

# field -> 该字段属于哪个渠道；kind=cookie 表示是一整条 Cookie 串
CHANNELS = {
    "quark":   {"label": "夸克网盘", "kind": "cookie",
                "fields": ["quark_cookie"],
                "hosts": ["pan.quark.cn", "drive-pc.quark.cn", "drive.quark.cn", "quark.cn"],
                "shapes": [r"__pus=", r"__kp=", r"__uid="],
                "aliases": ["quark", "夸克"]},
    "uc":      {"label": "UC网盘", "kind": "cookie",
                "fields": ["uc_cookie"],
                "hosts": ["drive.uc.cn", "pc-api.uc.cn", "drive-pc.uc.cn", "uc.cn"],
                "shapes": [r"_UP_A4A_11_", r"__puus="],
                "aliases": ["uc_cookie", "ucdrive", "uc网盘"]},
    "baidu":   {"label": "百度网盘", "kind": "cookie",
                "fields": ["baidu_cookie"],
                "hosts": ["pan.baidu.com", "pan.baidu.com", "baidupcs.com", "baidu.com"],
                "shapes": [r"BDUSS", r"STOKEN", r"PANWEB"],
                "aliases": ["baidu", "百度", "bdstoken"]},
    "115":     {"label": "115网盘", "kind": "cookie",
                "fields": ["pan115_cookie"],
                "hosts": ["115.com", "115cdn.com", "anxia.com"],
                "shapes": [r"(?:^|;\s*)UID=", r"(?:^|;\s*)SEID=", r"(?:^|;\s*)KID="],
                "aliases": ["115", "115网盘"]},
    "aliyun":  {"label": "阿里云盘", "kind": "token",
                "fields": ["open_token", "token"],
                "hosts": ["alipan.com", "aliyundrive.com", "aliyundrive.net"],
                "shapes": [],
                "aliases": ["aliyun", "alipan", "aliyundrive", "阿里云盘"]},
    "thunder": {"label": "迅雷云盘", "kind": "account",
                "fields": ["thunder_username", "thunder_password", "thunder_captchatoken"],
                "hosts": ["pan.xunlei.com", "xunlei.com", "thunder.com"],
                "shapes": [],
                "aliases": ["thunder", "迅雷", "xunlei"]},
    "yd":      {"label": "移动云盘", "kind": "token",
                "fields": ["yd_auth"],
                "hosts": ["yun.139.com", "caiyun.139.com", "139.com"],
                "shapes": [],
                "aliases": ["yd_auth", "移动云盘", "139"]},
    "pikpak":  {"label": "PikPak", "kind": "account",
                "fields": ["pikpak_username", "pikpak_password"],
                "hosts": ["mypikpak.com", "pikpak.me", "pikpak.com"],
                "shapes": [],
                "aliases": ["pikpak"]},
    "pan123":  {"label": "123云盘", "kind": "account",
                "fields": ["pan123_username", "pan123_password"],
                "hosts": ["123pan.com", "123684.com", "123912.com"],
                "shapes": [],
                "aliases": ["123pan", "123云盘"]},
    "pan189":  {"label": "天翼云盘", "kind": "account",
                "fields": ["pan189_username", "pan189_password"],
                "hosts": ["cloud.189.cn", "189.cn"],
                "shapes": [r"COOKIE_LOGIN_USER"],
                "aliases": ["189", "天翼", "cloud.189"]},
    "guangya": {"label": "光雅网盘", "kind": "token",
                "fields": ["guangya_access_token", "guangya_refresh_token"],
                "hosts": ["guangya"],
                "shapes": [],
                "aliases": ["guangya"]},
}

ORDER = ["quark", "uc", "baidu", "115", "aliyun", "thunder",
         "yd", "pikpak", "pan123", "pan189", "guangya"]

# 字段名 -> 渠道（用于识别 `quark_cookie=xxx` 这种文本）
_FIELD2CH = {}
for _ch, _def in CHANNELS.items():
    for _f in _def["fields"]:
        _FIELD2CH[_f] = _ch

# 用于「扫描任意文本」的字段名匹配。
# 注意 value 不能用 [^\s;]+ —— Cookie 串本身就带 "; " 和空格，
# 那样会把 `quark_cookie=__pus=a; __kp=b` 截成 `__pus=a`。
# 这里取到行尾（URL 场景下再被 & 截断），所以 `a=1&quark_cookie=X&b=2` 也能正确取 X。
_CK_FIELD_RE = re.compile(
    r"(?i)(?:^|[&\s,;])(?P<k>" + "|".join(sorted(_FIELD2CH, key=len, reverse=True)) + r")"
    r"\s*[=:]\s*(?:\"(?P<q>[^\"]+)\"|'(?P<s>[^']+)'|(?P<v>[^\n\r&]+))")

_URL_PARAM_KEYS = {"cookie", "ck", "ckstr", "token", "auth", "access_token",
                   "quark_cookie", "baidu_cookie", "pan115_cookie", "uc_cookie"}

_HIST_MAX = 5


# ------------------------------------------------------------------ 基础读写


def _now():
    return int(time.time())


def _mask(v):
    v = str(v or "")
    if len(v) <= 12:
        return "*" * len(v)
    return v[:6] + "…" + v[-4:] + f"({len(v)})"


def _empty():
    return {"version": 0, "updated": 0, "items": {}}


def load():
    d = store.load(C.F_CK, None)
    if not isinstance(d, dict) or "items" not in d:
        return _empty()
    d.setdefault("version", 0)
    d.setdefault("updated", 0)
    if not isinstance(d["items"], dict):
        d["items"] = {}
    return d


def save(d):
    d["updated"] = _now()
    store.save(C.F_CK, d)
    return d


def bump(d):
    d["version"] = int(d.get("version") or 0) + 1
    return d


def version():
    return int(load().get("version") or 0)


# ------------------------------------------------------------------ 入库


def _norm_source(source):
    return str(source or "")[:120]


def report(channel, fields, source="", state=None, note="", force=False,
           validate=True, count_use=False):
    """写入一个渠道的 CK。

    fields: {field: value}；也接受单值字符串（此时用该渠道主字段）
    返回 {ok, action, state, note, channel}
    """
    ch = (channel or "").strip().lower()
    if ch not in CHANNELS:
        return {"ok": False, "action": "reject", "error": f"未知渠道 {channel}"}
    if isinstance(fields, str):
        fields = {CHANNELS[ch]["fields"][0]: fields}
    fields = {k: str(v).strip() for k, v in (fields or {}).items() if str(v or "").strip()}
    if not fields:
        return {"ok": False, "action": "reject", "error": "空值"}

    d = load()
    items = d["items"]
    cur = items.get(ch)
    prev_fields = (cur or {}).get("fields") or {}
    prev_state = (cur or {}).get("state") or ""
    same = bool(cur) and all(prev_fields.get(k) == v for k, v in fields.items())

    if same and not count_use and (state is None or state == prev_state):
        return {"ok": True, "action": "unchanged", "channel": ch,
                "state": prev_state, "note": (cur or {}).get("note", "")}

    if validate and state is None:
        state, note, _info = ckcheck.check(ch, fields)
    state = state or "unverified"

    # 覆盖判断
    if cur and not force and not same:
        if prev_state == "valid" and state != "valid":
            # 新值没验证通过 —— 不动已有的有效 CK，只记事件
            store.log_event("ck", f"{CHANNELS[ch]['label']} 上报的 CK 未通过校验"
                                   f"（{state}: {note}），保留原有有效 CK")
            return {"ok": True, "action": "kept", "channel": ch,
                    "state": prev_state, "note": note}
        if prev_state == "unverified" and state == "invalid":
            return {"ok": True, "action": "kept", "channel": ch,
                    "state": prev_state, "note": note}

    now = _now()
    if cur and not same:
        hist = cur.setdefault("history", [])
        hist.insert(0, {"at": cur.get("updated") or cur.get("added") or now,
                        "state": prev_state,
                        "fields": {k: _mask(v) for k, v in prev_fields.items()},
                        "why": "superseded" if state == "valid" else "replaced"})
        del hist[_HIST_MAX:]

    item = cur or {"channel": ch, "added": now, "hits": 0, "last_used": 0}
    item["channel"] = ch
    item["fields"] = fields
    item["state"] = state
    item["note"] = note
    item["checked"] = now if validate or state != "unverified" else item.get("checked", 0)
    item["updated"] = now
    item["source"] = _norm_source(source) or item.get("source", "")
    if count_use:
        item["hits"] = int(item.get("hits") or 0) + 1
        item["last_used"] = now
        if state == "unverified":
            item["state"] = "valid"
            item["note"] = "播放成功"
    items[ch] = item
    bump(d)
    save(d)

    action = "created" if not cur else ("updated" if not same else "refreshed")
    store.log_event("ck", f"{CHANNELS[ch]['label']} CK {action}"
                           f"（{state}，来源 {item['source'] or '-'}）：{note}")
    return {"ok": True, "action": action, "channel": ch, "state": item["state"],
            "note": note, "version": d["version"]}


def report_fields(fields, source="", validate=False):
    """整份 tokenm.json 风格字段合并（只挑我们管的字段）"""
    out = []
    for f, v in (fields or {}).items():
        ch = _FIELD2CH.get(f)
        if ch and str(v or "").strip():
            out.append(report(ch, {f: v}, source=source, validate=validate))
    return out


# ------------------------------------------------------------------ 提取/嗅探


def channel_by_host(host):
    h = (host or "").lower().split(":")[0]
    if not h:
        return None
    for ch in ORDER:
        for cand in CHANNELS[ch]["hosts"]:
            if cand and (h == cand or h.endswith("." + cand) or cand in h):
                return ch
    return None


def channel_by_shape(value):
    v = str(value or "")
    if len(v) < 16:
        return None
    for ch in ORDER:
        for pat in CHANNELS[ch]["shapes"]:
            if re.search(pat, v):
                return ch
    return None


def channel_by_alias(text):
    t = str(text or "").lower()
    for ch in ORDER:
        for a in CHANNELS[ch]["aliases"]:
            if a.lower() in t:
                return ch
    return None


def extract(text, source="", hint=None):
    """从任意文本/URL/请求串里提取 CK，返回 [(channel, {field: value}, source)]

    支持四种形态：
      1. `quark_cookie=xxx`（显式字段名）
      2. `https://pan.quark.cn/...?...&cookie=xxx`（用主机名定位渠道）
      3. `Cookie: __pus=xxx`（用特征形态定位渠道）
      4. 一整份 tokenm.json
    """
    hits = []
    text = str(text or "").strip()
    if not text:
        return hits
    src = _norm_source(source)

    # 形态 4：整份 JSON
    if text.startswith("{") and text.endswith("}"):
        try:
            obj = json.loads(text)
            if isinstance(obj, dict):
                for f, v in obj.items():
                    ch = _FIELD2CH.get(f)
                    if ch and str(v or "").strip():
                        hits.append((ch, {f: str(v).strip()}, src + " /json"))
                if hits:
                    return hits
        except Exception:
            pass

    # 形态 1：显式字段名
    for m in _CK_FIELD_RE.finditer(text):
        ch = _FIELD2CH.get(m.group("k").lower())
        raw = m.group("q") or m.group("s") or m.group("v") or ""
        val = raw.strip().rstrip("&,;")
        if ch and len(val) >= 8:
            hits.append((ch, {m.group("k").lower(): val}, src + " /field"))
    if hits:
        return hits

    # 形态 2：URL
    for u in re.findall(r"https?://[^\s\"'<>]+", text):
        try:
            p = urllib.parse.urlparse(u)
        except Exception:
            continue
        q = urllib.parse.parse_qs(p.query)
        ch = channel_by_host(p.netloc) or channel_by_alias(p.netloc) or hint
        val = ""
        for k, vs in q.items():
            if k.lower() in _URL_PARAM_KEYS and vs and len(vs[0]) >= 16:
                val = vs[0]
                break
        if val and ch and ch in CHANNELS:
            hits.append((ch, {CHANNELS[ch]["fields"][0]: val}, src + " /url"))

    # 形态 3：裸 Cookie 串（必须特征命中，避免误伤站点会话 cookie）
    if not hits and re.search(r"[A-Za-z0-9_\-]{3,}\s*=", text):
        ch = hint or channel_by_shape(text)
        if ch and ch in CHANNELS:
            hits.append((ch, {CHANNELS[ch]["fields"][0]: text.strip()}, src + " /shape"))
    return hits


def sniff_request(path, query, headers, status, source="auto"):
    """请求级自动嗅探：只在「请求成功」时入库，并计一次播放使用。

    这是「播放成功即保存 CK」的自动路径 —— 客户端只要把带 CK 的请求发到中枢
    （网关 /gw/**、捕获台 /ck、或任何自定义上报），就会被识别。
    """
    if not C.CK_AUTOSNIFF:
        return []
    if status >= 400:
        return []
    src = f"{_norm_source(source)} {path[:60]}"
    chunks = []
    if query:
        chunks.append(query)
    for k in ("Cookie", "Authorization", "X-Ck", "X-Cookie", "X-Token"):
        v = headers.get(k) or headers.get(k.lower())
        if v:
            chunks.append(v)
    # 目标主机：路径里若夹了绝对 URL 就取它
    m = re.search(r"https?://[^\s&]+", path or "")
    parsed_host = None
    if m:
        chunks.append(m.group(0))
        try:
            parsed_host = urllib.parse.urlparse(m.group(0)).netloc
        except Exception:
            parsed_host = None

    hint = channel_by_host(parsed_host) if parsed_host else None
    out = []
    seen = set()
    for c in chunks:
        for ch, fields, s in extract(c, source=src, hint=hint):
            key = (ch, tuple(sorted(fields.items())))
            if key in seen:
                continue
            seen.add(key)
            r = report(ch, fields, source=s, count_use=True, validate=False)
            if r.get("ok") and r.get("action") != "unchanged":
                out.append(r)
    return out


# ------------------------------------------------------------------ 渲染

_BUILTIN_TPL = {"quark_cookie": "", "uc_cookie": "", "baidu_cookie": "",
                "pan115_cookie": "", "open_token": "", "token": "",
                "thunder_username": "", "thunder_password": "",
                "thunder_captchatoken": "", "yd_auth": "",
                "pikpak_username": "", "pikpak_password": "",
                "pan123_username": "", "pan123_password": "",
                "pan189_username": "", "pan189_password": "",
                "guangya_access_token": "", "guangya_refresh_token": "",
                "pan_order": "ali|quark|uc|123|189|115|yd|thunder|pikpak|baidu|guangya"}


def _template():
    """字段全集来自原版 tokentemplate.json（模板永不改动）"""
    for p in (os.path.join(C.PKG_DIR, "lib", "tokentemplate.json"),
              os.path.join(C.BASE_DIR, "baseline", "lib", "tokentemplate.json"),
              os.path.join(C.BASE_DIR, "assets", "lib", "tokentemplate.json")):
        if os.path.exists(p):
            try:
                with open(p, encoding="utf-8") as f:
                    obj = json.load(f)
                if isinstance(obj, dict) and obj:
                    return dict(obj)
            except Exception:
                continue
    return dict(_BUILTIN_TEMPL)

def effective(ch):
    """某渠道当前「应该下发」的字段。

    保险库里存的永远是「目前已知最好的那个值」——覆盖策略保证了
    invalid 的新值不会顶掉已有的 valid 值，所以这里直接下发即可
    （history 只存掩码，不用于回填）。
    """
    item = load()["items"].get(ch)
    if not item:
        return {}
    return {k: v for k, v in (item.get("fields") or {}).items() if v}


def merged():
    out = {}
    for ch in ORDER:
        out.update(effective(ch))
    return out


def render(lan=True):
    """模板 + 保险库覆盖。lan=False（公网来源）时不注入任何真实 CK。"""
    tpl = _template()
    if not lan:
        return tpl
    for f, v in merged().items():
        if f in tpl:
            tpl[f] = v
    return tpl


def render_text(lan=True):
    return json.dumps(render(lan=lan), ensure_ascii=False, indent=1)


def write_tokenm():
    """落盘一份，供文件路径访问与备份（动态路由始终以保险库为准）"""
    try:
        lib = os.path.join(C.PKG_DIR, "lib")
        if os.path.isdir(lib):
            p = os.path.join(lib, "tokenm.json")
            tmp = p + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(render_text(True))
            os.replace(tmp, p)
            return p
    except Exception as e:
        store.log_event("error", f"写入 tokenm.json 失败: {type(e).__name__}: {e}")
    return ""


# ------------------------------------------------------------------ 视图


def summary():
    d = load()
    items = d["items"]
    rows = []
    for ch in ORDER:
        it = items.get(ch)
        if not it:
            rows.append({"channel": ch, "label": CHANNELS[ch]["label"],
                         "state": "empty", "note": "未配置", "masked": "",
                         "updated": 0, "source": "", "hits": 0, "checkable": ckcheck.checkable(ch)})
            continue
        v = next(iter((it.get("fields") or {}).values()), "")
        rows.append({
            "channel": ch, "label": CHANNELS[ch]["label"],
            "state": it.get("state") or "unverified",
            "note": it.get("note") or "",
            "masked": _mask(v),
            "updated": it.get("updated") or 0,
            "added": it.get("added") or 0,
            "source": it.get("source") or "",
            "hits": int(it.get("hits") or 0),
            "last_used": it.get("last_used") or 0,
            "checkable": ckcheck.checkable(ch),
        })
    configured = [r for r in rows if r["state"] != "empty"]
    valid = [r for r in rows if r["state"] == "valid"]
    return {"version": d.get("version") or 0, "updated": d.get("updated") or 0,
            "rows": rows, "configured": len(configured), "valid": len(valid),
            "injected_fields": len(merged())}


def drop(channel):
    d = load()
    ch = (channel or "").lower()
    if ch in d["items"]:
        del d["items"][ch]
        bump(d)
        save(d)
        write_tokenm()
        store.log_event("ck", f"已删除 {CHANNELS.get(ch, {}).get('label', ch)} 的 CK")
        return True
    return False


def recheck(channel=None):
    """重新校验（默认全部）"""
    d = load()
    out = []
    targets = [channel] if channel else list(d["items"])
    for ch in targets:
        if ch not in CHANNELS or ch not in d["items"]:
            continue
        fields = d["items"][ch].get("fields") or {}
        state, note, _ = ckcheck.check(ch, fields)
        if state != "unverified":          # 判不了就不改状态（网络抖动保护）
            d["items"][ch]["state"] = state
            d["items"][ch]["note"] = note
            d["items"][ch]["checked"] = _now()
        out.append({"channel": ch, "state": state, "note": note})
    if out:
        bump(d)
        save(d)
        write_tokenm()
    return out
