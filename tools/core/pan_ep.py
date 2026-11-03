# -*- coding: utf-8 -*-
"""网盘分享 → 集数清单（2026-10-31）

为什么需要这个
--------------
之前的 `panwall` 只到「分享链接」这一层：详情页给用户 4 条线路名，点播放交给
PG 包自己转存、自己列文件、用户再在弹出来的文件列表里挑一集。
海哥的要求是「播放前必须选集」——**详情页这一层就要知道有几季几集**。

关键实测（2026-10-31，本机真机双验证）
--------------------------------------
1. 四个网盘站（玩偶/木偶/花卷/蜡笔）的详情页**只有整包分享链接**，
   一个「第N集」都不给。所以集数只能从**网盘侧**拿。
2. 夸克/UC 的 `share/sharepage/token` + `share/sharepage/detail` 两个接口
   **无登录态就能用**，返回里带 `file_name` / `fid` / `share_fid_token`。
3. 文件名就是集号载体，两种形态都实测到了：
     A.Record.of.a.Mortals.Journey.to.Immortality.S01E01.2020.2160p...mp4
     Joy.of.Life.2019.S01.EP02.WEB-DL.4K.HEVC.AAC-LeagueWEB.mp4
   还有纯 `E01.mkv`、以及「01风起天南 / 02燕家堡之战」这种**用目录分篇章**的。
4. app 播放单集时用的是本地代理直链（logcat 实测原文）：
     http://127.0.0.1:9978/proxy?do=quark&type=dwnz
       &file_id=<fid>_<share_fid_token>&share_id=<分享id>&pass_code=
       &file_name=<文件名>&ai_vt=<剧名>&ai_ep=<文件名>&quark_88vip=1
   所以拿到 fid + share_fid_token 就能**构造任意一集的播放地址**。

本模块只做「读」：列文件 + 解析季集号 + 给出每集的定位信息。
播放地址的拼接放在 `playurl()`，它不联网。
"""
from __future__ import annotations

import json
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36")

_TOKEN_API = {
    "quark": "https://drive-pc.quark.cn/1/clouddrive/share/sharepage/token?pr=ucpro&fr=pc",
    "uc": "https://pc-api.uc.cn/1/clouddrive/share/sharepage/token?pr=UCBrowser&fr=pc",
}
_DETAIL_API = {
    "quark": "https://drive-pc.quark.cn/1/clouddrive/share/sharepage/detail?pr=ucpro&fr=pc",
    "uc": "https://pc-api.uc.cn/1/clouddrive/share/sharepage/detail?pr=UCBrowser&fr=pc",
}

# 单次列目录上限：一部年番 200+ 文件很常见，给足；再多就砍（详情页也用不完）
MAX_FILES = 400
MAX_PAGES = 8
MAX_DEPTH = 3

_VIDEO_EXT = (".mp4", ".mkv", ".avi", ".ts", ".m2ts", ".rmvb", ".wmv",
              ".flv", ".mov", ".webm", ".mpg", ".mpeg", ".iso")
# 明显不是正片的：预告/花絮/字幕/广告/压缩包
_JUNK_RE = re.compile(
    r"(预告|花絮|片花|特辑|彩蛋|采访|制作|幕后|合集|解说|reaction|"
    r"\.srt$|\.ass$|\.ssa$|\.sub$|\.nfo$|\.txt$|\.jpg$|\.png$|"
    r"sample|trailer|\.zip$|\.rar$|\.7z$|\.torrent$)", re.I)

# 明显不是「正片第 N 集」的命名：剧场版/预告/花絮。它们**不参与集号推断**，
# 但也不从列表里删掉（电影分享只有这一个文件，删了就空了）。
_JUNK_EP_RE = re.compile(
    r"(剧场版|劇場版|movie|预告|預告|花絮|特辑|特典|彩蛋|幕后|幕後|"
    r"making|ova|sp)", re.I)

_CACHE_TTL = 6 * 3600
_cache: dict[str, tuple[float, object]] = {}
_lock = threading.Lock()


# ---------------------------------------------------------------------------
# 基础
# ---------------------------------------------------------------------------

