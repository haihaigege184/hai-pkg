# -*- coding: utf-8 -*-
"""网盘 CK 与直链解析（2026-11-01 新建）

为什么要自己维护 CK
-------------------
真机反查结论（电视 adb）：

* `com.hisen.android.tv`（改造版）的私有目录里只有 `app_webview/Cookies` 和
  `shared_prefs/WebViewChromiumPrefs.xml`，**没有任何网盘配置**；
* `com.fongmi.android.tv`（原版）同样只有 WebView 那一个文件；
* `/sdcard/TV/` 只有缓存、`jar_abr_*.bin`、`tv-*.bk.gz` 备份。

PG 把 CK 放在 native 层加密存储（作者明确防泄露），抓不到也不该抓。所以
「点完就播」要做到零 PG 界面，只有一条路：CK 存我们自己的服务器，直链
自己算。

能匿名做到什么程度（服务器实测，夸克）
------------------------------------
    share/sharepage/token   → 200，拿到 stoken        ✅ 匿名
    share/sharepage/detail  → 拿到文件名/fid/fid_token ✅ 匿名
    file/download           → 401                    ❌ 差 CK
    sharepage/download      → 404                    ❌ 不存在

也就是「列目录能，取直链不能」。补上 CK 之后这条链就闭环。

存储
----
`data/panck.json`：`{"quark": "<cookie>", "uc": "...", "baidu": "..."}`
只落在用户自己的机器上，任何接口都不回显完整 CK（只回长度和尾 4 位）。
"""
from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from . import config as C

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36")

# 夸克 PC 客户端 UA —— **这是 23018 的唯一解**（2026-11-01 实证）
#
# 用普通浏览器 UA 调 `file/download`，不管文件多大一律回
#   400 {"code":23018,"message":"download file size limit[<fid>]"}
# 连 2GB 的 mp4 都报「size limit」，看着像会员限制，其实是**客户端指纹校验**：
# 夸克只给「自家客户端」签发大文件直链。把 UA 换成客户端串立刻 200 出链。
QK_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
         "(KHTML, like Gecko) quark-cloud-drive/2.5.56 Chrome/100.0.4896.160 "
         "Electron/18.3.5.12 Safari/537.36 Channel/pckk_other_ch")

# 直链 CDN 有防盗链：只带 UA 会 412 Precondition Failed，
# 必须 UA + Referer + **Cookie** 三件套同时在场（实测）。
QK_DL_HEADERS = {"User-Agent": QK_UA, "Referer": "https://pan.quark.cn/"}

# UC 网盘 —— 和夸克是同一套 API（同一家），只有 base / pr / UA / Referer 不同。
# 2026-11-01 全链路实测：token → detail → save → task → download 全通，
# 裸 UA 取 CDN 直链会 403，UA+Referer+Cookie 才 200。
UC_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
         "(KHTML, like Gecko) uc-cloud-drive/2.5.56 Chrome/100.0.4896.160 "
         "Electron/18.3.5.12 Safari/537.36 Channel/pckk_other_ch")
UC_DL_HEADERS = {"User-Agent": UC_UA, "Referer": "https://drive.uc.cn/"}

# 每家网盘的接口常量。加新网盘只在这一张表里加一行。
_PAN = {
    "quark": {"base": "https://drive-pc.quark.cn/1/clouddrive/",
              "q": "?pr=ucpro&fr=pc",
              "ua": QK_UA, "ref": "https://pan.quark.cn/",
              "root": "quark"},
    "uc": {"base": "https://pc-api.uc.cn/1/clouddrive/",
           "q": "?pr=UCBrowser&fr=pc",
           "ua": UC_UA, "ref": "https://drive.uc.cn/",
           "root": "uc"},
    # 百度走的是完全另一套接口（share/list + tplconfig sign + filemetas），
    # 真正实现在 bdpan.py / bd_ep()；这里登记一条主要是让 `_cfg(pan)["root"]`
    # 能取到正确凭据 —— 少了它 app.py 会回退到夸克配置，拿夸克 CK 去请求百度。
    "baidu": {"base": "https://pan.baidu.com/",
              "q": "?channel=chunlei&web=1&app_id=250528",
              "ua": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                     "(KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36"),
              "ref": "https://pan.baidu.com/",
              "root": "baidu"},
}


def pan_of(url: str) -> str:
    """认分享链接属于哪家网盘。不认识返回空串。"""
    u = str(url or "").lower()
    if "pan.quark.cn" in u or "quark.cn" in u:
        return "quark"
    if "drive.uc.cn" in u or "uc.cn" in u:
        return "uc"
    if "pan.baidu.com" in u or "eyun.baidu.com" in u:
        return "baidu"
    return ""


BD_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
         "(KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36")

# 百度 CDN（d.pcs.baidu.com）的防盗链和夸克一个套路：**必须客户端 UA**。
# 2026-11-03 实测：Chrome UA → 403 `error_code:31326 user is not authorized,
# hitcode:119`；换成网盘客户端串 → 206 直接出数据。
#
# **UA 必须是完整长串（2026-11-03 晚踩第二次）**：dlink 尾巴挂上 `origin=dlna`
# 之后，UA 只写 `netdisk;P2SP;3.0.0.8` 会重新吃 403，必须带全
# `;netdisk;11.12.3;android-android;10.0` 这一段（3.3 MB/s vs 403）。
# 电视上 PG 转发用的也是这条长串。
BD_DL_UA = "netdisk;P2SP;3.0.0.8;netdisk;11.12.3;android-android;10.0"
BD_DL_HEADERS = {"User-Agent": BD_DL_UA, "Referer": "https://pan.baidu.com/"}


