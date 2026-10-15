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
from . import health
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
    """全部对外入口（主地址在前）。

    主地址取**当前生效**的 C.PUBLIC_BASE（patch 可能在运行期改写它，例如把
    网关地址指向另一条隧道），其余入口来自 C.PUBLIC_BASES 的配置顺序。
    """
    out = []
    cur = (getattr(C, "PUBLIC_BASE", "") or "").rstrip("/")
    if cur:
        out.append(cur)
    for u in (getattr(C, "PUBLIC_BASES", None) or []):
        u = (u or "").rstrip("/")
        if u and u not in out:
            out.append(u)
    return out


def endpoints():
    """客户端该填的接口地址（局域网 / 外网，外网可能有多条）"""
    lan_base = (C.LAN_BASE or "").rstrip("/")
    if not lan_base:
        ip = lan_ip()
        if ip:
            lan_base = "http://%s:%d" % (ip, C.PORT)
    pubs = public_bases()
    join = lambda b: (b + "/hai/jsm.json") if b else ""   # noqa: E731
    return {
        "lan": join(lan_base),
        "public": join(pubs[0]) if pubs else "",
        # 多条外网入口（如 CF 隧道 + 自建 gost 穿透），供命令里逐条展示
        "public_list": [join(u) for u in pubs],
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


def sites_report(pool, jsm=None, state=None):
    """生成站点级明细。jsm 为空时自动按 served → dist → baseline 取。"""
    source = "given"
    if jsm is None:
        jsm, source = _load_jsm()
    hstate = state if state is not None else (store.load(C.F_HEALTH_STATE, {}) or {})
    need = max(1, int(getattr(C, "OFFLINE_CONFIRM_ROUNDS", 2)))
    mark_enabled = bool(getattr(C, "MARK_NAMES", False))

    items = []
    for s in (jsm.get("sites") or []):
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

        marked = "⚠️失效" in name
        items.append({
            "key": key,
            "name": name,
            "marked": marked,           # 配置里是否已被打上失效标注
            # 标注与实际不符：标了失效但其实有可用地址（用户 3 号反馈的场景）
            "stale_mark": bool(marked and best),
            "tag": tag,
            "tag_src": tag_src,         # ext=来自 ext 尾部 TAG；api=用 api 兜底
            "base": base,               # ext 里的远程站根（空=内置站点）
            "api": s.get("api") or "",
            "proxy": (info or {}).get("proxy") or "",
            "status": status,
            "streak": streak,
            "usable": usable,
            "alive": alive,
            "total": total,
            "best": (best or {}).get("url") or "",
            "latency": (best or {}).get("latency") or 0,
            "note": (best or {}).get("note") or "",
            "checked": ts,
        })

    # 排序：失效在最前，其次有疑问的，再按名字 —— 便于「一眼看到有问题的」
    rank = {"offline": 0, "watch": 1, "guard": 2, "unknown": 3, "ok": 4, "local": 5}
    items.sort(key=lambda x: (rank.get(x["status"], 9), x["key"] or ""))

    ok = sum(1 for x in items if x["status"] == "ok")
    offline = sum(1 for x in items if x["status"] == "offline")
    watch = sum(1 for x in items if x["status"] == "watch")
    guard = sum(1 for x in items if x["status"] == "guard")
    unknown = sum(1 for x in items if x["status"] == "unknown")
    local = sum(1 for x in items if x["status"] == "local")
    marked = sum(1 for x in items if x["marked"])
    stale = sum(1 for x in items if x["stale_mark"])
    return {
        "total": len(items),
        "ok": ok,
        "offline": offline,
        "watch": watch,
        "guard": guard,
        "unknown": unknown,
        "local_sites": local,
        "marked": marked,
        "stale_mark": stale,
        "mark_enabled": mark_enabled,
        "source": source,           # 明细取自哪一份配置：served / dist / baseline
        "pool": health.summary(pool),
        "endpoints": endpoints(),
        "confirm_rounds": need,
        "updated": int(time.time()),
        "items": items,
    }