def kind_of(url: str) -> str:
    low = str(url or "").lower()
    if "pan.quark.cn" in low or "drive.quark.cn" in low:
        return "quark"
    if "drive.uc.cn" in low or "pan.uc.cn" in low:
        return "uc"
    # 2026-11-02：百度分享已经能匿名列目录了（bdpan.ls，注意子目录不能带 root=1）
    if "pan.baidu.com" in low or "eyun.baidu.com" in low:
        return "baidu"
    return ""


def share_id(url: str) -> str:
    m = re.search(r"/(?:s|web/share)/([A-Za-z0-9_-]+)", str(url or ""))
    return m.group(1) if m else ""


def _get_json(url: str, body=None, timeout: float = 10.0):
    data, headers = None, {"User-Agent": UA, "Accept": "application/json"}
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def _pwd_of(url: str) -> str:
    """分享提取码（?pwd=xxxx）。夸克有些链接带，UC 基本不带。"""
    m = re.search(r"[?&]pwd=([A-Za-z0-9]{4})", str(url or ""))
    return m.group(1) if m else ""


# ---------------------------------------------------------------------------
# 列文件
# ---------------------------------------------------------------------------

def _walk(kind: str, sid: str, stoken: str, pdir: str = "0",
          depth: int = 0, out: list = None, path: str = "",
          max_pages: int = None) -> list:
    """递归列目录。返回 [{name, fid, sft, size, path}]（只含文件，不含目录）。"""
    out = out if out is not None else []
    if len(out) >= MAX_FILES or depth > MAX_DEPTH:
        return out
    base = _DETAIL_API[kind]
    q = urllib.parse.quote(stoken)
    pages = int(max_pages or MAX_PAGES)
    for page in range(1, pages + 1):
        url = (f"{base}&pwd_id={sid}&stoken={q}&pdir_fid={pdir}&force=0"
               f"&_page={page}&_size=100&_fetch_banner=0&_fetch_share=0"
               f"&_fetch_total=1&_sort=file_type:asc,file_name:asc")
        try:
            r = _get_json(url)
        except Exception:
            break
        lst = (r.get("data") or {}).get("list") or []
        if not lst:
            break
        for it in lst:
            name = str(it.get("file_name") or "")
            if it.get("dir"):
                _walk(kind, sid, stoken, str(it.get("fid") or ""),
                      depth + 1, out, (path + "/" + name) if path else name,
                      max_pages=max_pages)
                if len(out) >= MAX_FILES:
                    return out
                continue
            ext = ("." + name.rsplit(".", 1)[-1].lower()) if "." in name else ""
            if ext not in _VIDEO_EXT:
                continue
            out.append({
                "name": name,
                "fid": str(it.get("fid") or ""),
                "sft": str(it.get("share_fid_token") or ""),
                "size": int(it.get("size") or 0),
                "path": path,
            })
            if len(out) >= MAX_FILES:
                return out
        if len(lst) < 100:
            break
    return out


