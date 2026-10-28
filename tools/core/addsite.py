"""上游新站源「一键加入 PG 包」

为什么需要它
────────────
上游配置里不断冒出新站点（`core/sync.collect_new_sites` 负责登记），但
**登记 ≠ 可用**。实测（2026-10-23）：登记的 307 个新站源里，只有 28 条的
解析类存在于本包的 `pg.jar`（去重后 20 个 api），其余 279 条（`csp_XBPQ` /
`csp_AppRJ` / `csp_PanWebShare` …）加进去只会让端上多出一堆点不开的死条目。

所以「加入」要走三道闸，任何一道不过就不加：

  ① **能力闸**（静态，秒回）
       `csp_XXX`   → 本包 jar 的 dex 里必须有 `com.github.catvod.spider.XXX`
       ext 的第 1 段若是 `./lib/x.json` / `./js/x.js` / `./json/x.json`
                    → 这个文件必须在本包里（spider 必然去读它，缺了就废）
       ext 其余段引用的本地文件缺失 → 只警告（多半是可选的规则文件）
  ② **探测闸**（动态）
       从 ext 里抽出远端地址（`$$$` 段的 base，或单段 http URL，或 api 本身），
       真连一次，确认有响应。没响应就不加 —— 加一堆连不上的接口等于给用户添乱。
       端上本地依赖（127.0.0.1 / localhost）无法从中枢探测，这类放行并注明。
  ③ **落盘闸**
       通过后写进 `data/added_sites.json`，由 `patch.build()` 注入配置。

【红线】`baseline/jsm.json` 是原版母本，任何情况下都不改。新增站点只存在于
`data/added_sites.json`：
  * 换 PG 包不会丢 —— pkgupdate 的严格镜像只作用包目录，`data/` 不在其中；
  * `core_ok` 的 `added_keys` 本来就不参与判定 ⇒ **新增站点不破红线**；
  * 新增站点排在原版之后（`core/order.py` 里钉在末尾），不扰动用户的手动排序。

【与域名池的关系】新增站点一律用**独立 TAG**（`ADD_<hash>`），不与原版站点
共用 TAG。原因是上游常见「多个不同站点共用一个解析类」（`csp_TTian` 同时是
「追剧」和「天天」两个站），若按 `api` 归 TAG 会让 A 站的地址被 B 站的
域名顶掉。独立 TAG 让它们各自被巡检、各自被修复，互不干扰。
TAG 的唯一推导入口是 `sources.tag_of()`（它会读 added_sites.json 的覆盖表），
其它模块一律不要自己另算一套。

【ext 改写】上游统一写 `./lib/token.json`，本包按作者 README 下发的是
`./lib/tokenm.json`（见 `core/libfill.py`）⇒ 加入时必须改写，否则网盘类
站点在端上读到 404。除此之外 ext 原样保留 —— 各 spider 类对 ext 的分段
理解由 jar 里的类自己决定，中枢不猜。
"""
import hashlib
import json
import os
import re
import time

import requests
import urllib3

from . import config as C
from . import jarinfo
from . import sources as S
from . import store

urllib3.disable_warnings()

# ext 里引用到的本地文件（./lib/x.json、./js/x.js、./json/x.json）
_LOCAL_REF = re.compile(r"\./((?:lib|js|json)/[^\s\"'$\\]+)")
# 上游写 token.json，本包下发的是 tokenm.json（见 libfill.py）
_TOKEN_FILE = "token.json"
_TOKEN_OUR = "tokenm.json"
# 端上本地地址（App 自带 9978 web 服务）：中枢探测不到，但端上能用
_LOCAL_HOSTS = ("127.0.0.1", "localhost", "[::1]")


# ---------------------------------------------------------------------------
# 持久化
# ---------------------------------------------------------------------------

def _path():
    return getattr(C, "F_ADDED_SITES", "") or os.path.join(C.DATA_DIR, "added_sites.json")


def load():
    """读已加入的站点表：{key: rec}"""
    d = store.load(_path(), {}) or {}
    if not isinstance(d, dict):
        return {}
    sites = d.get("sites")
    return sites if isinstance(sites, dict) else {}


def save(rec):
    d = store.load(_path(), {}) or {}
    if not isinstance(d, dict):
        d = {}
    d["sites"] = rec or {}
    d["updated"] = int(time.time())
    store.save(_path(), d)
    return d


