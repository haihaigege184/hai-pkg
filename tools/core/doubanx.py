# -*- coding: utf-8 -*-
"""豆瓣数据源 —— 海报墙的第二套数据（2026-10-31 海哥需求）

为什么加这一套：TMDB 对国产剧榜单更新太慢。实测「嫁金钗」（豆瓣：更新至
23 集 / 7.0 分 / 7 万人评）在 TMDB「热门剧集」里根本排不上，海报墙等于漏掉
当下最热的国产剧。豆瓣的榜单明显更贴国内观众，所以做成可切换的数据源，
由海报墙按「设置键」呼出菜单切换，两套数据各自保留自己的类目/筛选。

三条接口都不需要 key（已实测）：
  1) https://movie.douban.com/j/search_subjects?type=tv|movie&tag=&sort=&page_start=
     榜单。sort: recommend(推荐) / time(最新) / rank(高分)
  2) https://m.douban.com/rexxar/api/v2/{tv|movie}/{id}
     详情：简介 / 评分 / 国家 / 类型 / 导演 / 演员 / 集数
  3) https://m.douban.com/rexxar/api/v2/{tv|movie}/{id}/recommendations?start=&count=
     相关推荐（无限推荐链就靠它）

两个必须知道的坑：
  * 图片直连一律 418，**必须**带 `Referer: https://movie.douban.com/`。
    所以端上一律走 `/hai/dbimg/` 代理，别让电视直连 doubanio。
  * 集群会限流（403/418 空响应）。单条失败只影响这一条，别整页崩；
    连续失败把该接口冷 3 分钟，期间返回空列表而不是每张海报都等超时。
"""

from __future__ import annotations

import json
import re
import threading
import time
import urllib.parse
import urllib.request

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36")
_REF = "https://movie.douban.com/"

# 类目：(cid, 名称, kind, 默认 tag, 固定地区)
# kind 只有 tv / movie 两种，决定走哪个接口。
CLASSES = [
    ("dbtv_hot", "热门剧集", "tv", "热门", ""),
    ("dbtv_cn", "国产剧", "tv", "国产剧", "CN"),
    ("dbtv_us", "美剧", "tv", "美剧", "US"),
    ("dbtv_kr", "韩剧", "tv", "韩剧", "KR"),
    ("dbtv_jp", "日剧", "tv", "日剧", "JP"),
    ("dbtv_hk", "港剧", "tv", "港剧", "HK"),
    ("dbtv_tw", "台剧", "tv", "台剧", "TW"),
    ("dbtv_va", "综艺", "tv", "综艺", ""),
    ("dbtv_an", "动画", "tv", "动画", ""),
    ("dbmv_hot", "热门电影", "movie", "热门", ""),
    ("dbmv_cn", "华语电影", "movie", "华语", "CN"),
    ("dbmv_west", "欧美电影", "movie", "欧美", "US"),
    ("dbmv_score", "高分电影", "movie", "豆瓣高分", ""),
]
_CLASS_BY_ID = {c[0]: c for c in CLASSES}

# 筛选项（豆瓣只认 tag + sort，所以地区/类型最终都归一成一个 tag）
COUNTRIES = [("", "全部"), ("CN", "国产"), ("US", "美国"), ("GB", "英国"),
             ("JP", "日本"), ("KR", "韩国"), ("HK", "香港"), ("TW", "台湾")]
SORTS = [("", "默认"), ("recommend", "推荐"), ("time", "最新"), ("rank", "高分")]
GENRES = [("", "全部"), ("剧情", "剧情"), ("喜剧", "喜剧"), ("爱情", "爱情"),
          ("悬疑", "悬疑"), ("动作", "动作"), ("科幻", "科幻"), ("犯罪", "犯罪"),
          ("古装", "古装"), ("恐怖", "恐怖"), ("动画", "动画"), ("纪录片", "纪录片")]

# 地区 → tag（剧集 / 电影各一套；movie 的华语/欧美是豆瓣自己的 tag）
_TAG_TV = {"CN": "国产剧", "US": "美剧", "GB": "英剧", "JP": "日剧",
           "KR": "韩剧", "HK": "港剧", "TW": "台剧"}
_TAG_MV = {"CN": "华语", "US": "欧美", "GB": "欧美", "JP": "日本", "KR": "韩国",
           "HK": "华语", "TW": "华语"}

_LOCK = threading.Lock()
_cache: dict[str, tuple[float, object]] = {}
_cool: dict[str, float] = {}          # 接口级冷却（命中限流时）
_TTL_LIST = 15 * 60
_TTL_META = 6 * 3600


def _cache_get(k):
    with _LOCK:
        v = _cache.get(k)
        if not v:
            return None
        if v[0] < time.time():
            _cache.pop(k, None)
            return None
        return v[1]


def _cache_set(k, v, ttl=_TTL_LIST):
    with _LOCK:
        if len(_cache) > 800:
            now = time.time()
            for kk in [x for x, (e, _) in _cache.items() if e < now]:
                _cache.pop(kk, None)
        _cache[k] = (time.time() + ttl, v)


