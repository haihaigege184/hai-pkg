"""渲染口径（jsm.json 里的 /gw/<TAG>/ 网关地址）—— 可动态选择，但默认完全不动

先说清「渲染口径」到底是什么、能影响什么
────────────────────────────────────────
`config.PUBLIC_BASE`（= `OKTV_PUBLIC_BASE` 第一项）只干一件事：
**渲染 jsm.json 里 `/gw/<TAG>/` 网关地址时拼在前面用的基址**。
而那只在 `patch.build(mode="gateway"|"dual")` 时才写进配置。

**当前生产是 `mode=direct`，网关备份 0 条** ⇒ 下发的 jsm.json 里
一个 `/gw/` 地址都没有，一个 `_GW` 站点都没有（`grep -c '_GW'` = 0）。
所以今天不管这个基址是 CF 还是 gost、是快是慢，
**电视拉配置、点播都不经过它，对速度零影响**。

真正决定电视快慢的是**客户端里填的那个配置地址**
（实测内网 4ms / CF 1.7s / gost 1.6s），那由 `core/endpoints.py` 的清单
与用户的客户端决定，与本模块无关。本模块不碰那条链路。

为什么默认「不动」
────────────────
这个地址一旦被自动改写，就会：
  1. 让已下发到客户端的配置里的 `/gw/<TAG>/` 指向另一个域名，而**端会缓存**；
  2. 挑「最快」的逻辑天然会抖动 —— 隧道延迟是长尾分布，两条轮流领先，
     下发的配置就每轮换一次域名，客户端看到的是「接口一会儿好一会儿不好」；
  3. 触碰 `core_ok` 红线的敏感面（ext 里的 baseURL 属于「预期修复动作」，
     频繁变动会让「改写条数」虚高，掩盖真正的失效修复）。
所以：**默认 `OKTV_RENDER_PICK=0`，行为与引入本模块之前逐字一致。**

三档模式
────────
  0 = 关闭（默认）。渲染口径恒等于 `OKTV_PUBLIC_BASE` 第一项。
  1 = 故障切换。只在当前这条**连续 N 轮测不通**时才换，不看谁更快。
  2 = 最快优先。在 1 的基础上，若另一条**连续 N 轮**是最快且优势 >10% 才换。

任何档位都受同一条保护：**候选只能来自 `PUBLIC_BASES` 与 endpoints 清单**，
永远不会切到一个「清单里没有、用户没配过」的地址。
"""
import time

from . import config as C
from . import store

# 优势阈值：新的候选要比当前快这么多（毫秒）才值得换，防抖动
_SWITCH_MARGIN_MS = float(getattr(C, "RENDER_SWITCH_MARGIN_MS", 120))


def _default_base():
    return (getattr(C, "PUBLIC_BASE", "") or "").rstrip("/")


def candidates():
    """允许成为渲染口径的地址集合。

    来源只有两处：环境变量 PUBLIC_BASES（公网入口）+ endpoints 清单。
    **内网地址被排除在自动选择之外** —— 自动模式只在用户显式开启时生效，
    而把网关地址指向内网意味着外网客户端全部打不通；只有用户手动 pin 才允许。
    """
    out = []
    for u in (getattr(C, "PUBLIC_BASES", None) or []):
        u = (u or "").rstrip("/")
        if u:
            out.append(u)
    try:
        from . import endpoints as EP
        for r in EP.list_ordered():
            u = (r.get("url") or "").rstrip("/")
            # lan 除非用户手动 pin，否则不参与自动选择
            if u and r.get("kind") != "lan" and u not in out:
                out.append(u)
    except Exception:
        pass
    return out


def load():
    """读当前生效状态。文件缺失/损坏一律回落到默认基址。"""
    d = store.load(getattr(C, "F_RENDER_BASE", ""), None) if getattr(
        C, "F_RENDER_BASE", "") else None
    if not isinstance(d, dict):
        d = {}
    base = (str(d.get("base") or "").rstrip("/"))
    if base and base in candidates():
        d["base"] = base
    else:
        # 记录里的地址已不在候选内（用户删了清单条目）→ 回落默认
        d["base"] = _default_base()
    d.setdefault("mode", int(C.RENDER_PICK))
    d.setdefault("auto", False)
    d.setdefault("manual", False)
    d.setdefault("streak_bad", 0)
    d.setdefault("streak_best", 0)
    d.setdefault("changed", 0)
    d.setdefault("changed_at", 0)
    d.setdefault("last_reason", "")
    return d


def save(d):
    try:
        store.save(C.F_RENDER_BASE, d)
    except Exception as e:
        store.log_event("error", "渲染口径落盘失败: %s: %s" % (type(e).__name__, e))


def current():
    """**全代码路径唯一应调用的函数**：当前生效的渲染口径。

    刻意做成「读一次就够」——渲染在请求路径之外的后台线程里，
    每 45s 一次，读一个小 JSON 无所谓。
    """
    return load()["base"] or _default_base()


def mode():
    d = load()
    return 1 if d.get("manual") else int(d.get("mode") or 0)


def set_manual(url, reason="manual"):
    """手动指定（pin）。传空字符串 = 交回自动/默认。

    注意：交回时落盘的必须是**解析后的默认基址**，不能是空串 ——
    空串会让 load() 每次都要回落到 default，虽然结果一样，
    但落盘文件看着像「没配」，排查时容易误判。
    """
    u = (url or "").rstrip("/")
    if u and u not in candidates():
        return {"ok": False, "error": "这条地址不在候选清单里（PUBLIC_BASES + 接口状态清单）"}
    d = load()
    old = d["base"]
    d["base"] = u or _default_base() or old
    d["manual"] = bool(u)
    d["streak_bad"] = 0
    d["streak_best"] = 0
    d["changed"] = int(d.get("changed") or 0) + (0 if old == d["base"] else 1)
    d["changed_at"] = int(time.time())
    d["last_reason"] = reason
    save(d)
    if old != d["base"]:
        store.log_event("render", "渲染口径改为 %s（%s）" % (d["base"], reason))
    return {"ok": True, "state": d}


