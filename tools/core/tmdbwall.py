# -*- coding: utf-8 -*-
"""TMDB 海报墙 —— 中枢自建的苹果CMS V10 兼容数据源

为什么要有这个模块
------------------
电视端点豆瓣影片时，app 用自己的 WebView 直连 `movie.douban.com/subject/<id>/`：
URL、桌面 UA、注入的 CSS（`#wrapper>*:not(#content){display:none}` +
`html,body{background:#fff!important}`）全部硬编码在 `pg.jar` 的
`DoubanSubjectWebView` 里。jar 受 manifest 哈希红线保护，一个字节都不能改
——所以那一页的白色底**在中枢侧无解**。

替代方案就是在 app 原生渲染的体系里另起一个站点：
  * 海报墙由 app 自己画（原生深色，不是网页）
  * 元数据走 TMDB（海报/背景/评分/演员/简介，中文）
  * 播放源实时聚合三个公共苹果CMS采集站（暴风/量子/非凡）

对外只有一个路由：`/hai/tmdb/api.php/provide/vod/`，格式与标准苹果CMS V10 一致。

字段对照（苹果CMS V10 → app 原生渲染）
--------------------------------------
  vod_name    片名            vod_pic     海报（w500 竖版）
  vod_remarks 角标（⭐评分）   vod_content 简介（导演/主演拼在后面）
  vod_year/area/lang/actor/director → 详情页信息栏
  vod_play_from + vod_play_url → 播放源分组与剧集列表

设计约束
--------
* **不许拖慢首屏**：列表页只打 TMDB 一个接口，超时 8s，失败返回空列表；
* **详情页允许慢一点**：播放源三站并发搜，单站 6s，谁先回来先用谁；
* **缓存必须有**：TMDB 列表 30 分钟、详情 12 小时、搜索 2 小时（内存级）。
"""
from __future__ import annotations

import json
import os
import threading
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

TMDB_KEY = "2894d9a1baf7812b451de03c801b0281"   # 包内 lib 同款（光鸭社区在用）
TMDB_API = "https://api.tmdb.org/3"
TMDB_IMG = "https://images.tmdb.org/t/p"
TMDB_LANG = "zh-CN"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36")

# 分类：(type_id, 名称, TMDB 路径, 额外参数)
CLASSES = [
    (1, "热门电影", "/movie/popular", {}),
    (2, "热门剧集", "/tv/popular", {}),
    (3, "高分电影", "/movie/top_rated", {}),
    (4, "正在上映", "/movie/now_playing", {}),
    (5, "高分剧集", "/tv/top_rated", {}),
    (6, "华语剧集", "/discover/tv", {"with_original_language": "zh",
                                     "sort_by": "popularity.desc",
                                     "vote_count.gte": "20"}),
    (7, "日本动画", "/discover/tv", {"with_genres": "16",
                                     "with_original_language": "ja",
                                     "sort_by": "popularity.desc"}),
    (8, "即将上映", "/movie/upcoming", {}),
]
_CLASS_BY_ID = {c[0]: c for c in CLASSES}

# 采集站（包里已有这三个 type=1 站点，直接复用，不额外引入新源）
#
# 2026-10-28 调整：电视实测采集站播放**很卡**（边转码边播，4K 盒子扛不住），
# 用户明确要求改用网盘源 ⇒ 采集站降级为**兜底**：只有当三个网盘站都拿不到资源
# 时才用它们，保证「有片可看」而不是「一片空白」。设 OKTV_PLAY_PULL=0 可彻底关闭。
PULL_SITES = [
    ("暴风·采集", "https://bfzyapi.com/api.php/provide/vod/"),
    ("量子·采集", "http://cj.lziapi.com/api.php/provide/vod/"),
    ("非凡·采集", "http://cj.ffzyapi.com/api.php/provide/vod/"),
]

PULL_MODE = os.environ.get("OKTV_PLAY_PULL", "auto").strip().lower()   # auto|1|0

_TTL_LIST = 30 * 60
_TTL_DETAIL = 12 * 3600
_TTL_SEARCH = 2 * 3600