def _get(url: str, timeout: float = 8.0) -> str:
    """带 UA + Referer 的 GET。空/报错一律返回空串，调用方自己决定怎么办。"""
    host = urllib.parse.urlparse(url).netloc
    if _cool.get(host, 0) > time.time():
        return ""
    req = urllib.request.Request(url, headers={
        "User-Agent": UA, "Referer": _REF,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read().decode("utf-8", "replace")
    except Exception:
        _cool[host] = time.time() + 180
        return ""


def _json(url: str, timeout: float = 8.0):
    s = _get(url, timeout)
    if not s:
        return None
    try:
        return json.loads(s)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# 榜单
# ---------------------------------------------------------------------------

def classes():
    """给页面渲染类目条用：(cid, 名称)"""
    return [(c[0], c[1]) for c in CLASSES]


def _pick_tag(cls, country: str, genre: str) -> str:
    """地区/类型 → 豆瓣 tag。类型优先（豆瓣的 tag 空间里类型更细）。"""
    _, _, kind, base_tag, fixed = cls
    region = country or fixed
    if genre:
        # 「动画」在剧集侧豆瓣叫「动画」，电影侧也认，直接用
        return genre
    if region:
        m = (_TAG_TV if kind == "tv" else _TAG_MV).get(region)
        if m:
            return m
    return base_tag


def list_page(cid: str, page: int = 1, country: str = "", sort: str = "",
              genre: str = "") -> dict:
    cls = _CLASS_BY_ID.get(cid) or CLASSES[0]
    _, _, kind, _, _ = cls
    tag = _pick_tag(cls, country, genre)
    page = max(1, int(page or 1))
    srt = sort or "recommend"
    ck = "list:%s:%s:%s:%s:%d" % (cid, tag, srt, country, page)
    hit = _cache_get(ck)
    if hit is not None:
        return hit

    step = 20
    url = ("https://movie.douban.com/j/search_subjects?type=%s&tag=%s&sort=%s"
           "&page_limit=%d&page_start=%d" % (kind, urllib.parse.quote(tag), srt,
                                             step, (page - 1) * step))
    data = _json(url) or {}
    items = []
    for it in (data.get("subjects") or []):
        sid = str(it.get("id") or "")
        if not sid:
            continue
        rate = str(it.get("rate") or "").strip()
        eps = str(it.get("episodes_info") or "").strip()
        # 徽标优先「更新至N集」（剧集最有用），没有就退评分
        badge = eps or (rate and f"{rate} 分") or ""
        items.append({
            "vod_id": "db" + sid,
            "vod_name": it.get("title") or "",
            "vod_pic": relpic(it.get("cover")),
            "vod_remarks": badge,
            "vod_year": "",
            "vod_area": "",
            "type_name": "剧集" if kind == "tv" else "电影",
            "_rate": rate,
            "_eps": eps,
        })
    out = {
        "page": page,
        # 豆瓣不给总数：有满页就认为还能翻，上限 20 页（sort=time 时再往后就重复了）
        "pagecount": 20 if len(items) >= step else page,
        "limit": step,
        "total": len(items),
        "list": items,
        "tag": tag,
    }
    if items:
        _cache_set(ck, out, _TTL_LIST)
    return out


# ---------------------------------------------------------------------------
# 详情
# ---------------------------------------------------------------------------

def _as_int(v, d=0):
    try:
        return int(str(v).strip() or d)
    except (TypeError, ValueError):
        return d


def relpic(u: str) -> str:
    """豆瓣图 → 站内相对路径 `/hai/dbimg/...`

    直连 doubanio 必 418（防盗链），所以一律走中枢代理；用**相对**路径是
    为了适配任意入口（局域网 8099 / 公网 CF），App 侧再由接口层按请求 Host
    补成绝对地址。
    """
    s = str(u or "")
    m = re.match(r"^https?://[a-z0-9]*\.doubanio\.com/(view/photo/.+)$", s)
    if m:
        return "/hai/dbimg/" + m.group(1)
    return s


def _clean_intro(s: str) -> str:
    """豆瓣简介里夹着「©豆瓣」和不少换行，收拾干净。"""
    t = str(s or "")
    t = re.sub(r"\s*©\s*豆瓣\s*$", "", t.strip())
    t = re.sub(r"[ \t]+", " ", t)
    return t.strip()


def _kv_people(lst, limit=12):
    out = []
    for p in (lst or [])[:limit]:
        if isinstance(p, dict):
            n = str(p.get("name") or "").strip()
            if n:
                out.append(n)
    return out


def _detail_raw(sid: str, kind: str):
    return _json("https://m.douban.com/rexxar/api/v2/%s/%s" % (kind, sid))


def detail(vid: str, with_plays: bool = False) -> dict:
    """豆瓣详情。vid 形如 `db37191789`。

    `with_plays` 只为跟 tmdbwall.detail 保持签名一致 —— 豆瓣本身没有播放源，
    播放一律靠片名去网盘站源匹配，这里永远不返回播放数据。
    """
    sid = str(vid or "").strip()
    if sid.startswith("db"):
        sid = sid[2:]
    if not sid.isdigit():
        return {}
    ck = "meta:" + sid
    hit = _cache_get(ck)
    if hit is not None:
        return hit or {}

    raw = _detail_raw(sid, "tv")
    kind = "tv"
    if not raw or not raw.get("title"):
        raw = _detail_raw(sid, "movie")
        kind = "movie"
    if not raw or not raw.get("title"):
        return {}

    title = raw.get("title") or ""
    year = str(raw.get("year") or "")
    rating = raw.get("rating") or {}
    score = rating.get("value") or 0
    cnt = rating.get("count") or 0
    intro = _clean_intro(raw.get("intro") or "")
    cover = raw.get("cover_url") or ((raw.get("pic") or {}).get("large") or "")
    countries = [c for c in (raw.get("countries") or []) if c]
    genres = [g for g in (raw.get("genres") or []) if g]
    directors = _kv_people(raw.get("directors"), 4)
    actors = _kv_people(raw.get("actors"), 14)
    eps = _as_int(raw.get("episodes_count"), 0)
    eps_info = str(raw.get("episodes_info") or "")
    durations = [d for d in (raw.get("durations") or []) if d]

    remarks = ""
    if eps_info:
        remarks = eps_info
    elif eps and kind == "tv":
        remarks = "全 %d 集" % eps
    elif score:
        remarks = "%s 分" % score

    cast = [{"name": n, "character": "", "profile": ""} for n in actors]
    crew = [{"name": n, "job": "Director"} for n in directors]

    # 背景大图：豆瓣只给海报。用 l_ratio_poster（大图）当背景，
    # 加一张原海报做轮播第二帧，够用且不额外请求。
    big = re.sub(r"/(m|s)_ratio_poster/", "/l_ratio_poster/", cover)
    backdrops = [big] if big and big != cover else []

    recs = _recs(sid, kind)
    out = {
        "vod_id": "db" + sid,
        "vod_name": title,
        "vod_pic": relpic(cover),
        "vod_pic_slide": relpic(big or cover),
        "vod_year": year,
        "vod_area": " / ".join(countries),
        "vod_lang": "",
        "vod_remarks": remarks,
        "vod_content": intro,
        "type_name": ("剧集" if kind == "tv" else "电影")
                     + (" · " + genres[0] if genres else ""),
        "vod_genres": genres,
        "vod_director": " / ".join(directors),
        "vod_actor": " / ".join(actors[:8]),
        "vod_cast": cast,
        "vod_crew": crew,
        "vod_logo": "",
        "vod_backdrops": backdrops,
        "vod_recs": recs,
        "vod_score": score,
        "vod_score_cnt": cnt,
        "vod_episodes": eps,
        "src": "douban",
    }
    _cache_set(ck, out, _TTL_META)
    return out


def _recs(sid: str, kind: str, n: int = 12) -> list[dict]:
    """相关推荐。豆瓣给 card_subtitle（"2026 / 中国大陆 / 剧情 / 导演 / 演员"）。"""
    data = _json("https://m.douban.com/rexxar/api/v2/%s/%s/recommendations"
                 "?start=0&count=%d" % (kind, sid, n))
    out = []
    for it in (data or []):
        rid = str(it.get("id") or "")
        if not rid:
            continue
        sub = str(it.get("card_subtitle") or "")
        parts = [p.strip() for p in sub.split("/") if p.strip()]
        year = parts[0][:4] if parts and re.match(r"^\d{4}", parts[0]) else ""
        pic = ((it.get("pic") or {}).get("large")
               or (it.get("pic") or {}).get("normal") or "")
        out.append({
            "vid": "db" + rid,
            "name": it.get("title") or "",
            "pic": relpic(pic),
            "year": year,
            "remark": (parts[1] if len(parts) > 1 else ""),
        })
    return out


# ---------------------------------------------------------------------------
# 搜索（给「按片名找豆瓣条目」用；目前详情页跳转靠 vod_id，暂不依赖）
# ---------------------------------------------------------------------------

def search(kw: str, n: int = 10) -> list[dict]:
    kw = str(kw or "").strip()
    if not kw:
        return []
    ck = "s:" + kw
    hit = _cache_get(ck)
    if hit is not None:
        return hit
    url = ("https://movie.douban.com/j/subject_suggest?q=%s"
           % urllib.parse.quote(kw))
    data = _json(url) or []
    out = []
    for it in data[:n]:
        if it.get("type") not in ("movie", "tv"):
            continue
        out.append({
            "vod_id": "db" + str(it.get("id") or ""),
            "vod_name": it.get("title") or "",
            "vod_pic": it.get("img") or "",
            "vod_year": str(it.get("year") or ""),
            "vod_remarks": str(it.get("year") or ""),
        })
    if out:
        _cache_set(ck, out, 3600)
    return out