def dl_headers(pan: str) -> dict:
    if pan == "uc":
        return dict(UC_DL_HEADERS)
    if pan == "baidu":
        return dict(BD_DL_HEADERS)
    return dict(QK_DL_HEADERS)


def _kbps(url: str, hdr: dict, nbytes: int = 131072, timeout: float = 4.0) -> int:
    """量一条直链的实际速度（KB/s），0 = 探测失败。

    **样本必须够大（2026-11-03 晚改）**：老版本只量 96 KB / 3 秒，首包
    RTT 一抖就算出 30~90 KB/s，于是把好好的原盘直链误判成"百度限速"
    直接退 HLS —— 限速其实是 `origin=dlna` 缺失造成的（见 bdpan.dlink）。
    现在直链自带 `origin=dlna`，正常是 7~8 MB/s，量 4 MB 足够区分。
    """
    h = dict(hdr or {})
    h["Range"] = "bytes=0-%d" % max(0, int(nbytes) - 1)
    t0 = time.time()
    try:
        with urllib.request.urlopen(
                urllib.request.Request(url, headers=h), timeout=timeout) as f:
            b = f.read(int(nbytes))
        dt = max(0.05, time.time() - t0)
        return int(len(b) / 1024.0 / dt)
    except Exception:
        return 0


def _http_probe(url: str, hdr: dict, n: int = 64, timeout: float = 12.0) -> str:
    """取响应体前 n 字节当文本（判断"回的是不是真媒体"用）。

    2026-11-03 加：百度试看口出问题后会 HTTP 200 回一段 JSON（{"errno":71}），
    状态码看不出问题，只有看内容才知道是假的。
    """
    h = dict(hdr or {})
    h.setdefault("Range", "bytes=0-%d" % max(0, n - 1))
    try:
        with urllib.request.urlopen(
                urllib.request.Request(url, headers=h), timeout=timeout) as f:
            return (f.read(n) or b"").decode("utf-8", "ignore")
    except Exception:
        return ""


# 百度 HLS 档位记忆：{path: (选中时间, typ)}，30 分钟有效
_HLS_TYP: dict = {}


def probe_size(url: str, hdr: dict, fallback: int = 0) -> int:
    """CDN 报的真实文件大小。

    **详情接口给的 size 不可信**：UC 的 `sharepage/detail` 对一个 14.6MB 的
    视频只报 152480 字节（多半是预览/缩略），夸克也见过报 0 的。
    这个值会一路传到 APK 的多线程数据源当「文件总长」用 —— 只拉 148KB
    就当作读完了，播放器表现为「解出 1 秒就停」或直接卡住。
    真值从 Range 探测的 `Content-Range: bytes 0-0/<total>` 里拿。
    """
    h = dict(hdr or {})
    h["Range"] = "bytes=0-0"
    try:
        with urllib.request.urlopen(
                urllib.request.Request(url, headers=h), timeout=12) as f:
            cr = f.headers.get("Content-Range") or ""
            if "/" in cr:
                n = int(str(cr).rsplit("/", 1)[-1])
                if n > 0:
                    return n
            cl = f.headers.get("Content-Length")
            if cl and int(cl) > 0:
                return int(cl) + 1          # 0-0 只回 1 字节
    except Exception:
        pass
    return int(fallback or 0)


_LOCK = threading.Lock()
_CACHE: dict = {"t": 0.0, "v": {}}
_TTL = 30.0                      # CK 文件变化不频繁，30 秒读一次盘足够

PAN_KINDS = ("quark", "uc", "baidu")


def _path() -> str:
    return os.path.join(C.DATA_DIR, "panck.json")


def load(force: bool = False) -> dict:
    now = time.time()
    with _LOCK:
        if not force and (now - _CACHE["t"]) < _TTL:
            return dict(_CACHE["v"])
    out: dict = {}
    try:
        with open(_path(), "r", encoding="utf-8") as f:
            d = json.load(f)
        for k in PAN_KINDS:
            v = str((d or {}).get(k) or "").strip()
            if v:
                out[k] = v
    except Exception:
        out = {}
    with _LOCK:
        _CACHE["t"] = now
        _CACHE["v"] = dict(out)
    return out


def save(data: dict) -> dict:
    """只写这三家；空串 = 删除该项。返回保存后的摘要（不回显完整 CK）。"""
    cur = load(force=True)
    for k in PAN_KINDS:
        if k in data:
            v = str(data.get(k) or "").strip()
            if v:
                cur[k] = v
            else:
                cur.pop(k, None)
    try:
        os.makedirs(os.path.dirname(_path()), exist_ok=True)
        tmp = _path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cur, f, ensure_ascii=False, indent=1)
        os.replace(tmp, _path())
    except Exception:
        pass
    with _LOCK:
        _CACHE["t"] = 0.0
    return summary()