def added_keys():
    return set(load().keys())


def injected_sites():
    """注入配置用的站点数组（已去掉内部字段）"""
    out = []
    for key, rec in (load() or {}).items():
        s = (rec or {}).get("site")
        if isinstance(s, dict) and s.get("key"):
            out.append(s)
    return out


def tag_for(key):
    """新增站点的独立 TAG；不是新增站点返回空串"""
    rec = (load() or {}).get(str(key or ""))
    return str((rec or {}).get("tag") or "") if rec else ""


def tag_of_site(site):
    """站点在域名池里的 TAG（与 `sources.tag_of` 同一口径的便捷入口）。

    新增站点 → 独立 TAG；原版站点 → `ext 尾部 TAG or api or key`。
    **全工程统一走 `sources.tag_of()`**，这个函数只是给调用方少写一次 import。
    """
    k = str((site or {}).get("key") or "")
    t = tag_for(k)
    if t:
        return t
    info = S.parse_ext((site or {}).get("ext"))
    return S.tag_of(site, info) if info else ""


# ---------------------------------------------------------------------------
# 能力闸（静态）
# ---------------------------------------------------------------------------

def _api_kind(api):
    a = str(api or "")
    if a.startswith("csp_"):
        return "csp"
    if a.startswith("./"):
        return "local"
    if a.startswith("http"):
        return "http"
    return "other"


def _local_refs(site):
    """ext 里引用的本地文件（相对包根，去掉 ./ 前缀）"""
    out = []
    blob = json.dumps((site or {}).get("ext"), ensure_ascii=False)
    for m in _LOCAL_REF.finditer(blob):
        r = m.group(1)
        if r not in out:
            out.append(r)
    return out


def _is_local_host(url):
    host = S.norm_host(url)
    if host in _LOCAL_HOSTS or host.startswith("127."):
        return True
    # 中枢自己的地址（自建站点的 api 就写的中枢）同样不算远端站源
    own = set(getattr(C, "SELF_HOSTS", []) or [])
    for base in [getattr(C, "LAN_BASE", "")] + list(getattr(C, "PUBLIC_BASES", []) or []):
        if base:
            h = S.norm_host(base)
            if h:
                own.add(h)
    return host in own


# 上游配置里混着「说明/公告」这类**文档条目**（api 常常也是 csp_XPath，
# 但 ext 指向一个规则文件或干脆没有 ext）—— 它们不是站源，加进去只污染
# 端上的接口列表。见到这些词直接标成不可加。
_DOC_WORDS = ("说明", "公告", "更新维护", "请勿", "广告", "官网", "订阅",
              "群组", "群号", "电报", "发布页", "接口地址", "配置地址")


def _looks_like_doc(site):
    """这条是不是「说明/公告」类文档条目（不是站源）"""
    name = str((site or {}).get("name") or "")
    key = str((site or {}).get("key") or "")
    for w in _DOC_WORDS:
        if w in name or w in key:
            return True
    return False


