# -*- coding: utf-8 -*-
"""WebDAV 端点：接管 OK影视「网盘及弹幕配置 → WEBDAV 缓存网盘信息」的备份/恢复通道。

背景
----
**这套同步是 App 主程序（`com.fongmi.android.tv`）内置的，不在 PG 包里**
（`pg.jar` 里扫不到任何 webdav 字符串）。地址存在 SharedPreferences 的
`global_panconfig_webdavconfig_address`，UI 上就是「网盘及弹幕配置」里的
「WEBDAV 缓存网盘信息」。

**实测（2026-10-16，真实客户端）**：端管理并会来回同步这些文件 ——

    quark.json        夸克网盘 CK          ← 用户最想要的
    baidu.json        百度网盘 CK          ← 同上
    uc.json           UC 网盘 CK
    aliyun.json       阿里云盘 token
    youtube.json      YouTube 凭据
    ysp_uid.json      （弹幕/直播侧 UID）
    fylive_ua.json    （直播侧 UA）
    playprogress.json 播放进度（自动，最大 350KB+）
    connection_test.json  填地址时自动写的连通性探针（内容 `{}`）

**关键行为**：只填地址 → 端只会**拉取（GET）** + 写个 `connection_test.json`；
必须**点一次「备份」**才会把上面这些凭据 **PUT** 上来。
PUT 上来即自动落盘 + 提取入库，其它设备 GET 就能同步。

**为什么这条通道比 tokenm.json 有价值**：它是端**主动发起**的外发请求，
不受「端只 GET 不回写 tokenm」的限制。

把地址指向中枢 `/gw/dav`，这条链路就变成「端 ⇄ 中枢」：

    PUT  端把凭据备份上来 → 中枢原样落盘 + 提取入库（采集方向）
    GET  端要恢复       → 中枢把手里最好的那份给它（下发方向）

存储
----
`data/dav/<相对路径>`，字节原样落盘，方便人工核对。
`logs/dav.jsonl` 记录每一次交互（方法 / 路径 / 大小 / 命中字段）。

安全
----
* **任意 Basic Auth 都接受** —— 端会带它自己配置的账号密码，中枢只需吞下来；
  真正的门禁是来源网段（默认只允许局域网写，见 `allow_from`）。
  注：端走 CF/gost 穿透回源时 `remote_addr` 是 `127.0.0.1`，故回环一并放行。
* 路径做穿越防护，任何逃出 data/dav 的相对路径一律 403。
"""
import base64
import json
import os
import re
import time

from . import config as C

_LOG_MAX_BYTES = 8 * 1024 * 1024
_DAV_METHODS = ("GET", "HEAD", "PUT", "DELETE", "OPTIONS", "PROPFIND", "MKCOL", "MOVE", "COPY", "LOCK", "UNLOCK")

# ---------------------------------------------------------------- 端上落盘位置
#
# 端上每个渠道的凭据都是一个**明文文件**，落在 App 自己的配置目录里：
#
#     /storage/emulated/0/TV/.<名字>_user.pg
#
# 例：夸克 = `/storage/emulated/0/TV/.quark_user.pg`
# （App 的「查看凭据」会直接把这个路径显示给用户）。
# 该目录属**外置存储、非私有目录** —— 电视上的文件管理器（打开「显示隐藏文件」）
# 就能看 / 拷 / 删 / 换。而 WebDAV 那条通道的文件名与它**去掉 `_user.pg`、换成
# `.json`** 一一对应，所以「同步 CK」本质就是「同步这些文件」。
#
# 端 PUT 上来的样本（实测 `cookie_guanying.json`）确定了 cookie 类的封装格式：
#
#     {"cookie":"<裸 cookie>","_last_modified":<epoch 秒>,"sitename":"<NAME>"}
#
# 下面这张表决定「哪些文件我们能替端合成」—— 只收录格式已实测确认的 cookie 类
# 渠道；账号/密码类（thunder / pikpak / pan123 / pan189）与 token 类
# （aliyun / yd / guangya）的确切字段名还没实测到，**宁可不合成也不猜**
# （猜错会把端上那份还能用的凭据直接顶掉）。
VAULT_FILES = {
    "quark.json": "quark",
    "uc.json":    "uc",
    "baidu.json": "baidu",
    "115.json":   "115",
}

DEVICE_DIR = "/storage/emulated/0/TV"


def device_path(name):
    """端上这个渠道文件的落盘位置（只用于展示，中枢本身碰不到设备）"""
    stem = name[:-5] if name.endswith(".json") else name
    return "%s/.%s_user.pg" % (DEVICE_DIR, stem)


def root():
    return os.path.join(C.DATA_DIR, "dav")