def summary() -> dict:
    """给维护页看的摘要：只回长度 + 尾 4 位，绝不回显整条 CK。"""
    cur = load(force=True)
    out = {}
    for k in PAN_KINDS:
        v = cur.get(k) or ""
        out[k] = ({"set": True, "len": len(v), "tail": v[-4:] if len(v) > 4 else ""}
                  if v else {"set": False, "len": 0, "tail": ""})
    return out


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

def _req(url: str, body=None, ck: str = "", ref: str = "https://pan.quark.cn/",
         timeout: float = 12.0, ua: str = ""):
    data = None
    headers = {"User-Agent": ua or UA, "Referer": ref, "Accept": "application/json"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if ck:
        headers["Cookie"] = ck
    r = urllib.request.Request(url, data=data, headers=headers)
    try:
        with urllib.request.urlopen(r, timeout=timeout) as f:
            return json.loads(f.read().decode("utf-8", "ignore"))
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read().decode("utf-8", "ignore"))
        except Exception:
            return {"status": e.code, "code": e.code, "message": str(e)}
    except Exception as e:
        return {"status": -1, "message": "%s: %s" % (type(e).__name__, e)}


_QK_TOKEN = ("https://drive-pc.quark.cn/1/clouddrive/share/sharepage/token"
             "?pr=ucpro&fr=pc")
_QK_DETAIL = ("https://drive-pc.quark.cn/1/clouddrive/share/sharepage/detail"
              "?pr=ucpro&fr=pc")
_QK_SAVE = ("https://drive-pc.quark.cn/1/clouddrive/share/sharepage/save"
            "?pr=ucpro&fr=pc")
_QK_TASK = "https://drive-pc.quark.cn/1/clouddrive/task?pr=ucpro&fr=pc&task_id="
_QK_DL = "https://drive-pc.quark.cn/1/clouddrive/file/download?pr=ucpro&fr=pc"


def share_sid(url: str) -> str:
    """从分享链接里抠出 pwd_id（夸克/UC 都是 /s/<id>）。"""
    p = urllib.parse.urlparse(str(url or ""))
    seg = [x for x in (p.path or "").split("/") if x]
    return seg[-1] if seg else ""


def share_pwd(url: str) -> str:
    q = urllib.parse.parse_qs(urllib.parse.urlparse(str(url or "")).query)
    return str((q.get("pwd") or q.get("passcode") or [""])[0])


def _cfg(pan: str) -> dict:
    return _PAN.get(pan or "") or _PAN["quark"]


def share_token(url: str, ck: str = "", pan: str = "") -> str:
    """取 stoken —— 这一步夸克/UC 都**匿名可过**。"""
    pan = pan or pan_of(url) or "quark"
    cfg = _cfg(pan)
    sid, pwd = share_sid(url), share_pwd(url)
    if not sid:
        return ""
    d = _req(cfg["base"] + "share/sharepage/token" + cfg["q"],
             {"pwd_id": sid, "passcode": pwd}, ck=ck, ref=cfg["ref"])
    return str(((d.get("data") or {}).get("stoken")) or "")


def share_files(url: str, stoken: str, ck: str = "", pdir: str = "0",
                pan: str = "") -> list:
    pan = pan or pan_of(url) or "quark"
    cfg = _cfg(pan)
    q = ("&pwd_id=" + share_sid(url) + "&stoken=" + urllib.parse.quote(stoken)
         + "&pdir_fid=" + str(pdir) + "&force=0&_page=1&_size=200"
         + "&_fetch_banner=0&_fetch_share=0&_fetch_total=1"
         + "&_sort=file_type:asc,file_name:asc")
    d = _req(cfg["base"] + "share/sharepage/detail" + cfg["q"] + q, None,
             ck=ck, ref=cfg["ref"])
    return ((d.get("data") or {}).get("list")) or []


# ---------------------------------------------------------------------------
# 转存目标根目录
# ---------------------------------------------------------------------------
_ROOT_PATH = os.path.join(C.DATA_DIR, "panroot.json")
_R_ROOT = {"t": 0.0, "v": {}}


def root(pan: str = "quark") -> str:
    """用户网盘根目录 fid。夸克 save 的 `to_pdir_fid` 用它，不能用 "0"。"""
    now = time.time()
    if (now - _R_ROOT["t"]) < 60 and _R_ROOT["v"]:
        return _R_ROOT["v"].get(pan) or ""
    try:
        with open(_ROOT_PATH, "r", encoding="utf-8") as f:
            d = json.load(f) or {}
    except Exception:
        d = {}
    _R_ROOT["t"], _R_ROOT["v"] = now, dict(d)
    return d.get(pan) or ""


def set_root(pan: str, fid: str) -> None:
    fid = str(fid or "").strip()
    if not fid:
        return
    try:
        with open(_ROOT_PATH, "r", encoding="utf-8") as f:
            d = json.load(f) or {}
    except Exception:
        d = {}
    d[pan] = fid
    try:
        os.makedirs(os.path.dirname(_ROOT_PATH), exist_ok=True)
        with open(_ROOT_PATH, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=1)
    except Exception:
        pass
    _R_ROOT["t"] = 0.0


# ---------------------------------------------------------------------------
# 分享目录递归
# ---------------------------------------------------------------------------

_VIDEO_EXT = (".mp4", ".mkv", ".avi", ".ts", ".rmvb", ".wmv", ".flv",
              ".mov", ".webm", ".m2ts", ".mpg", ".mpeg", ".iso")


