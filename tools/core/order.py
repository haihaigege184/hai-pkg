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
"""
from . import config as C
from . import sources as S
from . import store

_NO_LATENCY = 10 ** 9      # 没有存活地址：排最后
_GROUP_GW = 3              # dual 模式的「·网关」克隆统一排最后


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


def apply(jsm, pool, pins_top=None, pins_next=None):
    """就地重排 jsm["sites"]，返回统计信息"""
    sites = jsm.get("sites") or []
    if not getattr(C, "SITE_ORDER_ENABLED", True):
        return {"enabled": False, "moved": 0, "pinned": 0, "with_latency": 0}

    top = list(C.SITE_PIN_TOP if pins_top is None else pins_top)
    nxt = list(C.SITE_PIN_NEXT if pins_next is None else pins_next)

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
        lat = lat_of(site)
        return (2, _NO_LATENCY if lat is None else lat, i)

    before = [s.get("key") for s in sites]
    ordered = [s for _, s in sorted(enumerate(sites), key=sort_key)]
    jsm["sites"] = ordered
    after = [s.get("key") for s in ordered]

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
    return {"enabled": True, "moved": moved, "pinned": pinned, "with_latency": with_lat}
