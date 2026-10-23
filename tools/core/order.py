"""接口选择页排序 —— 置顶项 + 其余按实测延迟升序

App 的「接口选择」页面按 `jsm.json` 里 `sites` 数组的先后顺序展示，所以数组顺序
就是产品体验。用户 2026-10-17 的要求：

    ① 网盘及弹幕配置（`csp_Config`）置顶
    ② 玩偶哥哥（Wogg）/ 木偶哥哥（Mogg）紧随其后
    ③ 其余按接口延迟排序（快的在前）
    ④ 每次巡检后重排

延迟口径：取该站点在域名池里**所有存活地址中最快的那个**的实测毫秒数（巡检时
探的是站点首页，是「这个接口顺不顺」最贴近的代理指标）。没有存活地址的排到末尾，
彼此之间保持原版相对顺序 —— 用稳定排序，避免抖动时顺序乱跳。

不改任何站点内容，只重排数组顺序。`verify.py` 的 `core_ok` 判定按 key 集合比较
（`missing_keys` / `api_changed` / `type_changed`），与顺序无关，所以重排不破红线。

【2026-10-22 增加手动覆盖层】维护页可以上移/下移、可以隐藏某几个接口，见
`core/siteorder.py`。执行顺序固定为：
    自动排序（下面这套规则）→ 剔除 hidden → 套用手动 order
手动顺序**只部分指定也算数**：没被手动点过的接口按自动顺序插在它前一个手动项
之后（浮点插值），所以新出现的站点不会因为「没写进手动名单」就跑到末尾。
"""
from . import config as C
from . import siteorder
from . import sources as S
from . import store

_NO_LATENCY = 10 ** 9      # 没有存活地址：排最后
_GROUP_GW = 3              # dual 模式的「·网关」克隆统一排最后
_GROUP_ADDED = 2.5         # 用户加入的新站源：排在原版之后、网关克隆之前


def _latency(ent):
    """该 TAG 在池里最快存活地址的延迟（ms）；没有存活地址返回 None"""
    if not ent:
        return None
    lat = None
    for d in (ent.get("domains") or []):
        if d.get("status") != "alive":
            continue
        v = d.get("latency")
        if not v or v <= 0:
            continue
        lat = v if lat is None else min(lat, v)
    return lat


def _tokens_of(site):
    """一个站点可用于匹配置顶规则的标识：key / api / 池 TAG / name"""
    info = S.parse_ext(str(site.get("ext") or ""))
    tag = S.tag_of(site, info) if info else ""
    return [str(site.get("key") or ""), str(site.get("api") or ""),
            tag, str(site.get("name") or "")]


def _pin_index(site, table):
    """在置顶表里命中的序号；未命中返回 -1

    匹配规则：与 key / api / 池 TAG **完全相等**，或作为 name 的**子串**
    （这样配置里写「玩偶哥哥」就能命中「玩偶哥哥|网盘」，中英文都顺手）。
    """
    toks = _tokens_of(site)
    key, api, tag, name = toks[0], toks[1], toks[2], toks[3]
    for i, want in enumerate(table):
        if not want:
            continue
        if want in (key, api, tag) or (name and want in name):
            return i
    return -1


def _apply_manual(sites, manual):
    """按手动顺序重排；manual 里没有的按「前一个手动项之后」插空。

    为什么用浮点插值而不是「手动项排前面、其余追加」：
      后者会让换包后新增的站点全部堆到末尾，而它们其实是该按延迟排在中间的；
      插值能让新站点落在它自动顺序上的邻居附近，视觉上最不突兀。

    【新增站源例外】用户主动加入的站点（added 集合）不在 manual 名单里时，
    固定钉在末尾 —— 否则浮点插值会把它塞进手动名单中间，等于「加一个站
    就打乱一次用户排好的顺序」。
    """
    if not manual or not sites:
        return sites
    rank = {k: i for i, k in enumerate(manual)}
    keys = [str(s.get("key") or "") for s in sites]
    n = len(keys)
    added = _added_keys()
    pos = {}
    for i, k in enumerate(keys):
        if k in rank:
            pos[i] = float(rank[k])
            continue
        if k in added:
            pos[i] = float(len(manual)) + 0.5 + 0.0001 * i
            continue
        p = None
        for j in range(i - 1, -1, -1):          # 往前找最近的手动项
            if keys[j] in rank:
                p = float(rank[keys[j]]) + 0.0001 * (i - j)
                break
        if p is None:                            # 前面没有 → 往后找
            for j in range(i + 1, n):
                if keys[j] in rank:
                    p = float(rank[keys[j]]) - 0.0001 * (j - i)
                    break
        pos[i] = p if p is not None else (len(manual) + 0.0001 * i)
    return [sites[i] for i in sorted(range(n), key=lambda i: (pos[i], i))]


