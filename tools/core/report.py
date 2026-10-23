"""接口状态明细 —— 给「接口状态」类命令/页面用的统一数据源

为什么单独抽一个模块
--------------------
健康巡检的数据分散在三处，视角各不相同：

  * POOL / data/domains.json —— **TAG 视角**（28 条）。一个 TAG 可能对应多个站点，
    反过来也有站点没有 TAG（纯本地脚本、ext 不指向远程地址）。
  * health_state.json —— 巡检的**滞后状态**（连续失败轮数、是否已告警）。
  * 基线的 sites 列表 —— **站点视角**（79 条），这才是用户在客户端里看到的那一份。

用户问「接口状态」时想要的是**站点视角**（79 条：哪个能用、哪个失效、走的哪个
域名、多快），而不是 TAG 视角。所以这里把它拼出来，避免各调用方各拼一套、
口径还不一致（曾经 patch 用 q>=2、通知用 usable_count，两处口径就已经出现过分歧）。

判活口径与 patch.build / _apply_health_notify 完全一致，全部来自 health：
  best 有值  -> 该站点会被指向可用地址，视为「正常」
  best 无值  -> 无可用地址；再看 health_state 的 streak 决定是「观察中」还是「已确认失效」
"""
import socket
import time

from . import config as C
from . import endpoints as endpoints_mod
from . import health
from . import renderbase
from . import sources as S
from . import store


def lan_ip():
    """本机在局域网里的 IPv4（不依赖外网，UDP connect 不发包）"""
    s = None
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except Exception:
        return ""
    finally:
        if s:
            try:
                s.close()
            except Exception:
                pass


def public_bases():
    """全部对外入口（当前生效的渲染口径在前）。

    第一项取 **renderbase.current()**（可被手动 pin 或自动故障切换改写），
    其余来自 C.PUBLIC_BASES 的配置顺序，去重。
    """
    out = []
    try:
        cur = (renderbase.current() or "").rstrip("/")
    except Exception:
        cur = (getattr(C, "PUBLIC_BASE", "") or "").rstrip("/")
    if cur:
        out.append(cur)
    for u in (getattr(C, "PUBLIC_BASES", None) or []):
        u = (u or "").rstrip("/")
        if u and u not in out:
            out.append(u)
    return out


def endpoints():
    """客户端该填的接口地址（内网 → FRP → CF 的顺序）。

    `public_base` / `public_list` 保持**渲染口径**（即 C.PUBLIC_BASE 那一条），
    因为 jsm.json 里的 /gw/<TAG>/ 是拿它拼的，换了会让端打到错的地方；
    而 `configs` 是给人看的候选清单，顺序由 core/endpoints.py 决定：
    内网第一、FRP 第二、CF 第三。
    """
    lan_base = (C.LAN_BASE or "").rstrip("/")
    if not lan_base:
        ip = lan_ip()
        if ip:
            lan_base = "http://%s:%d" % (ip, C.PORT)
    pubs = public_bases()
    join = lambda b: (b + "/hai/jsm.json") if b else ""   # noqa: E731
    try:
        configs = endpoints_mod.list_ordered()
    except Exception:
        configs = []
    return {
        "lan": join(lan_base),
        "public": join(pubs[0]) if pubs else "",
        # 多条外网入口（如 CF 隧道 + 自建 gost 穿透），供命令里逐条展示
        "public_list": [join(u) for u in pubs],
        # 内网 → FRP → CF 的完整候选清单（维护页与每日通知都用这一份）
        "configs": configs,
        # 局域网直连不需要任何隧道，最稳；外网用于不在同一网段的设备
        "recommend": join(lan_base) or (join(pubs[0]) if pubs else ""),
        "lan_base": lan_base,
        "public_base": pubs[0] if pubs else "",
    }


def _domain_entry(ent, base):
    """在某个 TAG 的域名列表里找出与 base 同站的那一条"""
    if not ent or not base:
        return None
    for d in (ent.get("domains") or []):
        if S.same_site(d.get("url") or "", base):
            return d
    return None


def _state_of(tag, hstate):
    e = (hstate or {}).get(tag) or {}
    return int(e.get("streak") or 0), bool(e.get("notified_offline")), e.get("ts") or 0


def _load_jsm():
    """按「离客户端由近到远」的顺序取配置

    baseline 是原版母本，永远不含失效标注；拿它算「是否已标注」恒为 0，
    于是明细会显示「一切正常」，而客户端实际拿到的那份却标着 ⚠️失效 ——
    这正是用户反馈「明细说正常、配置里却标着失效」的根因。所以优先读
    真正下发给客户端的 F_SERVED，其次本地 dist，最后才回落到 baseline。
    """
    import json
    for p, tag in ((getattr(C, "F_SERVED", ""), "served"),
                   (getattr(C, "F_DIST_JSM", ""), "dist"),
                   (C.BASELINE_JSM, "baseline")):
        if not p:
            continue
        try:
            with open(p, "r", encoding="utf-8-sig") as f:
                return json.load(f), tag
        except Exception:
            continue
    return {}, "none"