# TMDB 的 production_countries / spoken_languages 在 zh-CN 下仍是英文名，
# 详情页信息栏直接显示英文很掉价，这里做常用映射（查不到就保留原名）。
_AREA_CN = {
    "United States of America": "美国", "China": "中国", "Japan": "日本",
    "South Korea": "韩国", "United Kingdom": "英国", "France": "法国",
    "Germany": "德国", "India": "印度", "Thailand": "泰国",
    "Hong Kong": "中国香港", "Taiwan": "中国台湾", "Canada": "加拿大",
    "Australia": "澳大利亚", "Spain": "西班牙", "Italy": "意大利",
    "Russia": "俄罗斯", "Brazil": "巴西", "Mexico": "墨西哥",
    "Singapore": "新加坡", "Malaysia": "马来西亚", "New Zealand": "新西兰",
}
_LANG_CN = {
    "English": "英语", "Mandarin": "汉语", "Chinese": "汉语", "Cantonese": "粤语",
    "Japanese": "日语", "Korean": "韩语", "French": "法语", "Spanish": "西班牙语",
    "German": "德语", "Russian": "俄语", "Thai": "泰语", "Hindi": "印地语",
    "Italian": "意大利语", "Portuguese": "葡萄牙语",
}


def _cn(table: dict, name: str) -> str:
    return table.get(name, name)

_cache: dict[str, tuple[float, object]] = {}
_lock = threading.Lock()


# ---------------------------------------------------------------------------
# 基础
# ---------------------------------------------------------------------------

def _http_json(url: str, timeout: float = 8.0):
    """GET 一个 JSON；任何失败都返回 None（这个模块不允许把异常抛给 app）"""
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": UA,
            "Accept": "application/json",
        })
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except Exception:
        return None


def _cache_get(key: str):
    with _lock:
        hit = _cache.get(key)
        if not hit:
            return None
        exp, val = hit
        if exp < time.time():
            _cache.pop(key, None)
            return None
        return val


def _cache_set(key: str, val, ttl: int):
    with _lock:
        # 简单的容量控制：超过 800 条清掉过期的
        if len(_cache) > 800:
            now = time.time()
            for k in [k for k, (e, _) in _cache.items() if e < now]:
                _cache.pop(k, None)
        _cache[key] = (time.time() + ttl, val)


def _tmdb(path: str, **params):
    params.setdefault("api_key", TMDB_KEY)
    params.setdefault("language", TMDB_LANG)
    qs = urllib.parse.urlencode(params, safe=":,")
    return _http_json(f"{TMDB_API}{path}?{qs}")


def _img(path: str, size: str = "w500") -> str:
    """海报地址 —— 这里给 TMDB 原始地址。

    改写中枢代理地址由调用方做（app.py 按请求 Host 改、posterweb 改相对路径），
    因为只有那一层知道端是从局域网还是公网进来的；写死在这里两端必有一端图裂。
    """
    if not path:
        return ""
    return f"{TMDB_IMG}/{size}{path}"


def _year_of(it: dict) -> str:
    d = str(it.get("release_date") or it.get("first_air_date") or "")
    return d[:4] if len(d) >= 4 else ""


def _name_of(it: dict) -> str:
    return str(it.get("title") or it.get("name")
               or it.get("original_title") or it.get("original_name") or "").strip()


def _remark_of(it: dict) -> str:
    """角标：有评分给评分，没有给年份 —— 海报墙上一眼能看出来源质量"""
    v = it.get("vote_average") or 0
    if v:
        return f"⭐{float(v):.1f}"
    return _year_of(it)


def _vid(it: dict) -> str:
    kind = "t" if (it.get("name") and not it.get("title")) else "m"
    return f"{kind}{it.get('id')}"


def _parse_vid(vid: str):
    """'m550' → ('movie', 550)"""
    s = str(vid or "").strip()
    if len(s) < 2 or s[0] not in "mt":
        return None, None
    try:
        return ("movie" if s[0] == "m" else "tv"), int(s[1:])
    except ValueError:
        return None, None


# ---------------------------------------------------------------------------
# 列表
# ---------------------------------------------------------------------------

def _list_item(it: dict, type_id: int, type_name: str) -> dict:
    return {
        "vod_id": _vid(it),
        "vod_name": _name_of(it),
        # 列表用 w342：海报墙一屏 20 张，尺寸减半后代理拉取快一倍，
        # 电视上肉眼也看不出差别（详情页才用 w780/w1280）
        "vod_pic": _img(it.get("poster_path"), "w342"),
        "vod_remarks": _remark_of(it),
        "vod_year": _year_of(it),
        "type_id": type_id,
        "type_name": type_name,
    }