def _added_keys():
    """用户主动加入的新站源的 key 集合

    这些站点排在原版之后 —— 原版的 79 条顺序是用户精心排过的，
    新加的东西不该把它们顶到前面去（那等于每次加站都打乱一次）。
    """
    try:
        from . import addsite
        return set(addsite.added_keys())
    except Exception:
        return set()


def apply(jsm, pool, pins_top=None, pins_next=None):
    """就地重排 jsm["sites"]，返回统计信息"""
    sites = jsm.get("sites") or []
    if not getattr(C, "SITE_ORDER_ENABLED", True):
        return {"enabled": False, "moved": 0, "pinned": 0, "with_latency": 0}

    top = list(C.SITE_PIN_TOP if pins_top is None else pins_top)
    nxt = list(C.SITE_PIN_NEXT if pins_next is None else pins_next)
    added = _added_keys()

    lat_cache = {}

    def lat_of(site):
        info = S.parse_ext(str(site.get("ext") or ""))
        tag = S.tag_of(site, info) if info else ""
        if not tag:
            return None
        if tag not in lat_cache:
            lat_cache[tag] = _latency(pool.get(tag))
        return lat_cache[tag]

    def sort_key(item):
        i, site = item
        k = str(site.get("key") or "")
        if k.endswith("_GW"):
            return (_GROUP_GW, i, i)
        j = _pin_index(site, top)
        if j >= 0:
            return (0, j, i)
        j = _pin_index(site, nxt)
        if j >= 0:
            return (1, j, i)
        # 用户加入的新站源：排在原版之后，但仍在网关克隆之前
        if k in added:
            return (_GROUP_ADDED, i, i)
        lat = lat_of(site)
        return (2, _NO_LATENCY if lat is None else lat, i)

    before = [s.get("key") for s in sites]
    ordered = [s for _, s in sorted(enumerate(sites), key=sort_key)]

    # ① 用户隐藏的接口不下发（客户端「接口选择」页就看不到）
    hidden = set()
    manual = []
    try:
        so = siteorder.load()
        hidden = set(so.get("hidden") or [])
        manual = list(so.get("order") or [])
    except Exception as e:                       # 文件坏了就当没配过，不能因此不出配置
        store.log_event("error", "读取 site_order.json 失败: %s: %s" % (type(e).__name__, e))
    dropped = []
    if hidden:
        keep = []
        for s in ordered:
            if str(s.get("key") or "") in hidden:
                dropped.append(str(s.get("key") or ""))
            else:
                keep.append(s)
        ordered = keep
    # ② 套用用户手动顺序（覆盖延迟排序）
    ordered = _apply_manual(ordered, manual)

    jsm["sites"] = ordered
    after = [str(s.get("key") or "") for s in ordered]

    pinned = sum(1 for s in ordered if _pin_index(s, top) >= 0 or _pin_index(s, nxt) >= 0)
    with_lat = sum(1 for s in ordered if lat_of(s) is not None)
    moved = sum(1 for a, b in zip(before, after) if a != b)
    head_before, head_after = before[:8], after[:8]

    # 只记「头部顺序真的变了」——延迟每轮都在小幅波动，中后段相对位置几乎每轮
    # 都会动几个，全记会把这行日志刷成噪音；头部才是用户一眼看到的部分。
    # 注意 build() 每轮都从 baseline 重建，`before` 永远是原版顺序，所以不能拿它
    # 当基准，要用上次落盘的头部。
    prev = {}
    try:
        prev = store.load(getattr(C, "F_ORDER_HEAD", ""), {}) or {}
    except Exception:
        prev = {}
    prev_head = prev.get("head") or []
    if prev_head != head_after:
        store.log_event("render", f"接口排序：置顶 {pinned} 个 → "
                                  f"{'、'.join(str(x) for x in head_after[:pinned])}；"
                                  f"有延迟数据 {with_lat}/{len(ordered)} 个，"
                                  f"位置变动 {moved} 个")
        try:
            store.save(getattr(C, "F_ORDER_HEAD", ""),
                       {"head": head_after, "ts": int(__import__("time").time())})
        except Exception:
            pass
    return {"enabled": True, "moved": moved, "pinned": pinned, "with_latency": with_lat,
            "manual": len(manual), "hidden": len(hidden), "dropped": dropped}