def enabled():
    """默认开启局域网；公网是否放行由 OKTV_DAV_PUBLIC 控制"""
    return bool(getattr(C, "DAV_ENABLED", True))


def allow_from(ip):
    if getattr(C, "DAV_PUBLIC", False):
        return True
    ip = ip or ""
    if ip.startswith("10.0.0.") or ip.startswith("192.168.") or ip.startswith("172."):
        return True
    return ip in ("127.0.0.1", "::1", "localhost")


# ------------------------------------------------------------------ 存储


def _safe(rel):
    """把 URL 相对路径映射到 data/dav 下的绝对路径（穿越防护）"""
    r = (rel or "").replace("\\", "/").lstrip("/")
    r = re.sub(r"/+", "/", r)
    base = os.path.abspath(root())
    full = os.path.abspath(os.path.join(base, r))
    if full != base and not full.startswith(base + os.sep):
        return None
    return full


def _rel(full):
    return os.path.relpath(full, os.path.abspath(root())).replace(os.sep, "/")


def list_files():
    out = []
    for dirpath, _dirs, files in os.walk(root()):
        for f in files:
            p = os.path.join(dirpath, f)
            try:
                out.append({"rel": _rel(p), "size": os.path.getsize(p),
                            "mtime": int(os.path.getmtime(p))})
            except OSError:
                continue
    return sorted(out, key=lambda x: x["rel"])


# ------------------------------------------------------------------ 记录