def list_page(type_id: int, page: int = 1) -> dict:
    cls = _CLASS_BY_ID.get(int(type_id or 1)) or CLASSES[0]
    tid, tname, path, extra = cls
    page = max(1, int(page or 1))

    ck = f"list:{tid}:{page}"
    hit = _cache_get(ck)
    if hit is not None:
        return hit

    params = dict(extra)
    params["page"] = page
    data = _tmdb(path, **params) or {}
    items = [_list_item(it, tid, tname)
             for it in (data.get("results") or []) if it.get("id")]
    total = int(data.get("total_results") or 0)
    out = {
        "page": page,
        "pagecount": min(500, max(1, int(data.get("total_pages") or 1))),
        "limit": 20,
        "total": total or len(items),
        "list": items,
    }
    _cache_set(ck, out, _TTL_LIST)
    return out


def search(kw: str, page: int = 1) -> dict:
    kw = str(kw or "").strip()
    if not kw:
        return {"page": 1, "pagecount": 0, "limit": 20, "total": 0, "list": []}

    ck = f"search:{kw}:{page}"
    hit = _cache_get(ck)
    if hit is not None:
        return hit

    data = _tmdb("/search/multi", query=kw, page=max(1, int(page or 1)),
                 include_adult="false") or {}
    items = []
    for it in (data.get("results") or []):
        if it.get("media_type") not in ("movie", "tv") or not it.get("id"):
            continue
        tname = "电影" if it["media_type"] == "movie" else "剧集"
        items.append(_list_item(it, 0, tname))
    out = {
        "page": max(1, int(page or 1)),
        "pagecount": min(20, max(1, int(data.get("total_pages") or 1))),
        "limit": 20,
        "total": int(data.get("total_results") or len(items)),
        "list": items,
    }
    _cache_set(ck, out, _TTL_SEARCH)
    return out


# ---------------------------------------------------------------------------
# 播放源聚合
# ---------------------------------------------------------------------------

def _pick_match(items: list, title: str, year: str):
    """从采集站结果里挑最像的那条：同名优先、年份一致优先、标题最长优先"""
    if not items:
        return None
    title = (title or "").strip()

    def score(it):
        name = str(it.get("vod_name") or "").strip()
        s = 0
        if name == title:
            s += 100
        elif title and title in name:
            s += 60
        elif name and name in title:
            s += 40
        if year and str(it.get("vod_year") or "") == year:
            s += 30
        s += len(name) * 0.01
        return s

    best = max(items, key=score)
    # 太不像就不要（避免"肖申克"一个搜索命中无关片）
    name = str(best.get("vod_name") or "")
    if title and title not in name and name not in title:
        return None
    return best


def _search_one(base: str, title: str, year: str, timeout: float = 6.0):
    url = f"{base}?ac=detail&wd={urllib.parse.quote(title)}"
    data = _http_json(url, timeout=timeout)
    if not isinstance(data, dict):
        return None
    return _pick_match(data.get("list") or [], title, year)


def collect_play(title: str, year: str):
    """并发搜三个采集站 → [(源名, play_url串)]"""
    ck = f"play:{title}:{year}"
    hit = _cache_get(ck)
    if hit is not None:
        return hit

    out = []
    if title:
        with ThreadPoolExecutor(max_workers=3) as ex:
            futs = {ex.submit(_search_one, base, title, year): name
                    for name, base in PULL_SITES}
            try:
                for fut in as_completed(futs, timeout=9):
                    name = futs[fut]
                    try:
                        it = fut.result()
                    except Exception:
                        continue
                    if not it:
                        continue
                    play_url = str(it.get("vod_play_url") or "")
                    groups = play_url.split("$$$")
                    # 只取第一组（多数站就一个播放源），保证详情页不刷屏
                    if groups and groups[0]:
                        out.append((name, groups[0]))
            except Exception:
                # 某个采集站慢到超时：手上已经有几个源就先用几个，
                # 绝不能让「一个源卡住」把整个详情页打空
                pass
    # 顺序固定成 暴风/量子/非凡
    order = {n: i for i, (n, _) in enumerate(PULL_SITES)}
    out.sort(key=lambda x: order.get(x[0].split("·")[0], 99))
    _cache_set(ck, out, _TTL_DETAIL)
    return out


