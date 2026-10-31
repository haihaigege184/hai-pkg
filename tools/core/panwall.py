# -*- coding: utf-8 -*-
"""网盘源聚合 —— 从「花卷 / 玩偶 / 木偶」三个网盘站抓夸克·阿里·UC 分享链接

为什么单独一个模块
------------------
`tmdbwall` 早先用暴风/量子/非凡三个采集站做播放源，实测在电视上**又慢又卡**
（采集站边转码边播，4K 盒子扛不住）。用户要求改用网盘源。

关键技术判断（反编译得出，决定这条路走不走得通）
------------------------------------------------
`pg.jar` 里的 `Hjzc` / `Wogg` / `Mogg` 三个网盘蜘蛛**只负责提取分享链接**，
里面没有任何「转存 / 取直链」逻辑；`Ali` / `Quark` / `AliShare` / `QuarkShare`
是独立的网盘模块 —— 直链解析在 app 播放器层按 URL 特征统一分发。
所以只要把 `https://pan.quark.cn/s/xxx` 这类链接放进 `vod_play_url`，
app 就会用自己那套网盘模块（`lib/tokenm.json` 里的登录态）去转存播放。

三个站的结构（2026-10-28 实测）
------------------------------
* 花卷 `https://www.hjzhencai.top`  苹果CMS + mxtheme
  搜索 `/index.php/vod/search.html?wd=`，详情 `/index.php/vod/detail/id/<id>.html`
* 木偶 `http://123.666291.xyz`  同一套模板，路径完全一致
* 玩偶 `https://www.wogg.live`
  搜索 `/vodsearch/-------------.html?wd=`，详情 `/voddetail/<id>.html`

**踩过的坑：花卷每部片都挂同一条推广链接**
`pan.quark.cn/s/c6ca20520dad` 在肖申克/流浪地球2/捉妖记页面上都出现（固定 5 次），
真资源是另一条。不做处理就会「点谁都播成同一部片」。这里用两重过滤：
  1. 已知推广 ID 直接拉黑；
  2. 运行时学习 —— 同一个链接出现在两个不同片名下，判定为全站链接并永久拉黑。

礼貌与性能
----------
花卷首页写着「纯自用 切勿爬取 谢谢」，所以：
  * 每个 (片名, 年份) 缓存 12 小时，同一部片一天最多抓一次；
  * 单站最多试 2 个候选详情页，命中即停；
  * 单请求 3.5 秒超时，全程 7 秒硬预算，超时不再等慢站（线程池不 wait）。
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36")

SITES = [
    {
        "name": "花卷",
        "bases": ["https://www.hjzhencai.top"],
        "search": "/index.php/vod/search.html?wd={q}",
        "id_re": r"/index\.php/vod/detail/id/(\d+)\.html",
        "detail": "/index.php/vod/detail/id/{}.html",
    },
    {
        "name": "木偶",
        "bases": ["http://123.666291.xyz"],
        "search": "/index.php/vod/search.html?wd={q}",
        "id_re": r"/index\.php/vod/detail/id/(\d+)\.html",
        "detail": "/index.php/vod/detail/id/{}.html",
    },
    {
        "name": "玩偶",
        # 中枢巡检把玩偶主域改写成了 wogg.net，两个都活着，按顺序试
        "bases": ["https://www.wogg.net", "https://www.wogg.live"],
        "search": "/vodsearch/-------------.html?wd={q}",
        "id_re": r"/voddetail/(\d+)\.html",
        "detail": "/voddetail/{}.html",
    },
]

# 网盘商：链接特征 → (显示名, 优先级)，小的优先（夸克在电视上最稳）
PROVIDERS = [
    ("pan.quark.cn", "夸克", 0),
    ("alipan.com", "阿里", 1),
    ("aliyundrive.com", "阿里", 1),
    ("drive.uc.cn", "UC", 2),
    ("123pan.com", "123", 3),
    ("mypikpak.com", "PikPak", 4),
    ("guangyapan.com", "光雅", 4),
    ("yun.139.com", "移动", 5),
    ("cloud.189.cn", "天翼", 5),
    ("pan.baidu.com", "百度", 6),
    ("115.com", "115", 7),
    ("115cdn.com", "115", 7),
]
# 组名形如「木偶·夸克·网盘」，中间那段就是网盘名 —— 用它决定排序权重
_PNAME_RANK = {name: rank for _, name, rank in PROVIDERS}


def _sorted(out):
    """按「分集数多 → 网盘商权重」重排。

    第一优先是**分集数**：2026-10-29 实拍踩到过 —— 某站给「凡人修仙传」挂的
    夸克分享链接其实是另一部片（FRXX-2026），而且只有孤零零一条「第1集」。
    按网盘商排序会把它排第一，用户点播放必然「播放地址加载失败」。
    分集完整的那组（10 集）反而是对的。所以单集的一律往后放；
    电影本来就只有 1 集，不受影响。

    第二优先才是网盘商（电视上夸克明显更稳）。
    """
    def key(pair):
        parts = pair[0].split("·")
        pname = parts[1] if len(parts) >= 2 else parts[-1]
        eps = len([x for x in str(pair[1]).split("#") if x])
        return (-min(eps, 99), _PNAME_RANK.get(pname, 8))
    return sorted(out, key=key)

_PAN_RE = re.compile(
    r"https?://(?:[a-z0-9-]+\.)*(?:pan\.quark\.cn|alipan\.com|aliyundrive\.com|"
    r"drive\.uc\.cn|123pan\.com|mypikpak\.com|guangyapan\.com|yun\.139\.com|"
    r"cloud\.189\.cn|pan\.baidu\.com|115\.com|115cdn\.com)/[^\s\"'<>\\]+",
    re.I,
)
_H1_RE = re.compile(r"<h1[^>]*>\s*([^<]{1,60})", re.I)
_EP_RE = re.compile(r"第\s*(\d{1,4})\s*[集话回]")

# 已知全站推广链接（花卷每页都挂，真资源在别处）
_GLOBAL_URLS = {"c6ca20520dad"}
# 运行时学习：url → 见过的片名集合，出现两个以上不同片名即判为全站链接
_url_titles: dict[str, set] = {}

_TTL = 12 * 3600
_cache: dict[str, tuple[float, object]] = {}
_lock = threading.Lock()


# ---------------------------------------------------------------------------
# 基础
# ---------------------------------------------------------------------------

def _get(url: str, timeout: float = 3.5) -> str:
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": UA,
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "zh-CN,zh;q=0.9",
        })
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read(900000)
        for enc in ("utf-8", "gbk"):
            try:
                return raw.decode(enc)
            except UnicodeDecodeError:
                continue
        return raw.decode("utf-8", "replace")
    except Exception:
        return ""


def _norm(s: str) -> str:
    return re.sub(r"[\s·:：\-—_.,，。!！?？()（）\[\]【】/]+", "", str(s or "")).lower()


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


def peek(title: str, year: str = "") -> list[tuple[str, str]]:
    """只读缓存，**绝不发请求**。

    collect() 第一次要 1~5 秒（花卷站慢），详情页渲染拿它当数据源就会卡 3 秒。
    这里给网页用：缓存里有就显示线路名，没有就返回空，让页面先渲染出来 ——
    真正的搜索交给「点播放」那一刻，app 那边本来就要等。
    """
    title = str(title or "").strip()
    if not title:
        return []
    return _cache_get(f"pan:{title}:{year}") or []


def _cache_set(key: str, val, ttl: float = None):
    with _lock:
        if len(_cache) > 600:
            now = time.time()
            for k in [k for k, (e, _) in _cache.items() if e < now]:
                _cache.pop(k, None)
        _cache[key] = (time.time() + (ttl or _TTL), val)


def _share_id(url: str) -> str:
    """分享链接的短 id（/s/xxxx 的最后一段），用于去重与推广判定"""
    m = re.search(r"/(?:s|web/share)/([A-Za-z0-9_-]+)", url)
    if m:
        return m.group(1)
    return url[-24:]


# 网盘分享链接有效性缓存：sid → (是否可用, 过期时间)
_LINK_TTL = 6 * 3600
_link_cache: dict[str, tuple[bool, float]] = {}
_LINK_API = {
    "quark": "https://drive-pc.quark.cn/1/clouddrive/share/sharepage/token?pr=ucpro&fr=pc",
    "uc": "https://pc-api.uc.cn/1/clouddrive/share/sharepage/token?pr=UCBrowser&fr=pc",
}


def _share_probe(url: str):
    """探测分享链接：返回 (是否可用, 分享标题)。

    标题是 2026-10-30 新增的硬需求 —— FRXX-2026 那条毒分享的标题叫「F 凡r」，
    故意蹭一个「凡」字混进「凡人修仙传」的线路里，光看活性校验拦不住。
    token 接口响应里本来就带 data.title，顺手续命，零额外请求。
    """
    low = str(url or "").lower()
    if "pan.quark.cn/s/" in low:
        kind = "quark"
    elif "drive.uc.cn/s/" in low:
        kind = "uc"
    else:
        return None, ""                   # 阿里/天翼/115 暂不校验，按可用处理

    sid = _share_id(url)
    now = time.time()
    with _lock:
        hit = _link_cache.get(sid)
        if hit and hit[1] > now:
            val = hit[0]
            if isinstance(val, tuple):
                return val
            return val, ""                # 旧缓存只有 bool，标题按未知处理

    body = json.dumps({"pwd_id": sid, "passcode": ""}).encode()
    ok, st = True, ""
    try:
        req = urllib.request.Request(_LINK_API[kind], data=body, headers={
            "Content-Type": "application/json", "User-Agent": UA})
        with urllib.request.urlopen(req, timeout=8) as r:
            d = json.loads(r.read().decode("utf-8", "replace"))
        # 夸克/UC 成功返回 status=200（也有 0），失败是 404/403/41011 之类
        ok = d.get("status") in (0, 200) and bool(d.get("data"))
        st = str(((d.get("data") or {}).get("title")) or "")
    except urllib.error.HTTPError:
        ok = False                        # 404 失效 / 403 分享者被封
    except Exception:
        return None, ""                   # 网络抖动：别误杀，交给播放器试

    with _lock:
        if len(_link_cache) > 800:
            for k in [k for k, (_, e) in _link_cache.items() if e < now]:
                _link_cache.pop(k, None)
        _link_cache[sid] = ((bool(ok), st), now + _LINK_TTL)
    return bool(ok), st


def _share_ok(url: str):
    """夸克/UC 分享链接还能不能打开。

    2026-10-29 实测出来的硬需求：**木偶站给的夸克链接全是死的**
    （`404 分享地址已失效` 或 `403 分享者用户封禁链接查看受限`），
    而花卷/玩偶的是活的。原先按「木偶最快」把它排第一条，
    用户点下去必然播放失败 —— 这就是「点了直接播还播失败」的真因。

    返回 True 可用 / False 已死 / None 不校验（其他网盘或不认识的形态）。
    """
    ok, _ = _share_probe(url)
    return ok


def _share_title_hit(st: str, want: str) -> bool:
    """分享标题与目标片名是否对得上（≥2 字公共连续子串即算命中）。

    「F 凡r」×「凡人修仙传」只有单字「凡」交集 → 挡掉；
    「凡人修仙传4K合集」「凡修年番」都能正常通过。
    拿不到标题（其他网盘/请求失败）一律放行，不误杀。
    """
    a, b = _norm(st), _norm(want)
    if not a or not b:
        return True
    if a in b or b in a:
        return True
    return any(a[i:i + 2] in b for i in range(max(0, len(a) - 1)))


def _provider_of(url: str) -> tuple[str, int]:
    low = url.lower()
    for key, name, rank in PROVIDERS:
        if key in low:
            return name, rank
    return "网盘", 9


def _is_global(url: str) -> bool:
    sid = _share_id(url)
    if sid in _GLOBAL_URLS:
        return True
    with _lock:
        return len(_url_titles.get(sid) or ()) >= 2


def _learn(title: str, urls: list[str]) -> None:
    """记录链接出现过的片名；跨两部不同片即拉黑"""
    t = _norm(title)
    if not t:
        return
    with _lock:
        for u in urls:
            sid = _share_id(u)
            _url_titles.setdefault(sid, set()).add(t)


# ---------------------------------------------------------------------------
# 单站解析
# ---------------------------------------------------------------------------

def _candidate_ids(html: str, site: dict, limit: int = 2) -> list[str]:
    seen, out = set(), []
    for m in re.finditer(site["id_re"], html):
        i = m.group(1)
        if i in seen:
            continue
        seen.add(i)
        out.append(i)
        if len(out) >= limit:
            break
    return out


def _title_of(html: str) -> str:
    m = _H1_RE.search(html)
    return (m.group(1) or "").strip() if m else ""


def _groups(html: str, want: str):
    """按网盘商分组 → [(网盘商, [(剧集名, 链接)])]

    页面里链接会重复出现 4~5 次（卡片、复制按钮、下载按钮各一处），
    按 (网盘商, 剧集) 去重后每个商只剩一条干净清单。
    """
    buckets: dict[str, dict[str, str]] = {}
    ranks: dict[str, int] = {}
    for m in _PAN_RE.finditer(html):
        url = m.group(0).rstrip(".,;)]}\"'")
        if _is_global(url):
            continue
        name, rank = _provider_of(url)
        # 剧集名往回找 400 字符（mxtheme 的「第N集」总在链接前面）
        head = html[max(0, m.start() - 400):m.start()]
        eps = _EP_RE.findall(head)
        label = f"第{int(eps[-1])}集" if eps else ""
        bucket = buckets.setdefault(name, {})
        ranks[name] = rank
        if not label:
            label = f"第{len(bucket) + 1}集"
        bucket.setdefault(label, url)      # 同一集只留第一个网盘商链接

    out = []
    for n, b in buckets.items():
        items = list(b.items())
        # 有些站整季就一条分享链接（每个剧集都指同一个 url）——收成一条「全集」，
        # 否则详情页会出现 5 个一模一样的线路名
        if len({u for _, u in items}) == 1 and len(items) > 1:
            items = [("全集", items[0][1])]
        if items:
            out.append((n, items))
    out.sort(key=lambda x: (-len(x[1]), ranks.get(x[0], 9)))
    return out, [u for b in buckets.values() for u in b.values()]


def _alive(pairs: list[tuple[str, str]], budget: float = 2.5, want: str = ""):
    """把死链/错片的组剔掉。校验不出来（网络抖动）的**保留**，宁可让播放器试一次。

    并发校验、总预算 2.5 秒 —— 详情页本来就 2 秒左右，加太多就变卡。
    want 非空时顺带做片名匹配：分享标题与目标片名对不上的整组剔除
    （2026-10-30：FRXX-2026 蹭「凡」字混进凡人修仙传线路，播放必出错片）。
    """
    if not pairs:
        return pairs
    res: list = [None] * len(pairs)

    def work(i: int, pair: tuple[str, str]) -> None:
        first = pair[1].split("$", 1)[-1].split("#")[0]
        ok, st = _share_probe(first)
        if ok is False:
            res[i] = False
            return
        if want and st and not _share_title_hit(st, want):
            res[i] = False
            return
        res[i] = True

    ths = []
    for i, p in enumerate(pairs):
        t = threading.Thread(target=work, args=(i, p), daemon=True)
        t.start()
        ths.append(t)
    end = time.time() + budget
    for t in ths:
        t.join(max(0.02, end - time.time()))
    return [p for i, p in enumerate(pairs) if res[i] is not False]


def _one_site(site: dict, title: str, deadline: float):
    """单站：搜索 → 最多两个候选详情 → 命中返回 [(组名, '第1集$url#...')]"""
    if time.time() > deadline:
        return None
    q = urllib.parse.quote(title)
    page, base = "", ""
    for b in site["bases"]:
        page = _get(b + site["search"].format(q=q))
        if page:
            base = b
            break
    if not page:
        return None
    nt = _norm(title)
    all_urls: list[str] = []
    for cid in _candidate_ids(page, site):
        if time.time() > deadline:
            break
        html = _get(base + site["detail"].format(cid))
        if not html:
            continue
        name = _title_of(html)
        if not name:
            continue
        nn = _norm(name)
        if nt and nt not in nn and nn not in nt:
            continue                      # 片名对不上，换下一个候选
        groups, urls = _groups(html, title)
        if not groups:
            continue
        all_urls.extend(urls)
        _learn(title, urls)
        # 一个站最多两个网盘商，免得详情页线路多到翻页
        pairs = [(f"{site['name']}·{g}", "#".join(f"{lb}${u}" for lb, u in eps))
                 for g, eps in groups[:2]]
        return _alive(pairs, want=title) or None
    if all_urls:
        _learn(title, all_urls)
    return None


# ---------------------------------------------------------------------------
# 对外
# ---------------------------------------------------------------------------

def _merge(key: str, extra: list[tuple[str, str]]) -> None:
    """把后台补到的组并进缓存（同组名以新的为准）"""
    if not extra:
        return
    with _lock:
        hit = _cache.get(key)
        cur = list(hit[1]) if hit else []      # type: ignore[arg-type]
        names = {n for n, _ in extra}
        cur = [(n, u) for n, u in cur if n not in names]
        cur.extend(extra)
        cur = _sorted(cur)
        _cache[key] = (time.time() + _TTL, cur)


def _bg_fill(key: str, title: str, first_name: str) -> None:
    """后台把剩下的站跑完（串行，避免三站并发互相拖死），只更新缓存"""
    try:
        got = []
        for site in SITES:
            if f"{site['name']}·" in first_name:
                continue
            res = _one_site(site, title, time.time() + 6.0)
            if res:
                got.extend(res)
        _merge(key, got)
    except Exception:
        pass


def collect(title: str, year: str = "") -> list[tuple[str, str]]:
    """网盘播放源 → [(组名, '第1集$url#第2集$url')]

    实测结论（重要）：三站**不能并发**，同时打过去会各有请求卡到超时，
    反而是串行时 木偶 1 秒、花卷 4.5 秒都能拿到。所以这里：
      1. 先打最快的「木偶」，1~2 秒就返回，详情页立刻能看能播；
      2. 花卷/玩偶丢后台串行补，补到后写进缓存 —— 再打开这部片就是全量源。
    宁可先给 1 个能播的网盘源，也不让用户对着转圈等 5 个。
    """
    title = str(title or "").strip()
    if not title:
        return []

    key = f"pan:{title}:{year}"
    hit = _cache_get(key)
    if hit is not None:
        return hit                            # type: ignore[return-value]

    out: list[tuple[str, str]] = []
    tried: list[str] = []
    # 顺序按「链接质量」排，不按速度排：2026-10-29 实测木偶给的夸克链接
    # 全是 403 封禁/404 失效，花卷和玩偶的是活的。先拿能播的，再拿快的。
    end = time.time() + 9.0
    for name in ("花卷", "木偶", "玩偶"):
        site = next((s for s in SITES if s["name"] == name), None)
        if not site or time.time() > end:
            continue
        tried.append(name)
        res = _one_site(site, title, min(end, time.time() + 5.5))
        if res:
            out.extend(res)
            break                     # 拿到活源先用，剩下的丢后台补
    if out:
        out = _sorted(out)[:4]
        _cache_set(key, out)
        done = "|".join(f"{n}·" for n in tried)
        threading.Thread(target=_bg_fill, args=(key, title, done),
                         daemon=True).start()
        return out
    # 全站死链：不缓存，过一会儿还能再试（站方会更新资源）
    return out


# ---------------------------------------------------------------------------
# 播放直达站源：解析「站源 spider 认的 vod id」（2026-10-30 需求 / 10-31 重写）
# ---------------------------------------------------------------------------
#
# 2026-10-31 海哥验收后的硬规则（旧实现全部踩中）：
#   1. 旧实现只取搜索结果前 3 条候选、且用「子串包含」判片名 ——
#      玩偶搜「仙逆」，前两条是「仙逆剧场版 神临之战 / 弑仙之战」，
#      「仙逆」在第三条，子串匹配对剧场版也成立 ⇒ 必然跳错片。
#   2. 旧实现只问玩偶一个站，玩偶没有就直接掉搜索页。
#   所以改成：**并行搜索全部站源 → 每站只认「完全相等的片名」→
#   按站源顺序（玩偶打头）取第一个命中的站 → 全站都没有才回搜索页。**
#
# 站源清单可在 data/play_sites.json 覆盖（/live 维护页可改顺序/开关），
# 改站源不用重打包 APK。

# app 端 9 参入口要的 id 形态 = 爬虫 detailContent(id) 里的原始 id。
# 玩偶系（wogg 模板）爬虫「拿 id 拼 URL」，只认完整路径 /voddetail/<n>.html；
# 木偶/花卷（maccms 模板）爬虫吃纯数字 id，自己拼 index.php/vod/detail/id/<n>.html。
# 真机上打不开选集页就是这里形态错了 —— 纯服务端配置，改完重启即可。
PLAY_SITES_DEFAULT = [
    {"name": "玩偶", "key": "Wogg", "tpl": "wogg", "id_fmt": "path",
     "bases": ["https://www.wogg.live", "https://www.wogg.net"],
     "search": "/vodsearch/-------------.html?wd={q}",
     "id_re": r"/voddetail/(\d+)\.html", "detail": "/voddetail/{}.html"},
    {"name": "木偶", "key": "Mogg", "tpl": "maccms", "id_fmt": "path",
     "bases": ["http://123.666291.xyz"],
     "search": "/index.php/vod/search.html?wd={q}",
     "id_re": r"/index\.php/vod/detail/id/(\d+)\.html",
     "detail": "/index.php/vod/detail/id/{}.html"},
    {"name": "花卷", "key": "Hjzc", "tpl": "maccms", "id_fmt": "path",
     "bases": ["https://www.hjzhencai.top"],
     "search": "/index.php/vod/search.html?wd={q}",
     "id_re": r"/index\.php/vod/detail/id/(\d+)\.html",
     "detail": "/index.php/vod/detail/id/{}.html"},
    {"name": "蜡笔", "key": "蜡笔网盘", "tpl": "maccms", "id_fmt": "path",
     "bases": ["https://www.fmcf.fun"],
     "search": "/index.php/vod/search.html?wd={q}",
     "id_re": r"/voddetail/(\d+)\.html", "detail": "/voddetail/{}.html"},
]

_PLAY_CFG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "data", "play_sites.json")


def play_sites() -> list[dict]:
    """站源顺序：默认内置，data/play_sites.json 里按 name 覆盖/追加。

    只认 enabled != False 的项；顺序 = 列表顺序（第一个命中的站直接播）。
    """
    sites = [dict(s) for s in PLAY_SITES_DEFAULT]
    try:
        with open(_PLAY_CFG, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception:
        return sites
    over = {str(x.get("name")): x for x in (cfg.get("sites") or []) if x.get("name")}
    merged = []
    for name in (cfg.get("order") or []):
        src = over.get(name) or next((s for s in sites if s["name"] == name), None)
        if src and all(m["name"] != name for m in merged):
            merged.append(dict(src))
    for s in sites:                       # order 里没列的按默认位置补后面
        if all(m["name"] != s["name"] for m in merged):
            merged.append(dict(s))
    return [s for s in merged if over.get(s["name"], {}).get("enabled", True) is not False]


# 站源健康：连续失败就冷一段时间，别让每次点播放都等死站超时
_health: dict[str, float] = {}


def _site_ok(name: str) -> bool:
    return _health.get(name, 0) < time.time()


def _site_bad(name: str, sec: float = 1200) -> None:
    _health[name] = time.time() + sec


_CARD_RE = re.compile(r'<div class="(?:module-card-item[^"]*|video-info)">')


def _parse_cards(html: str, site: dict) -> list[dict]:
    """把搜索页解析成 [{id, name, note, kind, year, region}]（全量，不只前 3 条）

    两套模板：
      wogg   玩偶/蜡笔 —— `<h3><a href="/voddetail/1.html" title="片名">`
      maccms 木偶/花卷 —— `<img alt="片名">` + `<strong>片名</strong>` + 年份/地区/类型
    """
    out, seen = [], set()
    marks = [m.start() for m in _CARD_RE.finditer(html)]
    if not marks:
        marks = [0]
    bounds = marks + [len(html)]
    for i in range(len(marks)):
        seg = html[bounds[i]:bounds[i + 1]]
        m = re.search(site["id_re"], seg)
        if not m:
            continue
        cid = m.group(1)
        if cid in seen:
            continue
        name = ""
        for pat in (r'<h3>\s*<a[^>]*?title="([^"]+)"',
                    r'<img[^>]*?alt="([^"]+)"',
                    r'<strong[^>]*>([^<]+)</strong>',
                    r'<a[^>]*?title="([^"]+)"'):
            mm = re.search(pat, seg)
            if mm and mm.group(1).strip():
                name = mm.group(1).strip()
                break
        if not name:
            continue
        seen.add(cid)
        note = ""
        mm = (re.search(r'class="(?:module-item-note|video-serial)"[^>]*title="([^"]*)"', seg)
              or re.search(r'class="(?:module-item-note|video-serial)"[^>]*>([^<]*)<', seg))
        if mm:
            note = (mm.group(1) or "").strip()
        kind = ""
        mm = (re.search(r'class="module-card-item-class"[^>]*>([^<]*)<', seg)
              or re.search(r'class="video-info-aux".*?title="([^"]+)"', seg, re.S))
        if mm:
            kind = (mm.group(1) or "").strip()
        year, region = "", ""
        mm = re.search(r'class="module-info-item-content">\s*(\d{4})[^<]*<span', seg)
        if mm:
            year = mm.group(1)
        mm = re.search(r'(\d{4})<span class="slash">/</span>([^<]{1,12})<span', seg)
        if mm:
            year, region = mm.group(1), mm.group(2).strip()
        out.append({"id": cid, "name": name, "note": note, "kind": kind,
                    "year": year, "region": region})
    return out


# 片名归一化：去掉画质/语言/集数尾巴，只留「片名本体」做全等比较。
# 故意**不**去「第N季 / 剧场版 / 沙雕版」—— 那些是不同的片，去掉就会挂错资源。
_TITLE_JUNK = re.compile(
    r"(4k|8k|2160p|1080p|720p|hdr|hd|蓝光|bluray|高清|超清|国语|粤语|英语|双语|中字|"
    r"沪语|川语|方言|普通话|原声|无删减|未删减|导演剪辑|完整版|加长版|tv版|年番|"
    r"合集|合辑|全集|完结|更新至第?\d+集|共\d+集)", re.I)


def _title_key(s: str) -> str:
    t = str(s or "")
    t = re.sub(r"[\(（\[【][^\)）\]】]{0,20}[\)）\]】]", "", t)   # 括号内容
    t = re.sub(r"\b(19|20)\d{2}\b", "", t)                      # 年份
    t = _TITLE_JUNK.sub("", t)
    t = re.sub(r"[版]$", "", t)                                 # 末尾「…版」
    return _norm(t)


_SEASON_RE = re.compile(r"(第[一二三四五六七八九十\d]+季|season\s*\d+|第[一二三四五六七八九十\d]+部)", re.I)


def _season_no(s: str) -> int:
    m = re.search(r"第([一二三四五六七八九十\d]+)季", str(s or ""))
    if not m:
        return 0
    t = m.group(1)
    if t.isdigit():
        return int(t)
    cn = "一二三四五六七八九十"
    if t in cn:
        return cn.index(t) + 1
    if t == "十":
        return 10
    return 0


def _pick_exact(rows: list[dict], title: str, year: str = "") -> dict:
    """只认片名**完全相等**的条目。

    两级严格匹配（2026-10-31 真机反馈补的第 2 级）：
      1. 归一化后完全一致 —— 去掉 4K/国语/更新至第N集 这类尾巴。
         「仙逆」≠「仙逆剧场版 神临之战」≠「仙逆沙雕版」，一个都不许混进来。
      2. 「同一部剧的某一季」—— 站源把《庆余年》拆成「庆余年 第一季 / 第二季」，
         而 TMDB 是整部剧一个条目。这种情况按季号最小（第一季）收，
         但优先级低于第 1 级：真有整部剧条目时永远不用它。
    """
    want = _title_key(title)
    if not want:
        return {}
    wy = str(year or "")[:4]

    def score(r: dict) -> float:
        s = 0.0
        if wy and r.get("year"):
            s += 5 if wy == r["year"] else -3
        k = r.get("kind", "") + r.get("note", "") + r.get("region", "")
        if re.search(r"动漫|电视剧|剧集|连续剧|综艺|纪录片", k):
            s += 3
        if re.search(r"短剧|短漫|沙雕|解说|预告|花絮|合集|cut|剧场版|sp|特别篇", k, re.I):
            s -= 4
        m = re.search(r"第\s*(\d+)\s*集", r.get("note", ""))
        if m:
            s += min(int(m.group(1)), 300) / 1000.0
        return s

    tier1 = [r for r in rows if _title_key(r["name"]) == want]
    if tier1:
        tier1.sort(key=score, reverse=True)
        return tier1[0]
    base = _SEASON_RE.sub("", want)
    if not base:
        return {}
    tier2 = [r for r in rows
             if _SEASON_RE.sub("", _title_key(r["name"])) == base
             and _season_no(r["name"]) > 0]
    if tier2:
        tier2.sort(key=lambda r: (_season_no(r["name"]), -score(r)))
        return tier2[0]
    return {}


# 上次抓通的 base 记下来排最前：玩偶的 .live/.net 两个域名轮流抽风，
# 每次先试对的那个能把首屏解析从 6 秒压到 1 秒级。
_base_pick: dict[str, str] = {}


def _search_site(site: dict, title: str, budget: float = 6.0) -> list[dict]:
    """单站搜索，返回解析后的全部条目。

    注意区分两件事：**页面抓不到**（域名/网络问题 → 记健康惩罚，冷 20 分钟）
    和**抓到了但没有这部片**（正常业务结果，绝不算站死）。
    """
    name = site["name"]
    if not _site_ok(name):
        return []
    q = urllib.parse.quote(title)
    end = time.time() + budget
    bases = list(site.get("bases") or [])
    good = _base_pick.get(name)
    if good in bases:
        bases.remove(good)
        bases.insert(0, good)
    for base in bases:
        left = end - time.time()
        if left <= 0.5:
            break
        html = _get(base.rstrip("/") + site["search"].format(q=q),
                    timeout=min(4.0, left))
        if not html:
            continue
        _base_pick[name] = base
        return _parse_cards(html, site)      # 抓到了就信它（可能是真的没结果）
    _site_bad(name)
    return []


def resolve_play(title: str, year: str = "") -> dict:
    """点「▶ 播放」时的站源解析：并行搜全站 → 按顺序取第一个完全匹配。

    **首站优先短路**：玩偶命中就立刻返回，不等其余站（真机反馈「等 6 秒太久」）；
    玩偶没有才继续等其它站。全部站源都没有完全同名 → {}，
    这时才让按钮掉搜索页（海哥定的规则）。
    """
    title = str(title or "").strip()
    if not title:
        return {}
    ckey = f"pc:{_title_key(title)}:{str(year or '')[:4]}"
    hit = _cache_get(ckey)
    if hit is not None:
        return hit or {}

    chain = play_sites()
    got: dict[str, list[dict]] = {}
    ths: dict[str, threading.Thread] = {}
    for s in chain:
        t = threading.Thread(
            target=lambda s=s: got.__setitem__(s["name"], _search_site(s, title)),
            daemon=True)
        t.start()
        ths[s["name"]] = t

    def pick() -> dict:
        for s in chain:                  # 严格按配置顺序挑第一个命中的站
            rows = got.get(s["name"])
            if rows is None:             # 这一站还没搜完
                continue
            best = _pick_exact(rows, title, year)
            if best:
                return {"site_name": s["name"], "site_key": s["key"],
                        "id": best["id"] if s.get("id_fmt") == "num"
                        else s["detail"].format(best["id"]),
                        "num_id": best["id"], "name": best["name"],
                        "year": best.get("year", ""), "title_hit": True}
        return {}

    out: dict = {}
    end = time.time() + 9.0
    # 首站单独等：命中即回，不陪其它站跑完
    first = chain[0]["name"] if chain else ""
    if first:
        ths[first].join(max(0.05, min(6.0, end - time.time())))
        out = pick()
    if not out:
        for s in chain:
            if out:
                break
            ths[s["name"]].join(max(0.02, end - time.time()))
            out = pick()
    # 命中缓存 6h；全站没命中只缓存 20 分钟（站方上新后还能再试）
    _cache_set(ckey, out, ttl=(6 * 3600 if out else 1200))
    return out


def resolve_vod(site_name: str, title: str, year: str = "") -> dict:
    """只问单个站源（调试/兜底用）。站源顺序逻辑走 resolve_play。"""
    title = str(title or "").strip()
    site = next((s for s in play_sites() if s["name"] == site_name), None)
    if not site or not title:
        return {}
    best = _pick_exact(_search_site(site, title), title, year)
    if not best:
        return {}
    return {"site_name": site_name, "site_key": site["key"],
            "id": best["id"] if site.get("id_fmt") == "num" else site["detail"].format(best["id"]),
            "num_id": best["id"], "name": best["name"],
            "year": best.get("year", ""), "title_hit": True}