def _site_item(s, pool, hstate, need, mark_enabled, manual_keys, hidden_keys):
    """把一个 jsm.sites 条目算成明细行（站点视角）。

    抽出来的原因：隐藏的站点已经不在下发配置里，但仍然要在维护页列出来
    （否则用户点了「隐藏」就再也找不到它、无法恢复），所以这一套计算要被
    「当前配置」和「基线补齐」两条路径共用。
    """
    key = s.get("key")
    name = str(s.get("name") or "")
    ext = s.get("ext")
    info = S.parse_ext(ext) if isinstance(ext, str) else None
    base = (info or {}).get("base") or ""
    tag = S.tag_of(s, info)
    tag_src = ("ext" if (info or {}).get("tag") else "api") if tag else ""
    ent = pool.get(tag) if tag else None

    det = health.pick_best_detail(ent) if ent else None
    best = det if det and (det.get("quality") or 0) >= 2 else None
    # 与 patch.build 对齐口径：**原地址自己还活着时配置里根本不会换**，
    # 明细也必须显示原地址。否则会出现「明细说是 666.xxx，配置里写的是
    # 原版 123.xxx」这种自相矛盾，用户一比对就会怀疑数据不准。
    orig = _domain_entry(ent, base) if (ent and base) else None
    if orig and orig.get("status") == "alive" and int(orig.get("quality") or 0) >= 2:
        best = {"url": orig.get("url") or base,
                "latency": orig.get("latency") or 0,
                "quality": orig.get("quality"),
                "note": orig.get("note") or ""}
    usable = health.usable_count(ent) if ent else 0
    soft = (health.survivable_count(ent) - usable) if ent else 0
    total = len(ent.get("domains", [])) if ent else 0
    alive = sum(1 for d in (ent or {}).get("domains", [])
                if d.get("status") == "alive")
    retired = sum(1 for d in (ent or {}).get("domains", []) if d.get("retired"))

    streak, notified, ts = _state_of(tag, hstate) if tag else (0, False, 0)
    if best:
        status = "ok"
    elif not base:
        # ext 里**压根没有远程地址**（纯本地 JS/Python 脚本站点、本地 txt
        # 分享库、jar 插件等），这类站点不参与域名巡检，「查不到域名」是
        # 正常的，不是故障。判据是 base（必须带 $$$ 且有 http(s) 段）而不是
        # tag —— 用 tag 会把「第二段就是域名」的站点误判成内置。
        status = "local"
    elif not ent:
        # 有远程地址但域名池里没有这条 —— 要么池还没建立（首次部署/同步
        # 失败），要么该域名未被纳入巡检。这跟「站点失效」是两回事，
        # 单独报出来，否则会被误当成"全部正常"。
        status = "unknown"
    elif soft > 0:
        # 有地址活着但质量存疑（人机校验页 / 前端空壳）。
        # 我们这种数据中心 IP 探不到真相，**不能判死**，所以单列一档。
        status = "guard"
    elif streak >= need:
        status = "offline"          # 已确认失效（会告警）
    elif streak > 0:
        status = "watch"            # 本轮无可用但未达阈值，暂不动作
    else:
        status = "unknown"          # 域名池有记录但从未探测过

    hidden = str(key or "") in hidden_keys
    marked = "⚠️失效" in name
    return {
        "key": key,
        "name": name,
        "hidden": hidden,           # 用户主动隐藏（不在下发列表里）
        "manual": str(key or "") in manual_keys,   # 顺序被用户手动排过
        "pinned": _is_pinned(s),
        "marked": marked,           # 配置里是否已被打上失效标注
        # 标注与实际不符：标了失效但其实有可用地址（用户 3 号反馈的场景）
        "stale_mark": bool(marked and best),
        "tag": tag,
        "tag_src": tag_src,         # ext=来自 ext 尾部 TAG；api=用 api 兜底
        "base": base,               # ext 里的远程站根（空=内置站点）
        "api": s.get("api") or "",
        "type": s.get("type"),
        "proxy": (info or {}).get("proxy") or "",
        "status": status,
        "streak": streak,
        "usable": usable,
        "alive": alive,
        "total": total,
        "retired": retired,         # 池里已判弃用（不再巡检、不会写回配置）的域名数
        "best": (best or {}).get("url") or "",
        "latency": (best or {}).get("latency") or 0,
        "note": (best or {}).get("note") or "",
        "checked": ts,
    }


def _is_pinned(site):
    """是否命中「置顶」规则（维护页给这类行标一个钉）"""
    try:
        from . import order as O
        if O._pin_index(site, C.SITE_PIN_TOP) >= 0:
            return "top"
        if O._pin_index(site, C.SITE_PIN_NEXT) >= 0:
            return "next"
    except Exception:
        pass
    return ""