def _is_video(name: str) -> bool:
    return str(name or "").lower().endswith(_VIDEO_EXT)


def share_tree(url: str, stoken: str, ck: str = "", budget: float = 9.0,
               max_files: int = 800, max_depth: int = 6) -> list:
    """递归扫分享，把所有视频文件摊平。

    夸克分享常常是「剧名（年份）4K 高码率…」→ `66G` 版本目录 → `S01E01.mkv`
    三层套娃，只列第一层永远看不到文件（实测）。
    """
    out: list = []
    t0 = time.time()
    queue = [("0", 0)]
    while queue and (time.time() - t0) < budget and len(out) < max_files:
        pdir, dep = queue.pop(0)
        try:
            fs = share_files(url, stoken, ck, pdir)
        except Exception:
            continue
        for f in fs:
            name = f.get("file_name") or ""
            if f.get("file_type") == 0:
                if dep < max_depth:
                    queue.append((f["fid"], dep + 1))
            elif _is_video(name):
                f["_pdir"] = pdir
                out.append(f)
    return out


def pick_ep(files: list, ep: int = 0, title: str = ""):
    """在摊平的文件里挑出第 `ep` 集。ep=0 表示「第一集/正片」。"""
    from . import pan_ep

    def _to_rows(src):
        out = []
        for f in src:
            name = f.get("file_name") or ""
            if pan_ep._is_junk(name):
                continue
            try:
                sn, e = pan_ep.parse_name(name)
            except Exception:
                sn, e = 1, 0
            out.append({"sn": sn, "ep": e, "f": f, "name": name})
        return out

    rows = _to_rows(files)
    # 剧场版 / 花絮 / 预告 会抢到 ep=1（2026-11-02 实测：仙逆的分享里
    # 171 个文件，第 1 集被选成「剧场版 神临之战」）。候选足够多时才排除，
    # 电影单片分享（1~3 个文件）不受影响。
    if len(rows) > 12:
        JUNK = ("\u5267\u573a\u7248", "\u5267\u573a", "Movie", "MOVIE",
                "\u9884\u544a", "\u82b1\u7d6e", "\u5f69\u86cb",
                "\u5e55\u540e", "\u7279\u5178", "OVA", "SP")
        clean = [r for r in rows if not any(j in r["name"] for j in JUNK)]
        if len(clean) >= 8:
            rows = clean
    if not rows:
        return None
    # ---- 单文件分享：别纠结集号 ----------------------------------------
    # 用户手动分享的视频（`7.mp4`、`定制虚拟偶像..mp4`）文件名里常带数字，
    # `parse_name` 会把 `7.mp4` 当成「第 7 集」，于是请求 ep=1 时匹配不到、
    # 又因为「分享里有明确集号」不走兜底 → 直接报「找不到第 1 集」。
    # 整个分享只有一个视频文件时，它必然是用户要的那一个。
    if len(rows) == 1:
        # 例外（2026-11-03）：剧场版/花絮的单文件分享，名字里的数字会被
        # `parse_name` 当集号（"Renegade Immortal Movie 2.4K.mp4" → 第 2 集）。
        # 要的是第 2 集及以后时，宁可判「这个分享没有这一集」，让上层换下一个
        # 分享，也别把剧场版当正片丢给播放器。
        _nm = str(rows[0].get("name") or "")
        if int(ep or 0) > 1 and any(
                k in _nm for k in ("剧场版", "劇場版", "Movie", "MOVIE",
                                   "预告", "預告", "花絮", "特典")):
            return None
        return rows[0]["f"]
    if ep:
        hit = [r for r in rows if int(r["ep"] or 0) == int(ep)]
        if hit:
            hit.sort(key=lambda r: r["sn"])
            # 剧场版 / Movie 的名字会被 parse_name 误当成集号
            # （"Renegade Immortal Movie 2.4K.mp4" → ep=2），于是「第 2 集」
            # 被挑成剧场版。2026-11-03 仙逆实测：剧集分享和剧场版分享同时
            # 排在候选里时就会挂错片。这里优先挑正常命名的；只剩这类就被
            # 判成「这个分享没有这一集」，让上层换下一个分享。
            _JUNK2 = ("剧场版", "劇場版", "Movie", "MOVIE", "预告", "預告",
                      "花絮", "特典", "彩蛋", "幕后", "幕後")
            _good = [r for r in hit if not any(k in r["name"] for k in _JUNK2)]
            if _good:
                return _good[0]["f"]
            if int(ep) > 1:
                return None
            return hit[0]["f"]
        # ---- 文件名里一个集号都解析不出来时，退一步自己挑 ----
        # 常见于：电影（单文件）、网盘里被改过名的剧集（`4K.120帧.mkv`
        # `泰坦尼克号.mkv`）。原来这里直接 `return None`，上层就报
        # 「找不到第 N 集」，表现就是用户点播放「无数据」。
        # 只在**整个分享没有任何明确集号**时才启用，避免多季剧集挂错片。
        # 请求的集号超出分享的最大集号（比如只有 ep=7 却要第 1 集），
        # 说明这个「集号」不是真集号，退回第一个文件，别让用户点了没反应。
        _mx = max([int(r["ep"] or 0) for r in rows] or [0])
        if _mx and int(ep) <= _mx and len(rows) <= 3:
            return sorted(rows, key=lambda r: r["sn"])[0]["f"]
        if not any(int(r["ep"] or 0) > 0 for r in rows):
            t = (title or "").strip()
            cand = rows
            if t:
                same = [r for r in rows if t in r["name"] or r["name"] in t]
                if same:
                    cand = same
            if len(cand) >= int(ep):
                cand = sorted(cand, key=lambda r: (-int(r["f"].get("size") or 0),
                                                   r["sn"]))
                cand.sort(key=lambda r: r["sn"])
                return cand[int(ep) - 1]["f"]
            big = sorted(rows, key=lambda r: -int(r["f"].get("size") or 0))
            return big[0]["f"]
        return None
    rows.sort(key=lambda r: (r["sn"], r["ep"] or 1))
    return rows[0]["f"]