# ---------------------------------------------------------------------------
# 详情
# ---------------------------------------------------------------------------

def _with_push(play: str) -> str:
    """给「第1集$https://pan.xxx/s/yyy」这种分集串里的**地址部分**加 push://

    panwall 交出来的已经是 `名称$地址`（多集用 # 分隔）格式，不能直接
    在整串前面拼前缀 —— 那样会变成 `push://第1集$https://...`。
    只处理 http(s) 开头的地址；采集站兜底的 m3u8 是真视频，不要进来。
    """
    out = []
    for seg in str(play or "").split("#"):
        if "$" in seg:
            name, url = seg.split("$", 1)
            if url.startswith("http"):
                url = "push://" + url
            out.append(name + "$" + url)
        elif seg.startswith("http"):
            out.append("push://" + seg)
        else:
            out.append(seg)
    return "#".join(out)


def _plays(title: str, year: str, push: bool = False):
    """播放源：**网盘优先**（花卷/玩偶/木偶），一个都拿不到才兜底采集站。

    电视上采集站又慢又卡，所以只在前者全灭时启用，避免「干脆啥都没有」。

    线路名统一成 app 原生站那种短名（`夸克` / `阿里` / `UC`）：
    panwall 返回的是 `花卷·夸克` 这种带站点的名字，直接塞给 app 会变成一长排
    「花卷·夸克·网盘」「玩偶·夸克·网盘」，电视上一行放不下、也看不出区别。
    同一网盘最多留两条（不同站给的常是不同分享，互为备份），第二条加后缀 2。

    `push=True` 时给网盘分享链接加 `push://` 前缀 —— 2026-10-29 定位到：
    app 的 dex 里没有一处 quark/pan.quark.cn 字样，网盘转存全在 pg.jar 的
    spider 内部。type=1 站点把夸克链接当普通视频喂播放器必然失败，只有
    `push://` 这种自定义 scheme 才会被 app 拦截后交给网盘模块。
    采集站兜底的 m3u8 是真正的视频地址，**不能**加前缀。
    """
    pan = []
    try:
        from . import panwall
        pan = panwall.collect(title, year)
    except Exception:
        pan = []
    if pan:
        out, seen = [], {}
        for n, u in pan:
            pname = n.split("·")[1] if "·" in n else n
            seen[pname] = seen.get(pname, 0) + 1
            if seen[pname] > 2:
                continue
            out.append((pname if seen[pname] == 1 else f"{pname}{seen[pname]}",
                        _with_push(u) if push else u))
        return out
    if PULL_MODE == "0":
        return []
    return collect_play(title, year)


def _as_int(v, default: int = 1) -> int:
    try:
        return int(str(v).strip() or default)
    except (TypeError, ValueError):
        return default

def detail(vid: str, with_plays: bool = True, push: bool = False) -> dict:
    """站点详情。

    `with_plays=False` 用于只要元数据的场景（海报墙 hero、网页侧栏）——
    省掉三站聚合的那 5 秒，列表页才不会被拖慢。

    `push=True` 只给 app 接口用（网盘链接要 push:// 前缀），网页端保持原样。
    """
    kind, tid = _parse_vid(vid)
    if not kind:
        return {}

    # 只缓存 TMDB 元数据（海报/简介/演职员），**播放源不进这个缓存**。
    # 原因：panwall 是「木偶先回、花卷/玩偶后台补齐」，如果连播放源一起缓存
    # 12 小时，第一次算出来的「只有木偶」就被钉死了 —— 后台补到 5 组也永远
    # 不会出现在详情页。播放源交给 panwall 自己的缓存，每次读最新。
    mkey = f"meta:{kind}:{tid}"
    item = _cache_get(mkey)
    if item is None:
        item = _build_meta(vid, kind, tid)
        if not item:
            return {}
        _cache_set(mkey, item, _TTL_DETAIL)

    if not with_plays:
        return item

    out = dict(item)
    plays = _plays(item["vod_name"], item["vod_year"], push=push)
    out["vod_play_from"] = "$$$".join(n for n, _ in plays)
    out["vod_play_url"] = "$$$".join(u for _, u in plays)
    return out