def _pick_from_bench(bench):
    """从测速结果里挑出「本轮最该用的」地址与理由。"""
    rows = [x for x in (bench.get("rows") or []) if x.get("ok")]
    if not rows:
        return "", "全部入口不可用"
    best = rows[0]
    return best.get("url") or "", "最快（jsm %sms / pg.jar %sms）" % (
        best.get("jsm_ms"), best.get("big_ms"))


def decide(bench=None):
    """按当前档位决定是否换渲染口径。返回变化描述（没变则 changed=False）。

    刻意**不做**「每隔一小时看看谁最快就换」——
    抖动会让下发的配置自己翻（与 domains 那套「保持上次修复」是同一个教训）。
    """
    d = load()
    if d.get("manual"):
        return {"changed": False, "reason": "已手动指定", "state": d}
    m = int(d.get("mode") or 0)
    if m <= 0:
        return {"changed": False, "reason": "自动择优未开启(OKTV_RENDER_PICK=0)", "state": d}
    if bench is None:
        from . import epbench
        bench = epbench.latest()

    cur = d["base"]
    rows_by_url = {x.get("url"): x for x in (bench.get("rows") or [])}
    cur_row = rows_by_url.get(cur)

    # ---- 档位 1：故障切换 ----
    cur_bad = (cur_row is None) or (not cur_row.get("ok"))
    if cur_bad:
        d["streak_bad"] = int(d.get("streak_bad") or 0) + 1
    else:
        d["streak_bad"] = 0

    if d["streak_bad"] >= max(1, int(getattr(C, "RENDER_FAILOVER_ROUNDS", 3))):
        cand, why = _pick_from_bench(bench)
        if cand and cand != cur:
            d.update({"base": cand, "streak_bad": 0, "streak_best": 0,
                      "changed": int(d.get("changed") or 0) + 1,
                      "changed_at": int(time.time()),
                      "last_reason": "故障切换：当前连续 %d 轮不可用 → %s（%s）"
                                     % (max(1, int(getattr(C, "RENDER_FAILOVER_ROUNDS", 3))),
                                        cand, why)})
            save(d)
            store.log_event("render", d["last_reason"])
            return {"changed": True, "reason": d["last_reason"], "state": d}
        d["streak_bad"] = 0
        save(d)
        return {"changed": False, "reason": "当前不可用，但没有可用候选，保持 %s" % cur,
                "state": d}

    # ---- 档位 2：最快优先（带滞回）----
    if m >= 2 and cur_row:
        cand, why = _pick_from_bench(bench)
        c_row = rows_by_url.get(cand)
        if c_row and cand != cur:
            gain = (cur_row.get("jsm_ms") or 0) - (c_row.get("jsm_ms") or 0)
            if cand == _last_best_url(bench) and gain >= _SWITCH_MARGIN_MS:
                d["streak_best"] = int(d.get("streak_best") or 0) + 1
                if d["streak_best"] >= max(1, int(
                        getattr(C, "RENDER_FAILOVER_ROUNDS", 3))):
                    d.update({"base": cand, "streak_best": 0,
                              "changed": int(d.get("changed") or 0) + 1,
                              "changed_at": int(time.time()),
                              "last_reason": "最快优先：%s 比当前快 %dms（%s）"
                                             % (cand, gain, why)})
                    save(d)
                    store.log_event("render", d["last_reason"])
                    return {"changed": True, "reason": d["last_reason"], "state": d}
            else:
                d["streak_best"] = 0
        else:
            d["streak_best"] = 0
    save(d)
    return {"changed": False, "reason": "保持 %s" % cur, "state": d}


def _last_best_url(bench):
    return (bench.get("best") or "").rstrip("/")


def info():
    """给 /api/endpoints 与 /live 用的完整视图"""
    d = load()
    return {
        "render_base": d["base"],
        "default_base": _default_base(),
        "is_default": d["base"] == _default_base(),
        "manual": bool(d.get("manual")),
        "mode": int(d.get("mode") or 0),
        "mode_label": {0: "关闭（恒用 OKTV_PUBLIC_BASE 第一项）",
                       1: "仅故障切换",
                       2: "最快优先（含滞回）"}.get(int(d.get("mode") or 0), "?"),
        "streak_bad": int(d.get("streak_bad") or 0),
        "streak_best": int(d.get("streak_best") or 0),
        "changed": int(d.get("changed") or 0),
        "changed_at": int(d.get("changed_at") or 0),
        "last_reason": d.get("last_reason") or "",
        "candidates": candidates(),
        "affects": _affects_note(),
    }


def _affects_note():
    """一句话说清「这个地址当前到底影响什么」——直接读真实渲染模式，不猜。"""
    try:
        mode = store.get_state().get("mode") or "direct"
    except Exception:
        mode = "direct"
    if mode == "direct":
        return ("仅 gateway/dual 模式的 /gw/<TAG>/ 地址；当前 mode=direct，"
                "下发配置里没有网关地址，渲染口径不影响客户端速度")
    return "会写进下发的 jsm.json（当前 mode=%s）" % mode