def _files_baidu_quick(url: str, sid: str, key: str, now: float) -> dict:
    """百度分享的**快速预读**：挑「视频文件最多的那个目录」，最多翻 3 页。

    为什么这么干：年番 100~200 个文件、多层目录，全量递归扫又慢又容易触发
    风控。实测同一部剧常有 `1080P/` 和 `4K/` 两个目录各放一整套，挑一个就够
    （同名集在 `episodes()` 里会按体积去重）。剩余的集号缺口交给
    `infer_rule` 用命名模板补齐，**不猜总数**（`cur_total` 是分享总数，
    不是目录数，拿它外推会多出幽灵集）。
    """
    res = {"ok": False, "kind": "baidu", "sid": sid, "title": "",
           "files": [], "err": "", "total": 0, "dir": ""}
    try:
        from . import bdpan, panck
        ck = (panck.load() or {}).get("baidu") or ""
        b = bdpan.Bd(ck)
        if not b.open_share(url):
            res["err"] = "百度分享页解析失败"
        else:
            m = re.search(r"[?&]pwd=(\w+)", url)
            if m:
                b.verify(m.group(1))

            def videos(items):
                out = []
                for it in items or []:
                    nm = str(it.get("server_filename") or "")
                    if str(it.get("isdir")) == "1":
                        continue
                    ext = ("." + nm.rsplit(".", 1)[-1].lower()) if "." in nm else ""
                    if ext in _VIDEO_EXT:
                        out.append(it)
                return out

            best = [0, ""]                       # [视频数, 目录]

            def scan(pth, depth):
                if depth > 3 or best[0] >= 100:
                    return                       # 已经找到「整页都是视频」的目录
                d = b.ls(pth)
                vs = videos(d.get("list"))
                if len(vs) > best[0]:
                    best[0], best[1] = len(vs), pth
                if best[0] >= 100:
                    return                       # 主目录已确定，别再往下探
                for it in (d.get("list") or []):
                    if str(it.get("isdir")) == "1":
                        scan(it.get("path") or pth, depth + 1)

            scan("/", 0)
            out = []
            if best[1]:
                res["dir"] = best[1]
                for page in (1, 2, 3):           # 最多 300 个文件，够年番
                    d = b.ls(best[1], page=page)
                    vs = videos(d.get("list"))
                    for it in vs:
                        out.append({"name": it.get("server_filename") or "",
                                    "fid": str(it.get("fs_id") or ""),
                                    "sft": "",
                                    "size": int(it.get("size") or 0),
                                    "path": best[1]})
                    if len(vs) < 100:
                        break
            res["files"] = out
            res["total"] = len(out)
            res["ok"] = bool(out)
            if not out:
                res["err"] = "分享里没有视频文件"
    except Exception as e:
        res["err"] = "%s: %s" % (type(e).__name__, e)

    with _lock:
        if len(_cache) > 300:
            for k in [k for k, (e, _) in _cache.items() if e < time.time()]:
                _cache.pop(k, None)
        _cache[key] = (now + (_CACHE_TTL if res["ok"] else 600), res)
    return res


def _files_baidu(url: str, sid: str, key: str, now: float) -> dict:
    """百度分享 → 视频文件清单（匿名可列，不需要 BDUSS）。

    走 bdpan.Bd：open_share 拿 shareid/uk → verify 过提取码 → 递归 ls。
    **子目录列举不能带 root=1**（bdpan.ls 已处理），否则永远回分享根。
    """
    res = {"ok": False, "kind": "baidu", "sid": sid, "title": "",
           "files": [], "err": ""}
    try:
        from . import bdpan, panck
        ck = (panck.load() or {}).get("baidu") or ""
        b = bdpan.Bd(ck)
        if not b.open_share(url):
            res["err"] = "百度分享页解析失败"
        else:
            m = re.search(r"[?&]pwd=(\w+)", url)
            if m:
                b.verify(m.group(1))
            out: list = []

            def walk(pth: str, depth: int) -> None:
                if depth > 4 or len(out) >= MAX_FILES:
                    return
                d = b.ls(pth)
                if int(d.get("errno") or 0) != 0:
                    return
                for it in (d.get("list") or []):
                    nm = str(it.get("server_filename") or "")
                    if str(it.get("isdir")) == "1":
                        walk(it.get("path") or pth, depth + 1)
                        if len(out) >= MAX_FILES:
                            return
                        continue
                    ext = ("." + nm.rsplit(".", 1)[-1].lower()) if "." in nm else ""
                    if ext not in _VIDEO_EXT:
                        continue
                    out.append({"name": nm,
                                "fid": str(it.get("fs_id") or ""),
                                "sft": "",
                                "size": int(it.get("size") or 0),
                                "path": pth})
            walk("/", 0)
            res["files"] = out
            res["ok"] = bool(out)
            if not out:
                res["err"] = "分享里没有视频文件"
    except Exception as e:
        res["err"] = "%s: %s" % (type(e).__name__, e)

    with _lock:
        if len(_cache) > 300:
            for k in [k for k, (e, _) in _cache.items() if e < time.time()]:
                _cache.pop(k, None)
        _cache[key] = (now + (_CACHE_TTL if res["ok"] else 600), res)
    return res