def static_check(site):
    """静态能力判定：这条站点在本包里**能不能跑起来**。

    返回 {"ok":bool, "reason":str, "kind":str, "warnings":[...]}
    """
    api = str((site or {}).get("api") or "")
    kind = _api_kind(api)
    warns = []

    if kind == "csp":
        cls = api[4:]
        if not jarinfo.has_spider(api):
            return {"ok": False, "kind": kind, "warnings": warns,
                    "reason": "本包 jar 里没有 csp_%s 这个解析类（端上会报「解析器不存在」）" % cls}
    elif kind == "local":
        rel = api.lstrip("./")
        if not jarinfo.has_pkg_file(rel):
            return {"ok": False, "kind": kind, "warnings": warns,
                    "reason": "本包里没有 %s" % api}
    elif kind == "http":
        if int((site or {}).get("type") or 0) != 1:
            return {"ok": False, "kind": kind, "warnings": warns,
                    "reason": "http 类型的 api 只支持 type=1 的 JSON 接口"}
    else:
        return {"ok": False, "kind": kind, "warnings": warns,
                "reason": "无法识别的 api 形态：%s" % (api or "(空)")}

    if _looks_like_doc(site):
        return {"ok": False, "kind": "doc", "warnings": warns,
                "reason": "这是上游配置里的说明/公告条目，不是站源"}

    ext = (site or {}).get("ext")
    parts = []
    if isinstance(ext, str) and "$$$" in ext:
        parts = [p.strip() for p in ext.split("$$$")]
    elif isinstance(ext, str) and ext.strip():
        # 没有 $$$ 分段时，整串就是**唯一**的配置段
        # （如 ext='./json/企鹅直播.json'）—— 它是本地文件引用，包里没有就废了
        parts = [ext.strip()]
    # ext 的**第 1 段**是 spider 必读的配置/token 文件，缺了整个站就废了
    first = parts[0] if parts else ""
    if first.startswith("./"):
        rel = first.lstrip("./")
        if rel == "lib/" + _TOKEN_FILE:
            rel = "lib/" + _TOKEN_OUR      # 会被改写，改写后本包有
        if not jarinfo.has_pkg_file(rel):
            return {"ok": False, "kind": kind, "warnings": warns,
                    "reason": "ext 引用的 %s 不在本包里（端上会 404）" % first}

    # 其余段引用的本地文件缺失只警告，不拦截（多半是可选的规则文件）
    for rel in _local_refs(site):
        if rel in ("lib/" + _TOKEN_FILE, "lib/" + _TOKEN_OUR):
            continue
        if not jarinfo.has_pkg_file(rel):
            warns.append("ext 引用的 %s 不在本包里" % rel)
    return {"ok": True, "kind": kind, "reason": "", "warnings": warns}


def _rewrite_token(site):
    """把 ext 里的 ./lib/token.json 换成本包下发的 ./lib/tokenm.json。

    上游配置统一写 token.json，本包按作者 README 的要求发的是 tokenm.json
    （见 core/libfill.py）。不换的话网盘类站点读到的是 404。

    【两种 ext 形态都要处理】
      * 带 $$$ 分段：`./lib/token.json$$$https://...$$$proxy$$$1` → 改第 1 段
      * 单段（整串就是那一个文件引用）：`./lib/token.json` → 整串改写
    只改写这一种文件名，其余 ext 段原样保留 —— 各 spider 类对 ext 的分段
    理解由 jar 里的类自己决定，中枢不猜。
    """
    site = json.loads(json.dumps(site, ensure_ascii=False))
    ext = site.get("ext")
    if not isinstance(ext, str) or not ext.strip():
        return site
    if "$$$" in ext:
        parts = ext.split("$$$")
        for i, p in enumerate(parts):
            if p.strip() == "./lib/" + _TOKEN_FILE:
                parts[i] = "./lib/" + _TOKEN_OUR
        site["ext"] = "$$$".join(parts)
        return site
    # 单段形态
    if ext.strip() == "./lib/" + _TOKEN_FILE:
        site["ext"] = "./lib/" + _TOKEN_OUR
    return site


# ---------------------------------------------------------------------------
# 探测闸（动态）
# ---------------------------------------------------------------------------

def _candidate_urls(site, limit=3):
    """从站点定义里抽出可探测的远端地址（最多 limit 个）"""
    urls = []
    api = str((site or {}).get("api") or "")
    ext = (site or {}).get("ext")

    if api.startswith("http"):
        urls.append(api)
    if isinstance(ext, str) and "$$$" in ext:
        info = S.parse_ext(ext)
        if info and info.get("base"):
            urls.append(info["base"])
        elif "://" in ext.split("$$$")[0]:
            # 单段 http 放在第 1 段（部分 spider 自己解析整串 ext）
            urls.append(ext.split("$$$")[0].strip())
    elif isinstance(ext, str) and ext.strip().startswith("http"):
        urls.append(ext.strip())
    elif isinstance(ext, dict):
        for v in ext.values():
            if isinstance(v, str) and v.startswith("http"):
                urls.append(v)
            elif isinstance(v, list):
                for x in v:
                    if isinstance(x, str) and x.startswith("http"):
                        urls.append(x)
    out = []
    for u in urls:
        if u not in out:
            out.append(u)
    return out[:limit]