# ---------------------------------------------------------------------------
# 转存台账（去重 + 后续清理依据）
# ---------------------------------------------------------------------------

_XFER_PATH = os.path.join(C.DATA_DIR, "panxfer.json")
_L_XFER = {"t": 0.0, "v": {}}


def _xfer_load() -> dict:
    now = time.time()
    if (now - _L_XFER["t"]) < 20 and _L_XFER["v"]:
        return dict(_L_XFER["v"])
    try:
        with open(_XFER_PATH, "r", encoding="utf-8") as f:
            d = json.load(f) or {}
    except Exception:
        d = {}
    _L_XFER["t"], _L_XFER["v"] = now, dict(d)
    return d


def _xfer_save(d: dict) -> None:
    if len(d) > 3000:                        # 只留最近的，文件本身另有清理
        for k in sorted(d, key=lambda x: (d[x] or {}).get("t") or 0)[:1000]:
            d.pop(k, None)
    try:
        os.makedirs(os.path.dirname(_XFER_PATH), exist_ok=True)
        with open(_XFER_PATH, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False)
    except Exception:
        pass
    _L_XFER["t"] = 0.0


def _xfer_key(url: str, fid: str) -> str:
    import hashlib
    return "qk:" + hashlib.md5((share_sid(url) + str(fid)).encode()).hexdigest()[:16]


# 台账保留条数。转存是**写到用户自己网盘根目录**的，一集 2–10GB，
# 不清理两次就把盘塞满了（实测试播 6 集就吃掉 29.8GB）。只留最近几条，
# 超出的连同网盘文件一起删 —— 只删台账里我们自己转存过的 fid，不碰其他文件。
_XFER_KEEP = 6


def _pan_delete(fids: list, ck: str, pan: str = "quark") -> bool:
    fids = [str(x) for x in (fids or []) if x]
    if not fids or not ck:
        return False
    cfg = _cfg(pan)
    r = _req(cfg["base"] + "file/delete" + cfg["q"],
             {"action_type": 2, "filelist": fids, "exclude_fids": []},
             ck=ck, ua=cfg["ua"], ref=cfg["ref"])
    return int(r.get("code") or -1) == 0


def _xfer_prune(tab: dict, ck: str, pan: str = "quark") -> None:
    if len(tab) <= _XFER_KEEP:
        return
    order = sorted(tab, key=lambda k: (tab[k] or {}).get("t") or 0)
    old = order[:len(order) - _XFER_KEEP]
    fids = [(tab[k] or {}).get("fid") for k in old]
    if _pan_delete(fids, ck, pan):
        for k in old:
            tab.pop(k, None)
        _xfer_save(tab)


def _save_as(fid: str, tok: str, pdir: str, sid: str, stoken: str,
             ck: str, pan: str = "quark") -> dict:
    """转存到用户根目录，返回 {ok, fid}。

    夸克的 `to_pdir_fid` 必须是真实根目录 fid（写 "0" 报 token 校验异常）；
    UC 的根就是 "0"（2026-11-01 实测）。
    """
    cfg = _cfg(pan)
    tgt = root(cfg["root"]) if pan == "quark" else "0"
    r = _req(cfg["base"] + "share/sharepage/save" + cfg["q"],
             {"fid_list": [fid], "fid_token_list": [tok],
              "to_pdir_fid": tgt, "pwd_id": sid,
              "stoken": stoken, "pdir_fid": str(pdir or "0"),
              "scene": "link"}, ck=ck, ref=cfg["ref"])
    tid = str(((r.get("data") or {}).get("task_id")) or "")
    if not tid:
        return {"ok": 0, "error": "转存失败: %s" % (r.get("message") or r.get("code"))}
    for _ in range(24):
        time.sleep(0.5)
        t = _req(cfg["base"] + "task" + cfg["q"] + "&task_id=" + tid, None,
                 ck=ck, ref=cfg["ref"])
        d = t.get("data") or {}
        if int(d.get("status") or 0) == 2:
            ids = (d.get("save_as") or {}).get("save_as_top_fids") or []
            if ids:
                return {"ok": 1, "fid": str(ids[0])}
            return {"ok": 0, "error": "转存完成但没有 fid"}
        if int(d.get("status") or 0) in (3, 4):
            return {"ok": 0, "error": "转存失败 status=%s" % d.get("status")}
    return {"ok": 0, "error": "转存任务超时"}