def _build_meta(vid: str, kind: str, tid: str) -> dict:
    """纯 TMDB 元数据（不含播放源），可安全长缓存"""
    d = _tmdb(f"/{kind}/{tid}", append_to_response="credits") or {}
    if not d:
        return {}

    name = _name_of(d)
    year = _year_of(d)
    overview = str(d.get("overview") or "").strip()

    # 演员 / 导演（TMDB 是英文名，中文名看 zh-CN 有没有）
    cast = (d.get("credits") or {}).get("cast") or []
    crew = (d.get("credits") or {}).get("crew") or []
    actors = [str(c.get("name") or "") for c in cast[:6] if c.get("name")]
    directors = [str(c.get("name") or "") for c in crew
                 if c.get("job") == "Director"][:2]

    countries = " / ".join(_cn(_AREA_CN, x.get("name", ""))
                           for x in (d.get("production_countries") or [])[:3])
    langs = " / ".join(_cn(_LANG_CN, x.get("name", ""))
                       for x in (d.get("spoken_languages") or [])[:2])
    genres = " / ".join(x.get("name", "") for x in (d.get("genres") or [])[:4])

    content = overview or "（TMDB 暂无中文简介）"
    extra = []
    if genres:
        extra.append(f"类型：{genres}")
    if directors:
        extra.append("导演：" + "、".join(directors))
    if actors:
        extra.append("主演：" + "、".join(actors))
    votes = d.get("vote_average")
    if votes:
        extra.append(f"TMDB 评分：{float(votes):.1f}（{int(d.get('vote_count') or 0)} 人）")
    if extra:
        content = content + "\n\n" + "\n".join(extra)

    item = {        "vod_id": vid,
        "vod_name": name,
        "vod_pic": _img(d.get("poster_path"), "w780"),
        "vod_pic_slide": _img(d.get("backdrop_path"), "w1280"),
        "vod_year": year,
        "vod_area": countries,
        "vod_lang": langs,
        "vod_actor": "、".join(actors),
        "vod_director": "、".join(directors),
        "vod_content": content,
        "vod_remarks": _remark_of(d),
        "vod_pubdate": str(d.get("release_date") or d.get("first_air_date") or ""),
        "type_name": genres or ("电影" if kind == "movie" else "剧集"),
        "vod_play_from": "",
        "vod_play_url": "",
    }
    return item


# ---------------------------------------------------------------------------
# 路由入口
# ---------------------------------------------------------------------------

def _classes():
    return [{"type_id": t, "type_pid": 0, "type_name": n}
            for t, n, _, _ in CLASSES]


def handle(args: dict, push: bool = False) -> dict:
    """苹果CMS V10：ac=list / ac=videolist / ac=detail

    `push=True` 由 app 接口路由传入（网盘链接加 push://），网页端不传。
    """
    ac = str(args.get("ac") or "").lower()
    page = args.get("pg") or args.get("page") or 1

    body = {
        "code": 1,
        "msg": "数据列表",
        "class": _classes(),
    }

    # 详情：?ac=detail&ids=m550,t1399
    #
    # 注意：设备实测会发 `?t=1&ac=detail&pg=1` —— 苹果 CMS 里 ac=detail 带 t 就是
    # **分类浏览**（不是查详情）。早先只按 ids 解析，导致除「推荐」外全部分类空列表。
    if ac == "detail":
        raw_ids = str(args.get("ids") or args.get("id") or "").strip()
        if raw_ids:
            ids = [x.strip() for x in raw_ids.split(",") if x.strip()]
            items = [x for x in (detail(i, push=push) for i in ids[:20]) if x]
            body.update({"page": 1, "pagecount": 1, "limit": 20,
                         "total": len(items), "list": items})
            return body
        t = _as_int(args.get("t") or args.get("type_id"), 1)
        body.update(list_page(t, page))
        return body

    # 搜索：?wd=关键词
    wd = args.get("wd") or args.get("keyword") or args.get("search")
    if wd:
        body.update(search(wd, page))
        return body

    # 列表：?ac=videolist&t=1&pg=1  /  ?ac=list  /  ?（首页推荐）
    t = _as_int(args.get("t") or args.get("type_id"), 1)
    body.update(list_page(t, page))
    return body