def _ext_base(site):
    """站点在域名池里的基址；没有远端地址返回空串。

    与探测闸同一套取址逻辑（`_candidate_urls`）。上游有些站点的 ext 就是
    一整个地址（如厂长影视 `ext=https://cz01.vip`），`S.parse_ext` 只认带
    `$$$` 的形态、对这种返回 None —— 早先 seed_pool 会整个漏掉它们，
    表现为「加得进来、探测也通过，却既不巡检也不参与漂移修复」。
    本地/私有地址（127.0.0.1 等）规整后是空串，自动排除。
    **中枢自己的地址同样排除**：自建站点（如 TMDB 海报墙）的 api 指向中枢，
    那是本站不是上游站源，进了域名池只会让巡检去探自己。
    """
    urls = _candidate_urls(site, limit=1)
    if not urls or _is_local_host(urls[0]):
        return ""
    return S.norm_base(urls[0])


def probe_url(url, timeout=10):
    """探一次远端地址。返回 (alive, note, ms)

    口径刻意比域名池巡检**宽松**：这里只判「能不能连通」，不判「是不是真站点」
    —— 一个只有 JSON 接口、首页很小的站也能正常用。
      * HTTP >= 400 → 不通（人机校验页除外，客户端通常能过）
      * 空响应体 → 不通
      * 其余 → 通
    """
    t0 = time.time()
    try:
        r = requests.get(url, timeout=timeout, verify=False, allow_redirects=True,
                         headers={"User-Agent": C.UA, "Accept": "*/*"})
    except Exception as e:
        return False, "%s: %s" % (type(e).__name__, str(e)[:80]), int((time.time() - t0) * 1000)
    ms = int((time.time() - t0) * 1000)
    n = len(r.content or b"")
    if r.status_code >= 400:
        low = (r.content or b"")[:4000].decode("utf-8", "ignore").lower()
        chal = next((c for c in getattr(C, "CHALLENGE_HINTS", ()) if c in low), None)
        if chal:
            return True, "人机校验页(HTTP %s，数据中心 IP 被拦，客户端通常可过)" % r.status_code, ms
        return False, "HTTP %s（%d 字节）" % (r.status_code, n), ms
    if n <= 0:
        return False, "响应体为空", ms
    return True, "HTTP %s · %d 字节" % (r.status_code, n), ms


def probe_site(site):
    """探测闸：这条站点的接口现在通不通。

    返回 {"ok":bool, "reason":str, "url":str, "ms":int, "checked":int}
    没有可探测的远端地址（纯本地/聚合类站点）时 ok=True 且 reason 说明原因
    —— 那类站点本来就不连远端，探不到不代表坏。
    """
    urls = _candidate_urls(site)
    if not urls:
        return {"ok": True, "url": "", "ms": 0, "checked": int(time.time()),
                "reason": "无远端地址（本地/聚合类），未做连通性探测"}
    local = [u for u in urls if _is_local_host(u)]
    if local and len(local) == len(urls):
        return {"ok": True, "url": local[0], "ms": 0, "checked": int(time.time()),
                "reason": "指向端上本地服务（%s），中枢探不到，端上应当可用" % local[0]}
    last = ""
    for u in urls:
        if _is_local_host(u):
            continue
        alive, note, ms = probe_url(u)
        if alive:
            return {"ok": True, "url": u, "ms": ms, "checked": int(time.time()),
                    "reason": note}
        last = note
    return {"ok": False, "url": urls[0], "ms": 0, "checked": int(time.time()),
            "reason": "接口探测失败：%s" % last}


# ---------------------------------------------------------------------------
# 候选清单
# ---------------------------------------------------------------------------

def _baseline_index():
    """原版的 key 集合与 api 集合（用于判重）"""
    keys, apis = set(), set()
    try:
        with open(C.BASELINE_JSM, "r", encoding="utf-8-sig") as f:
            for s in (json.load(f).get("sites") or []):
                if isinstance(s, dict):
                    if s.get("key"):
                        keys.add(str(s["key"]))
                    if s.get("api"):
                        apis.add(str(s["api"]))
    except Exception:
        pass
    return keys, apis


def _new_sites_file():
    return os.path.join(C.DATA_DIR, "new_sites.json")


def _new_sites():
    d = store.load(_new_sites_file(), {}) or {}
    return d.get("sites") or []


