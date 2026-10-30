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


def _cache_set(key: str, val):
    with _lock:
        if len(_cache) > 600:
            now = time.time()
            for k in [k for k, (e, _) in _cache.items() if e < now]:
                _cache.pop(k, None)
        _cache[key] = (time.time() + _TTL, val)


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
# 播放直达站源：解析「站源 spider 认的 vod id」（2026-10-30 海哥需求）
# ---------------------------------------------------------------------------

# app 端 9 参入口要的 id 形态 = 爬虫族 detailContent(id) 里的原始 id。
# Wogg 系爬虫都是「拿 id 拼 URL」，给完整路径 /voddetail/<n>.html；
# panwall 自己抓页面用的是纯数字 —— 两种形态别混。真机上如果发现选集页
# 打不开/挂错片，改这一个字段就行（纯服务端配置，不用重打包 APK）。
PLAY_ID_FULLPATH = True


def resolve_vod(site_name: str, title: str, year: str = "") -> dict:
    """给「▶ 播放」直达站源选集页解析 vod id。

    csp_Wogg 是 pg.jar 里的爬虫，中枢没法直接问它 —— 但它抓的就是 wogg
    网页，搜索/详情结构与上面 _one_site 完全一样，直接复用同一套组件：
    搜索页抓候选 id → 逐个开详情页对片名 → 命中即回。
    结果进缓存（同 12h TTL）；标题对不上也缓存（带 title_hit=False），
    避免每按一次播放都重抓一遍。失败不缓存。
    """
    title = str(title or "").strip()
    site = next((s for s in SITES if s["name"] == site_name), None)
    if not site or not title:
        return {}
    key = f"vres:{site_name}:{title}:{year}"
    hit = _cache_get(key)
    if hit is not None:
        return hit or {}

    q = urllib.parse.quote(title)
    page, base = "", ""
    for b in site["bases"]:
        page = _get(b + site["search"].format(q=q))
        if page:
            base = b
            break
    if not page:
        return {}
    nt = _norm(title)
    out: dict = {}
    for cid in _candidate_ids(page, site, limit=3):
        html = _get(base + site["detail"].format(cid))
        if not html:
            continue
        name = _title_of(html)
        nn = _norm(name)
        if nt and nn and nt not in nn and nn not in nt:
            continue                      # 片名对不上，换下一个候选
        out = {"site_name": site_name, "id": site["detail"].format(cid),
               "num_id": cid, "name": name or title, "title_hit": True}
        break
    if not out:
        cids = _candidate_ids(page, site, limit=1)
        if cids:
            # 没有标题对得上的候选 —— 退回第一个，让用户在选集页自己看
            out = {"site_name": site_name, "id": site["detail"].format(cids[0]),
                   "num_id": cids[0], "name": title, "title_hit": False}
    if out and not PLAY_ID_FULLPATH:
        out["id"] = out["num_id"]
    if out:
        _cache_set(key, out)
    return out