def link_of(fid: str, ck: str = "", pan: str = "quark") -> dict:
    """已转存文件 → CDN 直链。**必须用客户端 UA**，否则夸克 23018 / UC 403。"""
    cfg = _cfg(pan)
    ck = ck or (load().get(cfg["root"]) or "")
    if not ck:
        return {"ok": 0, "error": "缺少 %s CK" % cfg["root"]}
    r = _req(cfg["base"] + "file/download" + cfg["q"], {"fids": [str(fid)]},
             ck=ck, ua=cfg["ua"], ref=cfg["ref"])
    d = r.get("data")
    lst = d if isinstance(d, list) else ((d or {}).get("list") or [])
    if not lst:
        return {"ok": 0, "error": "%s %s" % (r.get("code"), r.get("message"))}
    it = lst[0] or {}
    u = it.get("download_url") or ""
    if not u:
        return {"ok": 0, "error": "没有 download_url"}
    return {"ok": 1, "url": u, "size": it.get("size") or 0,
            "name": it.get("file_name") or ""}


def quark_direct(url: str, fid: str, fid_token: str, ck: str = "",
                 pdir_fid: str = "0") -> dict:
    """夸克分享单文件 → 直链。（保留旧签名，内部走新的转存台账）"""
    ck = ck or (load().get("quark") or "")
    if not ck:
        return {"ok": 0, "error": "缺少夸克 CK"}
    st = share_token(url, ck, "quark")
    if not st:
        return {"ok": 0, "error": "分享已失效或需要提取码"}
    sid = share_sid(url)

    key = _xfer_key(url, fid)
    tab = _xfer_load()
    nf = str((tab.get(key) or {}).get("fid") or "")
    if not nf:
        r = _save_as(fid, fid_token, pdir_fid, sid, st, ck, "quark")
        if not r.get("ok"):
            return r
        nf = r["fid"]
        tab[key] = {"fid": nf, "t": int(time.time())}
        _xfer_save(tab)
        _xfer_prune(tab, ck, "quark")
    lk = link_of(nf, ck, "quark")
    if not lk.get("ok"):
        return lk
    return {"ok": 1, "url": lk["url"], "fid": nf}