def files(url: str, quick: bool = False) -> dict:
    """分享 → {ok, kind, sid, title, files:[...]}；失败 ok=False。有缓存。

    `quick=True` 只给「预读选集」用：百度只下钻到**第一个装着一集一文件的目录**
    取首页（100 条）+ `cur_total`，剩下的集号交给 `infer_rule` 拼出来，
    不再整棵树扫（2026-11-03 海哥要求：又慢又容易触发风控）。
    播放时的精确定位仍走 `quick=False`（缓存键分开，互不污染）。
    """
    url = str(url or "").strip()
    kind, sid = kind_of(url), share_id(url)
    if not kind or not sid:
        return {"ok": False, "kind": kind, "sid": sid, "title": "", "files": [],
                "err": "不支持的网盘"}
    key = f"ep:{kind}:{sid}" + (":q" if quick else "")
    now = time.time()
    with _lock:
        hit = _cache.get(key)
        if hit and hit[0] > now:
            return hit[1]                      # type: ignore[return-value]

    if kind == "baidu":
        if quick:
            return _files_baidu_quick(url, sid, key, now)
        return _files_baidu(url, sid, key, now)

    res = {"ok": False, "kind": kind, "sid": sid, "title": "", "files": [], "err": ""}
    try:
        t = _get_json(_TOKEN_API[kind], {"pwd_id": sid, "passcode": _pwd_of(url)})
        d = t.get("data") or {}
        st = d.get("stoken")
        res["title"] = str(d.get("title") or "")
        if not st:
            res["err"] = f"token 失败({t.get('code')}/{t.get('message')})"
        else:
            # 预读只要前 3 页（300 个文件，年番也够了）；缺口交给命名规则补
            res["files"] = _walk(kind, sid, st,
                                 max_pages=(3 if quick else MAX_PAGES))
            res["ok"] = bool(res["files"])
            if not res["ok"]:
                res["err"] = "分享里没有视频文件"
    except urllib.error.HTTPError as e:
        res["err"] = f"HTTP {e.code}（分享可能已失效）"
    except Exception as e:
        res["err"] = f"{type(e).__name__}: {e}"

    # 成功缓 6 小时；空结果只缓 10 分钟（站方会补种）
    with _lock:
        if len(_cache) > 300:
            for k in [k for k, (e, _) in _cache.items() if e < now]:
                _cache.pop(k, None)
        _cache[key] = (now + (_CACHE_TTL if res["ok"] else 600), res)
    return res


# ---------------------------------------------------------------------------
# 文件名 → 季/集
# ---------------------------------------------------------------------------

_CN_NUM = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7,
           "八": 8, "九": 9, "十": 10, "零": 0}


def _cn2int(s: str) -> int:
    s = str(s or "").strip()
    if not s:
        return 0
    if s.isdigit():
        return int(s)
    if s in _CN_NUM:
        return _CN_NUM[s]
    # 十一 / 二十三 / 三十四
    if "十" in s:
        a, _, b = s.partition("十")
        n = (_CN_NUM.get(a, 1) if a else 1) * 10
        return n + (_CN_NUM.get(b, 0) if b else 0)
    return 0


# 1) S01E02 / S01.EP02 / S01 E02 / s1e2 / S01-EP02
_RE_SXXEXX = re.compile(r"[Ss](\d{1,2})[\s._\-]*[Ee](?:[Pp])?(\d{1,4})")
# 2) 20x 常见「第01集」/「第1话」
_RE_CNEP = re.compile(r"第\s*([一二三四五六七八九十百\d]{1,5})\s*[集话回期]")
# 3) EP01 / E01（要求前后是分隔符，避免误吞 1080p 里的数字）
_RE_EP = re.compile(r"(?:^|[^A-Za-z0-9])[Ee][Pp]?[\s._\-]*(\d{1,4})(?!\d)")
# 4) 纯数字一集一个文件：01.mp4 / "12.mkv" / "第12.mp4"
_RE_NUM = re.compile(r"(?:^|[^\d])(\d{1,4})(?=\s*[.．]?\s*(?:mp4|mkv|avi|ts|rmvb|wmv|flv|mov|webm|m2ts)\b)", re.I)
# 5) 季：第N季 / Season N / S N
_RE_SEASON = re.compile(r"第\s*([一二三四五六七八九十\d]{1,3})\s*季|[Ss]eason\s*(\d{1,2})", re.I)