def _baseline_sites():
    """读基线 jsm 的 sites（仅用于补齐「已隐藏、因此不在下发配置里」的条目）"""
    import json
    try:
        with open(C.BASELINE_JSM, "r", encoding="utf-8-sig") as f:
            return json.load(f).get("sites") or []
    except Exception:
        return []


def sites_report(pool, jsm=None, state=None, order_by="config"):
    """生成站点级明细。

    jsm 为空时自动按 served → dist → baseline 取。

    【2026-10-22 顺序口径改为「实时接口顺序」】
    用户要求维护页的接口状态卡片**按实际下发的顺序**排列 —— 也就是客户端
    「接口选择」页看到的那个次序（`jsm.sites` 数组顺序，已含巡检后的延迟重排
    与用户手动排序）。原来按「失效优先」排序，看起来像另一份清单，对不上号。
    需要老行为时传 `order_by="status"`。
    隐藏的接口（用户点了「隐藏」）不在下发配置里，这里从基线补齐并标
    `hidden=True` —— 不补的话用户隐藏完就找不回来。
    """
    source = "given"
    if jsm is None:
        jsm, source = _load_jsm()
    hstate = state if state is not None else (store.load(C.F_HEALTH_STATE, {}) or {})
    need = max(1, int(getattr(C, "OFFLINE_CONFIRM_ROUNDS", 2)))
    mark_enabled = bool(getattr(C, "MARK_NAMES", False))
    try:
        from . import siteorder
        so = siteorder.load()
        manual_keys = set(so.get("order") or [])
        hidden_keys = set(so.get("hidden") or [])
    except Exception:
        manual_keys, hidden_keys = set(), set()

    items = []
    seen = set()
    for s in (jsm.get("sites") or []):
        if not isinstance(s, dict):
            continue
        it = _site_item(s, pool, hstate, need, mark_enabled, manual_keys, hidden_keys)
        seen.add(str(it.get("key") or ""))
        items.append(it)

    # 补齐隐藏项：用户隐藏后它就不在 jsm 里了，得从基线取定义才能列出来 / 取消隐藏
    missing_hidden = [k for k in hidden_keys if k and k not in seen]
    if missing_hidden:
        by_key = {}
        for s in _baseline_sites():
            if isinstance(s, dict):
                by_key[str(s.get("key") or "")] = s
        for k in missing_hidden:
            s = by_key.get(k)
            if not s:
                # 基线里也没有（换包后该站被作者删了）：仍需可见，避免隐藏名单
                # 里留一条永远无法操作的幽灵项
                items.append({"key": k, "name": "(基线里已不存在)", "hidden": True,
                              "manual": k in manual_keys, "pinned": "", "marked": False,
                              "stale_mark": False, "tag": "", "tag_src": "", "base": "",
                              "api": "", "type": None, "proxy": "", "status": "unknown",
                              "streak": 0, "usable": 0, "alive": 0, "total": 0,
                              "best": "", "latency": 0, "note": "", "checked": 0})
                continue
            items.append(_site_item(s, pool, hstate, need, mark_enabled,
                                    manual_keys, hidden_keys))

    if order_by == "status":
        # 老口径：失效在最前，其次有疑问的，再按名字 —— 便于「一眼看到有问题的」
        rank = {"offline": 0, "watch": 1, "guard": 2, "unknown": 3, "ok": 4, "local": 5}
        items.sort(key=lambda x: (rank.get(x["status"], 9), x["key"] or ""))
    else:
        # 「实时接口顺序」= 下行顺序；隐藏项（不在下行里）统一挂到末尾。
        # 稳定排序：只把 hidden 挑到后面，其余保持 jsm 数组原样次序。
        items.sort(key=lambda x: 1 if x.get("hidden") else 0)
    for i, it in enumerate(items, 1):
        it["idx"] = i

    ok = sum(1 for x in items if x["status"] == "ok")
    offline = sum(1 for x in items if x["status"] == "offline")
    watch = sum(1 for x in items if x["status"] == "watch")
    guard = sum(1 for x in items if x["status"] == "guard")
    unknown = sum(1 for x in items if x["status"] == "unknown")
    local = sum(1 for x in items if x["status"] == "local")
    marked = sum(1 for x in items if x["marked"])
    stale = sum(1 for x in items if x["stale_mark"])
    hidden_n = sum(1 for x in items if x.get("hidden"))
    return {
        "total": len(items),
        "visible": len(items) - hidden_n,
        "hidden": hidden_n,
        "ok": ok,
        "offline": offline,
        "watch": watch,
        "guard": guard,
        "unknown": unknown,
        "local_sites": local,
        "marked": marked,
        "stale_mark": stale,
        "mark_enabled": mark_enabled,
        "order_by": order_by,
        "manual_order": len(manual_keys),
        "source": source,           # 明细取自哪一份配置：served / dist / baseline
        "pool": health.summary(pool),
        "endpoints": endpoints(),
        "confirm_rounds": need,
        "updated": int(time.time()),
        "items": items,
    }