def bd_ep(url: str, ep: int = 0, title: str = "", ck: str = "") -> dict:
    """百度网盘分享 → 可播地址。

    两条腿：
      ① **原盘直链**（`filemetas?dlink=1`）：必须先转存到自己网盘，
         而转存**必须有有效 BDUSS**。这才是用户要的 4K 原画。
      ② **m3u8 试看**：匿名就能取（`/share/streaming?type=M3U8_AUTO_1080`），
         但百度只给前 ~100 秒。CK 过期时退到这条，至少能看到内容。

    接法（2026-11-02 CDP 抓包实证，之前 errno 2 全是参数错）：
      /share/list 必须 `web=5` + `shorturl=<surl>` + `root=1` + `view_mode=1`
      `/share/tplconfig?fields=sign,timestamp` 拿 sign
    """
    import re as _re
    from . import bdpan

    ck = ck or (load().get("baidu") or "")
    b = bdpan.Bd(ck)
    if not b.open_share(url):
        return {"ok": 0, "error": "百度分享页解析失败（取不到 shareid/uk）"}
    m = _re.search(r"[?&]pwd=(\w+)", url)
    if m:
        b.verify(m.group(1))

    # 递归摊平（分享里常常是「剧名/季/文件」三层套娃）
    files = []

    def _walk(path, depth=0):
        d = b.ls(path)
        if int(d.get("errno") or 0) != 0:
            return
        for it in (d.get("list") or []):
            if str(it.get("isdir")) == "1":
                if depth < 5:
                    _walk(it.get("path") or path, depth + 1)
            else:
                it["file_name"] = it.get("server_filename") or ""
                files.append(it)

    _walk("/")
    if not files:
        return {"ok": 0, "error": "分享里没扫到文件（可能需要提取码或已失效）"}

    f = pick_ep(files, ep, title)
    if f is None:
        return {"ok": 0, "error": "找不到第 %s 集" % (ep or 1), "total": len(files)}

    name = f.get("file_name") or ""
    size = int(f.get("size") or 0)
    fid = str(f.get("fs_id") or "")

    def _hls(path):
        """PCS 在线播放 → m3u8（普通账号可用，不走下载限速）。

        2026-11-03 真机实测（电视 + PG 原生对照）：
        * 百度转码流按「档位」走**不同转码任务**，段长和断点数差别很大：
          - `M3U8_AUTO_1080` 有时是 2 秒一段、**24 个 `EXT-X-DISCONTINUITY`**
            ⇒ 电视上的播放器解到第 7 秒就卡死（有画面、有速度、就是不推进）。
          - `M3U8_AUTO_4K` / `M3U8_AUTO_720` 常见是 **10 秒一段、只有 5~6 个断点**
            ⇒ 能连着播。
        * PG 原生播同一部百度 4K 时走的是 `127.0.0.1:8096/kaiser` 多线程代理，
          底层也是 `v1-ant.baidu.com` 这套转码分片（实测 3.4 MB/s）。
        所以这里**不再盲选 480**，而是把各档都问一遍，挑「断点最少」的那档，
        同断点再按清晰度优先（4K > 1080 > 720 > 480）。断点为 0 立刻收工。
        """
        order = ("M3U8_AUTO_4K", "M3U8_AUTO_1080", "M3U8_AUTO_720",
                 "M3U8_AUTO_480")
        # 档位记忆：同一部片子 30 分钟内只探一次。
        # 之前每次都把 4 档全问一遍，光解析就 25 秒 ⇒ 电视端直接"连接超时"。
        now = time.time()
        hit = _HLS_TYP.get(path)
        if hit and now - hit[0] < 1800:
            txt = b.streaming_path(path, hit[1])
            if txt and "#EXTM3U" in txt:
                return txt, hit[1]
        best = ("", "", 999)
        for typ in order:
            txt = b.streaming_path(path, typ)
            if not txt or "#EXTM3U" not in txt:
                continue
            disc = txt.count("EXT-X-DISCONTINUITY")
            if disc < best[2] or (disc == best[2] and best[0] == ""):
                best = (txt, typ, disc)
            # 6 个断点以内已经能连着播了，别再为了"更少"多问几档（每档 1~2 秒）
            if disc <= 6:
                break
        if best[0]:
            _HLS_TYP[path] = (now, best[1])
        return best[0], best[1]

    # ① 转存后先试**原盘直链**，但要量速度：能跑就用原盘（画质最好），
    #    跑不动（百度对非会员限到 ~100KB/s）就退到 ② 在线播放（HLS）。
    own, own_fid = "", ""
    if ck:
        ok, to = b.transfer(fid)
        # 路径**猜**比**扫**快几个数量级。三条线索：
        #   ① transfer 返回的 `to`（真转存成功的路径）
        #   ② 分享里的相对路径（`/D-动--漫/X-仙-逆/弑仙之战/x.mp4`）—— 转存后
        #      中间目录可能被丢掉，所以从**最短后缀**开始试：`/弑仙之战/x.mp4`、`/x.mp4`
        #   ③ 根目录同名文件
        # 每个候选只列一次父目录（stat_path），全不中才回退全盘找。
        cands = []
        if ok and str(to or ""):
            t = str(to)
            if _re.search(r"\.(mp4|mkv|ts|avi|mov|flv|wmv|rmvb|m2ts|iso)$",
                          t, _re.I):
                cands.append(t)
            else:
                cands.append(t.rstrip("/") + "/" + name)
        sp = str(f.get("path") or "")
        if sp.startswith("/"):
            segs = [x for x in sp.split("/") if x]
            for i in range(len(segs) - 1, -1, -1):     # 越短越先试
                cands.append("/" + "/".join(segs[i:]))
        cands.append("/" + name)
        seen = set()
        for c in cands:
            if c in seen:
                continue
            seen.add(c)
            fid_c = b.stat_path(c)
            if fid_c:
                own, own_fid = c, fid_c
                b.remember(name, own_fid, own)
                break
        if not own:
            info0 = b.own_lookup(name)
            own, own_fid = info0.get("path") or "", info0.get("fid") or ""

    if ck and own:
        dl = b.dlink(own_fid) if own_fid else ""
        if dl:
            # 直链已带 origin=dlna ⇒ 正常 7~8 MB/s。量 4MB 就够定性，
            # 别再用 96KB 的小样本（首包一抖就误判成限速）。
            kbps = _kbps(dl, {"Cookie": ck, **BD_DL_HEADERS},
                         nbytes=4 * 1024 * 1024, timeout=12.0)
            if kbps >= 800:             # 原盘够快 → 就用原盘
                return {"ok": 1, "url": dl, "pan": "baidu", "mode": "origin",
                        "name": name, "size": size, "total": len(files),
                        "kbps": kbps}
        txt, typ = _hls(own)
        if txt:
            return {"ok": 1, "pan": "baidu", "mode": "preview",
                    "url": "https://d.pcs.baidu.com/rest/2.0/pcs/file?"
                           + urllib.parse.urlencode({
                               "method": "streaming", "app_id": "250528",
                               "path": own, "type": typ}),
                    "name": name, "size": size, "total": len(files),
                    "note": "百度原盘限速（非会员），已切在线播放（1080p 转码）"}

    # ② m3u8 试看（分享口，老路，常年 errno 71，留着做最后兜底）
    sign, ts = b.sign_ts()
    if not sign:
        return {"ok": 0, "error": "百度取链失败：转存/直链/在线播放都不通"}
    m3u_url = ("https://pan.baidu.com/share/streaming"
               "?channel=chunlei&uk=%s&fid=%s&sign=%s&timestamp=%s&shareid=%s"
               "&type=M3U8_AUTO_1080&vip=0&jsToken=undefined&isplayer=1"
               "&check_blue=1&adToken="
               % (b.uk, fid, sign, ts, b.shareid))
    # 2026-11-03：这个试看口现在恒回 `{"errno":71}`（sign 校验失败），
    # 再把它当播放地址交给播放器就只剩 3003。这里先探一下是不是真 m3u8。
    try:
        _h = dl_headers("baidu")
        _h["Cookie"] = ck
        _probe = _http_probe(m3u_url, _h)
    except Exception:
        _probe = ""
    if _probe and "#EXTM3U" not in _probe:
        return {"ok": 0,
                "error": "百度只肯给原盘直链，但转存/签名这一步失败了"
                         "（filemetas 没返回 dlink）；试看口也失效"}
    return {"ok": 1, "url": m3u_url, "pan": "baidu", "mode": "preview",
            "name": name, "size": size, "total": len(files),
            "note": "百度 CK 只够试看前 100 秒"}