def parse_name(name: str, path: str = "") -> tuple[int, int]:
    """从文件名(+所在目录)解析 (季, 集)。解析不出返回 (0, 0)。"""
    stem = str(name or "")
    hay = (path + "/" + stem) if path else stem

    season, ep = 0, 0
    m = _RE_SXXEXX.search(hay)
    if m:
        season, ep = int(m.group(1)), int(m.group(2))
    if not ep:
        m = _RE_CNEP.search(stem)
        if m:
            ep = _cn2int(m.group(1))
    if not ep:
        m = _RE_EP.search(stem)
        if m:
            ep = int(m.group(1))
    if not ep:
        m = _RE_NUM.search(stem)
        if m:
            cand = int(m.group(1))
            # `H.264.mp4` / `1080p` 里的数字不是集号（2026-11-01 真机实拍：
            # 《希望》的 3 个文件被识别成「第1集 / 第264集 / 第265集」，
            # 就是 H.264 / H.265 两个编码号被当成了集号）。
            if cand not in (240, 264, 265, 480, 576, 720, 1080, 1440, 2160, 4320):
                ep = cand
    if not season:
        m = _RE_SEASON.search(hay)
        if m:
            season = _cn2int(m.group(1) or m.group(2) or "")
    return (season if 0 < season < 100 else 0,
            ep if 0 < ep < 10000 else 0)


def _is_junk(name: str) -> bool:
    return bool(_JUNK_RE.search(str(name or "")))


# ---------------------------------------------------------------------------
# 命名规则推断（2026-11-03 海哥：预读别全量扫，只看首尾就能拼出整部）
# ---------------------------------------------------------------------------
#
# 一部年番 100~200 个文件，全量列目录又慢又容易触发风控。实际上网盘的命名
# 是**机器生成**的（`XN.S01E02.mp4`、`第03集.mp4`、`03.mp4`），只要拿到几个
# 样本就能反推出模板，剩下的集号自己拼 —— 不需要真的把文件列出来。
#
# 模板用 `@@EP@@` 当集号占位符（不会出现在文件名里，避免和真实字符冲突）。


def _stem_ext(name: str) -> tuple:
    n = str(name or "")
    i = n.rfind(".")
    if i > 0 and len(n) - i <= 6:
        return n[:i], n[i:].lower()
    return n, ""


def _tpl_of(name: str, ep: int) -> tuple:
    """把一个文件名里的集号换成占位符 → (模板, 扩展名, 补零位数)。"""
    stem, ext = _stem_ext(name)
    ep = int(ep or 0)
    if not stem or ep <= 0:
        return None, ext, 0
    # 按「数字段」匹配，不能用 rfind(str(ep))：`S01E01` 里找 "1" 会命中最末尾那个
    # "1"，模板变成 `S01E0@@EP@@`，第 12 集就拼成 `S01E012`。必须整段比较。
    best = None
    for m in re.finditer(r"\d+", stem):
        if int(m.group(0)) == ep:
            best = m                     # 集号通常靠后，取最后一个命中的数字段
    if not best:
        return None, ext, 0
    w = len(best.group(0))
    return (stem[:best.start()] + "@@EP@@" + stem[best.end():], ext, w)


def synth_name(rule: dict, ep: int) -> str:
    """按模板拼出第 ep 集的文件名。"""
    if not rule:
        return ""
    return (rule["tpl"].replace("@@EP@@", str(int(ep)).zfill(rule["width"]))
            + rule["ext"])