def _record(rec):
    try:
        os.makedirs(os.path.dirname(C.F_DAV_LOG), exist_ok=True)
        try:
            if os.path.getsize(C.F_DAV_LOG) > _LOG_MAX_BYTES:
                with open(C.F_DAV_LOG, "rb") as f:
                    f.seek(-_LOG_MAX_BYTES // 2, os.SEEK_END)
                    tail = f.read()
                with open(C.F_DAV_LOG, "wb") as f:
                    f.write(tail[tail.find(b"\n") + 1:])
        except OSError:
            pass
        with open(C.F_DAV_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass


def recent(n=100):
    out = []
    try:
        with open(C.F_DAV_LOG, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        out.append(json.loads(line))
                    except Exception:
                        pass
    except Exception:
        return []
    return out[-n:][::-1]


def _ask_index():
    """端请求过的文件名 -> 最近一次记录（只保留根目录下的 <name>.json）"""
    latest = {}
    for r in recent(1000):
        p = (r.get("p") or "").strip("/")
        if not p or "/" in p or not p.endswith(".json"):
            continue
        latest[p] = r                      # recent() 是倒序，最后覆盖到的是最新
    return latest


def mirror():
    """一张对照表：端上的落盘文件 / WebDAV 名 / 我们能不能替它合成。

    `device` 是最有价值的一列 —— CK 在电视上的**实际落盘位置**，
    即 App 的「查看凭据」里显示的那个路径。
    """
    from . import ckvault
    have = set(x["rel"].split("/")[-1] for x in list_files())
    out = []
    for name, r in sorted(_ask_index().items()):
        ch, main = refill_channel(name)
        val = (ckvault.effective(ch) or {}).get(main) if ch else ""
        out.append({
            "name": name,
            "device": device_path(name),
            "have": name in have,
            "refill": ("%s.%s" % (ch, main)) if val else "",
            "m": r.get("m"),
            "status": r.get("status"),
            "t": r.get("t"),
        })
    return out


def wanted():
    """端主动来要、但我们手上没有的文件（GET 404）。

    这是**最有行动价值的一列**：它等于「端希望同步哪些渠道」。每出现一个新名字，
    就说明 App 那边多了一个可同步项 —— 只要端 PUT 上来（或我们按同名放一份），
    该渠道就自动接上了。
    """
    from . import ckvault
    have = set(x["rel"].split("/")[-1] for x in list_files())
    out = []
    for name, r in sorted(_ask_index().items()):
        if name in have:
            continue
        ch, main = refill_channel(name)
        val = (ckvault.effective(ch) or {}).get(main) if ch else ""
        out.append({"name": name, "m": r.get("m"), "status": r.get("status"),
                    "t": r.get("t"), "note": r.get("note") or "",
                    "device": device_path(name),
                    "refill": ("%s.%s" % (ch, main)) if val else ""})
    return out


def stats():
    rows = recent(500)
    puts = [r for r in rows if r.get("m") == "PUT"]
    gets = [r for r in rows if r.get("m") == "GET" and r.get("status") == 200]
    hit = [r for r in rows if r.get("hit")]
    want = wanted()
    mir = mirror()
    if not getattr(C, "DAV_REFILL", True):
        for x in want + mir:
            x["refill"] = ""
    return {
        "enabled": enabled(),
        "public": bool(getattr(C, "DAV_PUBLIC", False)),
        "files": len(list_files()),
        "total": len(rows),
        "puts": len(puts),
        "gets": len(gets),
        "with_cred": len(hit),
        "last": rows[0].get("t", "") if rows else "",
        "last_put": puts[0]["t"] if puts else "",
        "last_hit": hit[0]["hit"] if hit else [],
        "wanted": want,                    # 端想要、我们还没有的渠道
        "wanted_n": len(want),
        "mirror": mir,                     # 全量对照：端上文件 / WebDAV 名 / 能否回填
        "refill_n": sum(1 for x in want if x.get("refill")),
        "refill_on": bool(getattr(C, "DAV_REFILL", True)),
        "device_dir": DEVICE_DIR,          # CK 在电视上的落盘目录
        "url": "/gw/dav",
    }


# ------------------------------------------------------------------ 采集


def _harvest(rel, body, ip):
    # 【2026-10-18 废弃】CK 多端同步停用：端上来的内容照样落盘留档（便于观察），
    # 但不再解析入库 —— 保险库不再参与下发，入库只会造成"以为在同步"的错觉。
    if not getattr(C, "CK_SYNC_ENABLED", False):
        return []
    from . import ckvault
    text = ""
    try:
        text = body.decode("utf-8", "replace")
    except Exception:
        return []
    # 一次备份里同一渠道可能有多个字段（access_token + refresh_token），
    # 必须**合并成一次 report** —— 分次写会互相覆盖（后一次把前一次的字段顶掉）。
    merged = {}
    for ch, fields, _src in ckvault.extract(text, source="dav %s" % rel, hint=None):
        merged.setdefault(ch, {}).update(fields)
    hits = []
    for ch, fields in merged.items():
        ckvault.report(ch, fields, source="dav %s" % rel, validate=False)
        hits.extend(fields.keys())
    return sorted(set(hits))


# ------------------------------------------------------------------ 回填


def refill_channel(name):
    """这个文件能不能用保险库里的 CK 合成。返回 (渠道, 主字段) 或 (\"\", \"\")"""
    if "/" in str(name or "").strip("/"):
        return "", ""
    ch = VAULT_FILES.get(str(name or "").strip("/"))
    if not ch:
        return "", ""
    from . import ckvault
    main = ((ckvault.CHANNELS.get(ch) or {}).get("fields") or [""])[0]
    return ch, main


def from_vault(rel):
    """端来要 `<渠道>.json`、我们手上没有落盘文件 → 用保险库当前最优 CK 合成一份。

    这是把云端 CK 送进电视的**唯一自动通道**：端每约 30 分钟（以及打开对应网盘
    设置时）会 GET 这些文件，取到就写回它自己的
    `/storage/emulated/0/TV/.<渠道>_user.pg`。所以只要往保险库写一次，端上那个
    文件就会自动变成我们给的值 —— 不用扫码、不用在电视上点任何东西。

    保险库为空 / 该渠道没值 → 返回 (None, "")，行为与以前完全一致（404）。
    """
    # 【2026-10-18 废弃】CK 多端同步停用后不再回填 —— 避免中枢的值盖掉端上
    # 用户自己扫码登录的那份。端 GET 一律 404，它就继续用本地 .pg 文件。
    if not getattr(C, "CK_SYNC_ENABLED", False):
        return None, ""
    if not getattr(C, "DAV_REFILL", True):
        return None, ""
    ch, main = refill_channel(rel)
    if not ch:
        return None, ""
    from . import ckvault
    val = (ckvault.effective(ch) or {}).get(main) or ""
    if not val:
        return None, ""
    obj = {"cookie": val,
           "_last_modified": int(time.time()),
           "sitename": str(rel).strip("/")[:-5].upper()}
    return json.dumps(obj, ensure_ascii=False).encode("utf-8"), "%s.%s" % (ch, main)


# ------------------------------------------------------------------ 响应


def _xml_multistatus(entries):
    parts = ['<?xml version="1.0" encoding="utf-8"?>',
             '<D:multistatus xmlns:D="DAV:">']
    for href, is_dir, size, mtime in entries:
        parts.append("<D:response><D:href>%s</D:href><D:propstat><D:prop>" % href)
        if is_dir:
            parts.append("<D:resourcetype><D:collection/></D:resourcetype>")
        else:
            parts.append("<D:resourcetype/>")
            parts.append("<D:getcontentlength>%d</D:getcontentlength>" % size)
            parts.append("<D:getcontenttype>application/octet-stream</D:getcontenttype>")
        parts.append("<D:getlastmodified>%s</D:getlastmodified>" %
                     time.strftime("%a, %d %b %Y %H:%M:%S GMT", time.gmtime(mtime or time.time())))
        parts.append("</D:prop><D:status>HTTP/1.1 200 OK</D:status></D:propstat></D:response>")
    parts.append("</D:multistatus>")
    return "".join(parts).encode("utf-8")


def _basic_user(headers):
    """吃掉端的 Basic Auth（不校验，只记录用）"""
    v = (headers or {}).get("Authorization") or (headers or {}).get("authorization") or ""
    if not v.lower().startswith("basic "):
        return ""
    try:
        raw = base64.b64decode(v.split(None, 1)[1] + "==").decode("utf-8", "replace")
        return raw.split(":", 1)[0]
    except Exception:
        return ""


def handle(rel, method, headers, body, ip, base_url):
    """处理一次 WebDAV 请求。返回 (body_bytes, status, headers)"""
    hdrs = {"DAV": "1,2", "MS-Author-Via": "DAV",
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": ", ".join(_DAV_METHODS),
            "Access-Control-Allow-Headers": "*"}

    if not enabled():
        return b"", 503, hdrs
    if method == "OPTIONS":
        return b"", 200, hdrs
    if not allow_from(ip):
        _record({"t": time.strftime("%Y-%m-%d %H:%M:%S"), "ip": ip, "m": method,
                 "p": rel, "status": 403, "hit": [], "note": "来源不允许"})
        return b"forbidden", 403, hdrs

    # 根目录必须存在：端第一次上来通常先 PROPFIND 根，拿到 404 可能直接放弃。
    try:
        os.makedirs(root(), exist_ok=True)
    except OSError:
        pass

    path = _safe(rel)
    if path is None:
        return b"forbidden", 403, hdrs

    user = _basic_user(headers)
    hit = []
    status = 200
    note = ""
    size = 0
    out = b""

    try:
        if method == "MKCOL":
            os.makedirs(path, exist_ok=True)
            status = 201

        elif method in ("PUT",):
            os.makedirs(os.path.dirname(path) or root(), exist_ok=True)
            size = len(body or b"")
            tmp = path + ".part"
            with open(tmp, "wb") as f:
                f.write(body or b"")
            os.replace(tmp, path)
            status = 201
            hit = _harvest(rel, body or b"", ip)

        elif method in ("GET", "HEAD"):
            if os.path.isfile(path):
                with open(path, "rb") as f:
                    out = f.read()
                hdrs["Content-Type"] = "application/json"
                size = len(out)
            else:
                # 落盘没有，但保险库里有这个渠道的 CK → 合成一份给端（回填）。
                # 端会把它写进 /storage/emulated/0/TV/.<渠道>_user.pg。
                blob, src = from_vault(rel)
                if blob:
                    out = blob
                    hdrs["Content-Type"] = "application/json"
                    size = len(out)
                    note = "回填自保险库 %s" % src
                else:
                    status, note = 404, "remote missing"
                    out = b""

        elif method == "DELETE":
            if os.path.isfile(path):
                os.remove(path)
                status = 204
            elif os.path.isdir(path):
                status = 204          # 不递归删除，留个记录
                note = "目录删除被忽略"
            else:
                status = 404

        elif method == "PROPFIND":
            depth = str((headers or {}).get("Depth") or (headers or {}).get("depth") or "1")
            entries = []
            href = "%s/%s" % (base_url.rstrip("/"), rel.strip("/")) if rel.strip("/") else base_url.rstrip("/") + "/"
            if os.path.isdir(path):
                entries.append((href if href.endswith("/") else href + "/", True, 0,
                                os.path.getmtime(path)))
                if depth != "0":
                    for name in sorted(os.listdir(path)):
                        c = os.path.join(path, name)
                        entries.append((href.rstrip("/") + "/" + name,
                                        os.path.isdir(c),
                                        os.path.getsize(c) if os.path.isfile(c) else 0,
                                        os.path.getmtime(c)))
                out = _xml_multistatus(entries)
                status = 207
            elif os.path.isfile(path):
                entries.append((href, False, os.path.getsize(path), os.path.getmtime(path)))
                out = _xml_multistatus(entries)
                status = 207
            else:
                status = 404
                note = "remote missing"

        else:
            status, note = 501, "未实现的方法 %s" % method

    except Exception as e:
        status, note = 500, "%s: %s" % (type(e).__name__, e)

    if method == "PROPFIND":
        hdrs["Content-Type"] = "application/xml; charset=utf-8"

    _record({"t": time.strftime("%Y-%m-%d %H:%M:%S"), "ip": ip, "user": user,
             "m": method, "p": rel, "status": status, "size": size,
             "hit": hit, "note": note})
    if hit:
        from . import store
        store.log_event("ck", "WebDAV 备份捕获凭据 %s（来源 %s）" % (",".join(hit), ip or "-"))
    return out, status, hdrs