def dup_check(key, api):
    """查这个站点是不是「原版已经有了」。

    返回 (kind, reason)；kind 为空串表示不重复。
    kind: "dup_key" / "dup_api"

    【为什么必须单独抽出来】candidates()、probe 路由、add() 三处都要判重 ——
    三处各写一遍必然走偏。实测过：candidates() 判「原版已有同 key」，
    probe 路由却不判，返回 static_ok=True，用户看到「可以加」，一点就被拒。
    """
    b_keys, b_apis = _baseline_index()
    key = str(key or "")
    api = str(api or "")
    if key and key in b_keys:
        return "dup_key", "原版里已经有同 key 的站点，不能重复加入"
    if api and api in b_apis:
        return "dup_api", "原版已经收录了同一个解析类 %s（多半是同一个站被改了 key）" % api
    return "", ""


def candidates(probe=False):
    """上游新站源清单 + 每条的加入可行性。

    数据来自 `data/new_sites.json`（sync.collect_new_sites 登记的）。
    返回 {"items":[...], "added":[key...], "jar":{...}}

    【为什么要回源】new_sites.json 只登记了 key/name/api/base，**没有 ext**，
    而 ext 才是「怎么访问这个站」的全部信息 —— 只靠摘要判不了「ext 引用的
    文件在不在包里」。所以这里按 source 回源取完整定义再判。
    upstream.fetch_config 自带原文缓存（data/upstream_cache/<md5>.bin），
    命中缓存就不走网络；一次上游配置能覆盖几十个条目。

    【回源必须按 key+api 配对】同一个 key 可能出现在多个上游源里，而各源的
    api 不一样（实测「少儿教育」在 4 个源里分别用 csp_BiliGuard / csp_Bili）。
    按 key 单键索引会张冠李戴 —— 拿 A 源的 api 去配 B 源的 ext，判定结果
    全错（实测表现为「jar 里明明没有这个类，却标成可加入」）。
    所以索引必须是 (key, api) 二元组。

    probe=True 时额外真连一次（慢，用于用户主动点「批量检测」）。
    """
    already = load()

    # 按 source 分组回源，一次 fetch 覆盖同源的所有条目
    by_src = {}
    for s in _new_sites():
        src = str(s.get("source") or "")
        if src:
            by_src.setdefault(src, []).append(s)

    # 【必须按源隔离】同一个 key 可能出现在多个上游源里，而各源的 api 不一样
    # （实测「少儿教育」在 4 个源里分别用 csp_BiliGuard / csp_Bili）。
    # 若把各源的条目混进一个扁平索引再按 key 取，先到先得 ⇒ 拿 A 源的 api
    # 配 B 源的 ext，判定全错（表现为「jar 里明明没这个类，却标成可加入」）。
    # 正确做法：每条登记项只在自己的 source 里查，且 key+api 必须同时对上。
    src_cache = {}

    def _src_sites(src):
        if src not in src_cache:
            src_cache[src] = _fetch_sites(src)
        return src_cache[src]

    def _lookup(key, api, src):
        """在**该条目自己的上游源**里按 key+api 定位完整定义。"""
        for s in _src_sites(src):
            if not isinstance(s, dict):
                continue
            if str(s.get("key") or "") != key:
                continue
            if api and str(s.get("api") or "") != api:
                continue          # 同 key 不同 api —— 不是同一条，不能拿
            return s
        # 同源里 key 改了但 api 还在（上游偶尔改 key）。
        # 【只认唯一匹配】上游常见「多个站共用一个解析类」（csp_TTian 同时是
        # 「追剧」和「天天」）—— 候选不唯一就说明认不准，宁可判为不可加，
        # 也不能替用户挑一个塞进 PG 包。
        if api:
            hit = [s for s in _src_sites(src)
                   if isinstance(s, dict) and str(s.get("api") or "") == api]
            if len(hit) == 1:
                return hit[0]
        return None

    items = []
    for s in _new_sites():
        key = str(s.get("key") or "")
        if not key:
            continue
        api = str(s.get("api") or "")
        src = str(s.get("source") or "")
        site = _lookup(key, api, src)
        item = {
            "key": key,
            "name": s.get("name") or key,
            "api": api,
            "type": s.get("type"),
            "base": s.get("base") or "",
            "source": src,
            "seen": s.get("seen") or 0,
            "added": key in already,
            "tag": (already.get(key) or {}).get("tag") or "",
            "resolved": bool(site),
        }
        if item["added"]:
            item["ok"] = True
            item["reason"] = "已加入"
            item["warnings"] = []
            item["kind"] = "added"
        else:
            # 判重（collect_new_sites 的三维判重会漏，改过 key/name 的站要在这里兜住）
            dkind, dreason = dup_check(key, api)
            if dkind:
                item["ok"] = False
                item["reason"] = dreason
                item["warnings"] = []
                item["kind"] = "dup"
            else:
                probe_site_def = dict(site) if isinstance(site, dict) else \
                    {"key": key, "api": api, "type": s.get("type"),
                     "name": s.get("name") or key}
                chk = static_check(probe_site_def)
                item["ok"] = chk["ok"]
                item["reason"] = chk["reason"]
                item["warnings"] = chk["warnings"]
                item["kind"] = chk["kind"]
                if probe and chk["ok"] and isinstance(site, dict):
                    pr = probe_site(site)
                    item["probe"] = pr
                    item["ok"] = pr["ok"]
                    if not pr["ok"]:
                        item["reason"] = pr["reason"]
        items.append(item)
    return {"items": items, "added": sorted(already.keys()),
            "jar": jarinfo.summary()}