def infer_rule(items: list) -> dict:
    """从一批 {name, ep, season} 推断命名模板。

    只认「同一季 + 模板一致 + 命中率 ≥60% + 样本≥2」的情形，否则返回 None
    （宁可老实列目录，也别拼错名字去转存一个不存在的文件）。
    """
    from collections import Counter
    tpls, exts, seas = Counter(), Counter(), Counter()
    seen = {}
    for it in items or []:
        nm = str(it.get("name") or "")
        ep = int(it.get("ep") or 0)
        if ep <= 0 or _JUNK_EP_RE.search(nm):
            continue
        t, ext, w = _tpl_of(nm, ep)
        if not t:
            continue
        tpls[(t, w)] += 1
        exts[ext] += 1
        seas[int(it.get("season") or 0)] += 1
        seen.setdefault((t, w), {})[ep] = nm
    if not tpls:
        return None
    (t, w), n = tpls.most_common(1)[0]
    if n < 2 or n < len(items or []) * 0.6:
        return None
    return {"tpl": t, "ext": exts.most_common(1)[0][0], "width": w,
            "season": seas.most_common(1)[0][0],
            "have": sorted(seen[(t, w)].keys()), "hit": n}


def _junk_cnt(names: list) -> int:
    """这批文件名里有几个是「非正片」（剧场版/预告/花絮/字幕…）。"""
    return sum(1 for n in (names or [])
               if _JUNK_EP_RE.search(str(n)) or _is_junk(str(n)))


def episodes(url: str, quick: bool = False) -> dict:
    """分享 → 集数清单。

    返回 {
      ok, title, kind, sid, total,
      seasons: [{season, label, eps: [{ep, label, name, fid, sft, size, path}]}],
      unnamed: [...]        # 解析不出集号的视频（保留，别丢）
    }
    同名集去重（网盘常有「4K/1080P」两个版本目录，取体积大的那个）。
    """
    f = files(url, quick=quick)
    if not f.get("ok"):
        return {"ok": False, "title": f.get("title", ""), "kind": f.get("kind", ""),
                "sid": f.get("sid", ""), "total": 0, "seasons": [], "unnamed": [],
                "err": f.get("err", "")}

    seen: dict[tuple[int, int], dict] = {}
    unnamed: list[dict] = []
    for it in f["files"]:
        if _is_junk(it["name"]):
            continue
        s, e = parse_name(it["name"], it.get("path", ""))
        if not e:
            unnamed.append({**it, "season": s, "ep": 0})
            continue
        k = (s, e)
        old = seen.get(k)
        if old is None or it["size"] > old["size"]:
            seen[k] = {**it, "season": s, "ep": e}

    # 一集都没有 → 大概率是电影（单个大文件），或文件名完全没有数字
    has_season = any(s > 0 for s, _ in seen.keys())
    buckets: dict[int, list] = {}
    for (s, e), it in seen.items():
        buckets.setdefault(s, []).append(it)

    seasons = []
    hint_total = int(f.get("total") or 0)      # 百度快速预读给的目录总条数
    for s in sorted(buckets.keys()):
        eps = sorted(buckets[s], key=lambda x: x["ep"])
        # ---- 命名规则推断 + 缺集补全（2026-11-03 海哥）----
        # 只列了首页（或截图被截断）时，用模板把中间/尾部集号拼出来，
        # 让选集区一上来就是完整集数；拼出来的条目 fid 为空（synth=1），
        # 真正点播时 `bd_ep`/`ep_direct` 会按名字去定位，不影响正确性。
        rule = infer_rule([{"name": it["name"], "ep": it["ep"], "season": s}
                           for it in eps])
        have = [it["ep"] for it in eps]
        hi = max(have) if have else 0
        # 注意：**不要**拿 `cur_total` 去外推尾部集号 —— 它是分享总文件数
        # （含剧场版/花絮/其他目录），拿它算会多出幽灵集（仙逆实测多出 E166）。
        # 这里只补「1..max(已见)」之间的缺口，尾部以真实列出的最大集号为准。
        if rule and hi > len(have):
            byep = {it["ep"]: it for it in eps}
            filled = []
            for e in range(1, hi + 1):
                it = byep.get(e)
                if it:
                    filled.append({"ep": e, "label": f"第{e}集",
                                   "name": it["name"], "fid": it["fid"],
                                   "sft": it["sft"], "size": it["size"]})
                else:
                    filled.append({"ep": e, "label": f"第{e}集",
                                   "name": synth_name(rule, e), "fid": "",
                                   "sft": "", "size": 0, "synth": 1})
            eps = filled
        if s > 0:
            label = f"第{s}季"
        else:
            label = "正片" if has_season else "全集"
        seasons.append({
            "season": s,
            "label": label,
            "eps": [{"ep": it["ep"], "label": f"第{it['ep']}集",
                     "name": it["name"], "fid": it["fid"], "sft": it["sft"],
                     "size": it["size"], "synth": it.get("synth", 0)}
                    for it in eps],
        })

    # 完全解析不出集号：按文件名排一遍，做成「第1集…第N集」的顺序集
    # （「01风起天南」这类单文件一话的情况会落到这里）
    if not seasons and unnamed:
        unnamed.sort(key=lambda x: x["name"])
        seasons = [{
            "season": 0, "label": "全集",
            "eps": [{"ep": i + 1, "label": _pretty_ep_name(it["name"], i + 1),
                     "name": it["name"], "fid": it["fid"], "sft": it["sft"],
                     "size": it["size"]} for i, it in enumerate(unnamed)],
        }]

    total = sum(len(s["eps"]) for s in seasons)
    return {"ok": bool(seasons), "title": f.get("title", ""), "kind": f.get("kind", ""),
            "sid": f.get("sid", ""), "total": total, "seasons": seasons,
            "unnamed": unnamed, "err": "" if seasons else "解析不出集数"}