def ep_direct(url: str, ep: int = 0, title: str = "", pan: str = "",
              budget: float = 9.0) -> dict:
    """分享链接 + 集号 → 可播直链。夸克 / UC 共用这一条实现。

    这是「点完就播」的服务端终点：调用方只管给出「哪一家的哪个分享、
    第几集」，剩下扫目录、挑集、转存、取链全在这。
    """
    pan = pan or pan_of(url)
    if not pan:
        return {"ok": 0, "error": "不支持的网盘: %s" % str(url)[:40]}
    if pan == "baidu":
        # 百度是另一套接口（share/list + tplconfig sign），实现见 bd_ep
        return bd_ep(url, ep, title, ck=(load().get("baidu") or ""))
    cfg = _cfg(pan)
    ck = (load() or {}).get(cfg["root"]) or ""
    if not ck:
        return {"ok": 0, "error": "缺少 %s CK" % cfg["root"]}
    st = share_token(url, ck, pan)
    if not st:
        return {"ok": 0, "error": "分享失效或需要提取码"}
    files = share_tree(url, st, ck, budget=budget)
    if not files:
        return {"ok": 0, "error": "分享里没扫到视频文件"}
    f = pick_ep(files, ep, title)
    if f is None:
        return {"ok": 0, "error": "找不到第 %s 集" % (ep or 1),
                "total": len(files)}
    sid = share_sid(url)

    # ---- ① 免转存优先（2026-11-02 实测）----------------------------------
    # UC 的分享 fid **可以直接 file/download 拿到 download_url**，不必先转存。
    # 这一步解决三个问题：
    #   1. 「用户禁止转存自己的分享」—— 自己账号发出的分享，save 必失败，
    #      以前整条链就死在这里（用户给的第一条 UC 样本就是这样）；
    #   2. 省掉 2~4 秒的转存轮询，点播更快；
    #   3. 不再往用户网盘里塞测试垃圾（一天试播能吃掉几十 GB）。
    # 夸克不支持（回 21001 file not found），失败后照旧走「转存 → 取链」。
    lk = link_of(f["fid"], ck, pan)
    if lk.get("ok"):
        _h = dl_headers(pan)
        _h["Cookie"] = ck
        _sz = probe_size(lk["url"], _h, lk.get("size") or f.get("size") or 0)
        return {"ok": 1, "url": lk["url"], "fid": f["fid"], "pan": pan,
                "no_xfer": 1,
                "name": f.get("file_name") or "",
                "size": _sz,
                "total": len(files)}

    key = _xfer_key(url, f["fid"])
    tab = _xfer_load()
    nf = str((tab.get(key) or {}).get("fid") or "")
    lk = {"ok": 0}
    if nf:
        lk = link_of(nf, ck, pan)
        if not lk.get("ok"):
            # 台账里记的 fid 已经不在了（网盘被清过 / 手动删过 / 换了账号）。
            # 这时**必须作废台账重转一次**，否则会一直 21001 —— 上层就只能
            # 退回 UC，而 UC 非会员只给 ~220KB/s 甚至弹「开通会员」面板。
            tab.pop(key, None)
            _xfer_save(tab)
            nf = ""
    if not nf:
        r = _save_as(f["fid"], f.get("share_fid_token") or "",
                     f.get("_pdir") or "0", sid, st, ck, pan)
        if not r.get("ok"):
            # 多半是网盘空间不够（`save_as_top_fids` 空 + status=3）。
            # 我们自己转存的文件是纯缓存，删掉再试一次即可。
            old_fids = [(tab[k] or {}).get("fid") for k in list(tab)]
            if old_fids and _pan_delete([x for x in old_fids if x], ck, pan):
                for k in list(tab):
                    tab.pop(k, None)
                _xfer_save(tab)
                r = _save_as(f["fid"], f.get("share_fid_token") or "",
                             f.get("_pdir") or "0", sid, st, ck, pan)
        if not r.get("ok"):
            return r
        nf = r["fid"]
        tab[key] = {"fid": nf, "t": int(time.time()),
                    "name": f.get("file_name") or ""}
        _xfer_save(tab)
        _xfer_prune(tab, ck, pan)
        lk = link_of(nf, ck, pan)
    if not lk.get("ok"):
        return lk
    _h2 = dl_headers(pan)
    _h2["Cookie"] = ck
    _sz2 = probe_size(lk["url"], _h2, lk.get("size") or f.get("size") or 0)
    return {"ok": 1, "url": lk["url"], "fid": nf, "pan": pan,
            "name": f.get("file_name") or "",
            "size": _sz2,
            "total": len(files)}


def quark_ep(url: str, ep: int = 0, title: str = "", ck: str = "",
             budget: float = 9.0) -> dict:
    """兼容旧调用：等价于 ep_direct(pan="quark")。"""
    return ep_direct(url, ep, title, pan="quark", budget=budget)


def uc_ep(url: str, ep: int = 0, title: str = "", budget: float = 9.0) -> dict:
    return ep_direct(url, ep, title, pan="uc", budget=budget)


def probe() -> dict:
    """连通性自检（维护页按钮用）。"""
    ck = load()
    return {"quark_ck": bool(ck.get("quark")),
            "uc_ck": bool(ck.get("uc")),
            "baidu_ck": bool(ck.get("baidu")),
            "quark_root": bool(root("quark"))}