_FETCH_CACHE = {}


def _fetch_sites(url):
    """抓一个上游配置，返回它的 sites 列表（失败返回空表）

    带进程内缓存 —— 一次候选判定会按源回源几十次，同一个源不该反复解析。
    upstream.fetch_config 本身有磁盘原文缓存，这里只是省掉重复的解析开销。
    """
    if url in _FETCH_CACHE:
        return _FETCH_CACHE[url]
    try:
        from . import upstream
        cfg, _final, _note = upstream.fetch_config(url)
    except Exception:
        cfg = {}
    sites = (cfg or {}).get("sites") or []
    _FETCH_CACHE[url] = sites
    return sites


def resolve_site(cand):
    """拿到候选的**完整**站点定义（含 ext）。

    `new_sites.json` 只登记了 key/name/api/base，没有 ext —— 而 ext 才是
    「怎么访问这个站」的全部信息。所以按登记的 source 重新抓一份上游配置，
    按 key+api 定位原始条目。upstream.fetch_config 自带原文缓存，命中缓存
    就不走网络。
    """
    raw = (cand or {}).get("site")
    if isinstance(raw, dict) and raw.get("api"):
        return raw
    url = str((cand or {}).get("source") or "")
    if not url:
        return None
    key = str((cand or {}).get("key") or "")
    api = str((cand or {}).get("api") or "")
    for s in _fetch_sites(url):
        if not isinstance(s, dict):
            continue
        if str(s.get("key") or "") == key and str(s.get("api") or "") == api:
            return s
    # key 变了也要能捞回来（上游偶尔改 key）。
    # 【只认唯一匹配】同源里同 api 有多个条目时（csp_TTian 同时被「追剧」和
    # 「天天」用），任选一个都会装错站 —— 认不准就直接说抓不到。
    if api:
        hit = [s for s in _fetch_sites(url)
               if isinstance(s, dict) and str(s.get("api") or "") == api]
        if len(hit) == 1:
            return hit[0]
    return None


# ---------------------------------------------------------------------------
# 加入 / 移除
# ---------------------------------------------------------------------------

def add(key, force=False):
    """把一个上游新站源加入 PG 包。

    返回 {"ok":bool, "error":str, "site":{...}, "probe":{...}}
    """
    key = str(key or "").strip()
    if not key:
        return {"ok": False, "error": "缺少 key"}

    rec = load()
    if key in rec:
        return {"ok": True, "site": (rec.get(key) or {}).get("site") or {},
                "message": "已经在列表里了"}

    cand = None
    for s in _new_sites():
        if str(s.get("key") or "") == key:
            cand = s
            break
    if not cand:
        return {"ok": False, "error": "上游清单里没有这个站点（先执行一次「上游同步」）"}

    api = str(cand.get("api") or "")
    dkind, dreason = dup_check(key, api)
    if dkind:
        return {"ok": False, "error": dreason, "kind": "dup"}

    site = resolve_site(cand)
    if not site:
        return {"ok": False, "error": "拿不到这个站点的完整定义（上游配置已变或抓不到），先重跑一次「上游同步」"}

    site = _rewrite_token(site)
    site["key"] = key
    if not site.get("name"):
        site["name"] = cand.get("name") or key

    # ① 能力闸
    chk = static_check(site)
    if not chk["ok"]:
        return {"ok": False, "error": chk["reason"], "kind": chk["kind"]}

    # ② 探测闸
    pr = probe_site(site)
    if not pr["ok"] and not force:
        return {"ok": False, "error": pr["reason"], "probe": pr,
                "can_force": True, "kind": chk["kind"]}

    tag = "ADD_" + hashlib.md5(key.encode("utf-8")).hexdigest()[:8]
    entry = {
        "site": site,
        "tag": tag,
        "added_at": int(time.time()),
        "source": cand.get("source") or "",
        "probe": pr,
        "warnings": chk.get("warnings") or [],
    }
    rec[key] = entry
    save(rec)
    store.log_event("addsite", "加入新站源 %s（%s）· tag=%s · %s"
                    % (key, site.get("api") or "-", tag, pr.get("reason") or ""))
    return {"ok": True, "site": site, "probe": pr, "tag": tag,
            "warnings": chk.get("warnings") or [],
            "message": "已加入 %s" % (site.get("name") or key)}