def _pretty_ep_name(name: str, ep: int) -> str:
    """没有集号的片子，用文件名里的中文片段当标题（「01风起天南」→「风起天南」）。"""
    stem = str(name or "").rsplit(".", 1)[0]
    stem = re.sub(r"^[\s\d._\-]+", "", stem)
    stem = re.sub(r"[Ss]\d{1,2}[Ee]\d{1,4}.*$", "", stem)
    stem = stem.strip(" ._-")
    return f"第{ep}集 · {stem}" if stem else f"第{ep}集"


# ---------------------------------------------------------------------------
# 播放地址
# ---------------------------------------------------------------------------

APP_PROXY = "http://127.0.0.1:9978/proxy"


def playurl(url: str, item: dict, title: str = "") -> str:
    """构造「本机直链」播放地址 —— **注意：这个地址目前是错的，别再用**。

    2026-11-01 反查 pg.jar（assets/pg.jar 里的 classes.dex）地面事实：
      * jar 自带的本地服务固定在 `http://127.0.0.1:10078`（实际监听 10079，
        install-recovery.sh 里的 iptables REDIRECT 10078→10079 就是为它）；
      * dex 里出现的端点只有 `/ping`、`/yt/enhance`、`/yt/nsig`、`/yt/ping`、
        `/proxy?`，**没有任何 `dwnz` / `do=quark` / `do=uc` 字样**；
      * `:9978` 是 app 自己的 WebServer，它的 `/proxy` 是个通用 URL 代理，
        带 `?do=quark&type=dwnz…` 请求会直接 500
        （`Attempt to read from null array`，adb forward 实测）。

    也就是说「拼一条 http://127.0.0.1:9978/proxy?do=… 就能单集起播」这个假设
    不成立 —— 网盘转存 + 取直链全在 app/jar 内部，外部拿不到那个 URL。
    单集直达目前**只能**通过站点选集页（`haiplay://play?site=TMDBWall&id=`）。
    返回值保留原样只为不破坏上层结构：`routes[].href` 由
    `window.__haiDirectOk` 把关，页面永远走选集页那条正路。

    item 需要 fid + sft（share_fid_token）。夸克和 UC 的参数名不同，按 kind 分。
    拼不出来返回空串。
    """
    kind, sid = kind_of(url), share_id(url)
    fid, sft = str(item.get("fid") or ""), str(item.get("sft") or "")
    name = str(item.get("name") or "")
    if not kind or not sid or not fid:
        return ""
    q = urllib.parse.quote
    vt = q(str(title or ""))
    if kind == "quark":
        file_id = f"{fid}_{sft}" if sft else fid
        return (f"{APP_PROXY}?do=quark&type=dwnz&file_id={file_id}"
                f"&share_id={sid}&pass_code=&file_name={q(name)}"
                f"&ai_vt={vt}&ai_ep={q(name)}&quark_88vip=1")
    file_id = f"{fid}_{sft}" if sft else fid
    return (f"{APP_PROXY}?do=uc&type=dwnz&file_id={file_id}"
            f"&share_id={sid}&pass_code=&file_name={q(name)}&ai_vt={vt}&ai_ep={q(name)}")