def remove(key):
    """从已加入列表里移除（不动原版，也不改包目录）

    【顺带清掉域名池条目】TAG 是按 key 算出来的，而 remove() 之后
    added_sites.json 里就查不到这个 key 了 —— 所以必须在删之前先把 TAG
    取出来。调用方（app.py）用返回的 tag 去清域名池。
    """
    key = str(key or "").strip()
    rec = load()
    if key not in rec:
        return {"ok": False, "error": "这个站点没在已加入列表里"}
    tag = str((rec.get(key) or {}).get("tag") or "")
    rec.pop(key, None)
    save(rec)
    store.log_event("addsite", "移除新站源 %s" % key)
    return {"ok": True, "message": "已移除 %s" % key, "tag": tag}


def tags_of(keys):
    """取一批 key 对应的独立 TAG（移除时用来清域名池）"""
    rec = load()
    return [str((rec.get(k) or {}).get("tag") or "") for k in (keys or [])]


# ---------------------------------------------------------------------------
# 域名池
# ---------------------------------------------------------------------------

def seed_pool(pool):
    """把新增站点的远端地址并入域名池（独立 TAG），使其参与巡检与自动修复。

    与 `sync.seed_pool_from_baseline` 同构，只是数据源换成已加入的站点表。
    """
    n = 0
    for key, rec in (load() or {}).items():
        site = (rec or {}).get("site") or {}
        tag = str((rec or {}).get("tag") or "")
        if not tag:
            continue
        base = _ext_base(site)
        if not base:
            continue
        ent = pool.setdefault(tag, {"tag": tag, "name": site.get("name") or key,
                                     "api": site.get("api") or "", "domains": []})
        n += len(S.merge_domains(ent, [base], origin="added"))
    return n


def drop_pool(pool, keys=None, tags=None):
    """移除站点时把它在域名池里的条目也带走

    【为什么要传 tags】TAG 是按 key 算的，站点移出 added_sites.json 之后
    就再也查不到 key → TAG 的对应关系了。所以 remove() 会先把 TAG 返回出来，
    调用方拿它来清池子。（传 keys 也能查，但那时记录已经没了。）
    """
    tg = [str(t) for t in (tags or []) if t]
    if not tg and keys:
        tg = tags_of(keys)
    for tag in tg:
        if tag and tag in pool:
            pool.pop(tag, None)
    return len(tg)


def prune_orphan_pool(pool):
    """清掉域名池里的**孤儿** ADD_ 条目。

    孤儿 = TAG 以 ADD_ 开头，但 added_sites.json 里已经没有对应的站点了。
    正常流程下 remove() 会顺带清掉，但历史版本（以及手工改文件）可能留下
    残留 —— 它们会被巡检、会被写进 report，白占资源还让明细页冒出幽灵行。
    启动时清一次最省事。
    """
    live = set()
    for rec in (load() or {}).values():
        t = str((rec or {}).get("tag") or "")
        if t:
            live.add(t)
    orphans = [t for t in list(pool.keys())
               if str(t).startswith("ADD_") and t not in live]
    for t in orphans:
        pool.pop(t, None)
    if orphans:
        store.log_event("addsite", "清理域名池里的孤儿新增条目 %d 个：%s"
                        % (len(orphans), "、".join(orphans[:8])))
    return orphans