# ---------------------------------------------------------------------------
# 按剧名汇总各站源集数（详情页用）
# ---------------------------------------------------------------------------

def source_eps(title: str, year: str = "", budget: float = 6.0) -> dict:
    """剧名 → 各站源 × 各网盘商的集数清单。

    数据来源是 `panwall.collect()` 给的那批分享链接（同一套缓存，不重复抓站）。
    每条链接并行去网盘列文件，总预算 budget 秒 —— 到点没回来的先标 pending，
    下一次刷新就命中缓存直接出结果（详情页每开一次都会重新问一遍）。

    返回 {"ok":1, "title":..., "sources":[{site, pan, link, ok, total, seasons, err}]}
    """
    title = str(title or "").strip()
    if not title:
        return {"ok": 0, "title": "", "sources": [], "error": "缺少片名"}
    try:
        from . import panwall
        # 选集必须三站都问（collect_full）；collect() 命中第一个站就返回，
        # 首个站只给百度链接时选集区会空着（2026-11-01 实测）。
        groups = panwall.collect_full(title, year, budget=11.0)
        if not groups:
            groups = panwall.collect(title, year)
    except Exception as e:
        return {"ok": 0, "title": title, "sources": [],
                "error": "%s: %s" % (type(e).__name__, e)}
    if not groups:
        return {"ok": 0, "title": title, "sources": [], "error": "各站源都没有可用分享"}

    tasks = []
    for gname, urls in groups[:8]:
        parts = str(gname).split("·")
        site = parts[0] if parts else gname
        pan = parts[1] if len(parts) >= 2 else ""
        if gname in ("全集",) or not urls:
            continue
        u = str(urls).split("#")[0].split("$", 1)[-1].strip()
        if not u:
            continue
        if not kind_of(u):
            # 百度/阿里/115 暂不支持无登录态列目录，如实标注，别显示成 0 集
            tasks.append((site, pan, u, None))
            continue
        tasks.append((site, pan, u, "todo"))

    out: list = [None] * len(tasks)
    end = time.time() + max(2.0, budget)

    def work(i: int, site: str, pan: str, url: str, flag) -> None:
        if flag is None:
            out[i] = {"site": site, "pan": pan, "link": url, "ok": 0,
                      "total": 0, "seasons": [],
                      "err": "暂不支持解析该网盘（夸克/UC 已支持）"}
            return
        # 预读走 quick：百度只取首页+总条数，集号靠命名规则拼，不整树扫
        r = episodes(url, quick=True)
        out[i] = {"site": site, "pan": pan, "link": url, "ok": 1 if r["ok"] else 0,
                  "total": r["total"], "seasons": r["seasons"],
                  "err": r.get("err", "")}

    ths = []
    for i, (site, pan, u, flag) in enumerate(tasks):
        t = threading.Thread(target=work, args=(i, site, pan, u, flag), daemon=True)
        t.start()
        ths.append(t)
    for t in ths:
        t.join(max(0.02, end - time.time()))

    done, pending = [], 0
    for i in range(len(tasks)):
        if out[i] is None:
            pending += 1
            site, pan, u, _ = tasks[i]
            out[i] = {"site": site, "pan": pan, "link": u, "ok": 0,
                      "total": 0, "seasons": [], "pending": 1, "err": "解析中"}
        else:
            done.append(out[i])

    # 有集数的排前面，同按集数多少
    done.sort(key=lambda x: (-(x.get("total") or 0), x.get("site") or ""))
    return {"ok": 1 if done else 0, "title": title, "sources": done,
            "pending": pending}


def human_size(n: int) -> str:
    try:
        n = int(n)
    except Exception:
        return ""
    if n <= 0:
        return ""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f}{unit}" if unit in ("B", "KB") else f"{n:.2f}{unit}"
        n /= 1024.0
    return ""
