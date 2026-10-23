"""接口顺序与显隐 —— 用户手动编辑的持久化层

背景
────
`core/order.py` 原来只有一条路：置顶项 + 其余按实测延迟升序，**全自动**。
用户 2026-10-22 提出：维护页要能**自己排**（上移/下移），也要能**把某个接口
藏起来**（不出现在客户端的「接口选择」列表里），并且**换 PG 包之后保持**。

所以把「用户意志」单独存一份，与「自动排序」解耦：

    data/site_order.json
    {
      "order":  ["csp_Config", "Wogg", ...],   # 手动顺序（只需部分，缺的按自动顺序插空）
      "hidden": ["SomeKey", ...],              # 隐藏：不下发给客户端
      "updated": 1759...
    }

三条设计约束
────────────
1. **不能破 `core_ok`**。隐藏一个站点 = jsm.sites 少一个 key，`verify.py` 的
   `missing_keys` 会非空 → 一致性直接判负。所以隐藏集合在这里是**唯一豁免凭据**：
   `verify` 只对「在这张名单里」的 key 放行，其他任何缺失照旧报错（红线不动）。
2. **换包不丢**。文件在 `data/` 下，`pkgupdate` 的严格镜像只作用于包目录
   （`PKG_DIR`），`data/` 不在其中 —— 但仍要实测确认（见交接记录）。
3. **顺序只是顺序**。不碰站点内容（name/api/ext 一字不改），与 `order.py` 的
   既有承诺一致；所以 manual 顺序同样不参与 `core_ok` 的 site 指纹比对。

数据格式对「key」的定义与别处一致：`sites[i].key`（字符串）。
"""
import os
import time

from . import config as C
from . import store


def _path():
    return getattr(C, "F_SITE_ORDER", os.path.join(C.DATA_DIR, "site_order.json"))


def _hist_path():
    """可恢复快照。教训：2026-10-23 凌晨一次「清理测试残留」的 reset
    把用户辛苦排的 78 条顺序和 2 个隐藏一起清掉了，端上重启也拉不回。
    从那以后任何一次 reset 都必须先留快照，且能一键撤销。"""
    return getattr(C, "F_SITE_ORDER_HIST",
                   os.path.join(C.DATA_DIR, "site_order.history.json"))


def _push_history(state, reason="", major=False):
    """压入改动前的状态。空状态不压（避免刷无用历史）。

    major=True 专指「重置」这类灾难性动作 —— undo 会优先找最近一条 major，
    这样用户点一次「撤销」就回到重置之前，而不是被中间几十次上移淹没。
    """
    if not (state.get("order") or state.get("hidden")):
        return
    d = store.load(_hist_path(), {}) or {}
    snaps = list(d.get("snapshots") or [])
    snap = {"at": int(time.time()), "reason": str(reason or "")[:60],
            "major": bool(major),
            "order": list(state.get("order") or []),
            "hidden": list(state.get("hidden") or [])}
    last = snaps[-1] if snaps else None
    if (last and not major and not last.get("major")
            and last.get("order") == snap["order"]
            and last.get("hidden") == snap["hidden"]):
        return                        # 与上一条一模一样就不重复压
    snaps.append(snap)
    # 容量控制：保留最近 10 条 major + 最近 20 条普通
    major_snaps = [x for x in snaps if x.get("major")][-10:]
    plain = [x for x in snaps if not x.get("major")][-20:]
    merged = sorted(major_snaps + plain, key=lambda x: x.get("at") or 0)
    store.save(_hist_path(), {"snapshots": merged})


def load():
    """读原始结构，永远返回完整字段（缺字段补空）"""
    d = store.load(_path(), {}) or {}
    if not isinstance(d, dict):
        d = {}
    order = [str(x) for x in (d.get("order") or []) if str(x or "").strip()]
    hidden = [str(x) for x in (d.get("hidden") or []) if str(x or "").strip()]
    # 去重但保留首次出现位置（手工编辑过的文件可能被写重复）
    order = list(dict.fromkeys(order))
    hidden = list(dict.fromkeys(hidden))
    return {"order": order, "hidden": hidden,
            "updated": int(d.get("updated") or 0)}


def save(obj, reason=""):
    cur = load()
    prev = {"order": list(cur["order"]), "hidden": list(cur["hidden"])}
    cur.update({k: v for k, v in (obj or {}).items() if k in ("order", "hidden")})
    cur["order"] = list(dict.fromkeys(cur["order"]))
    cur["hidden"] = list(dict.fromkeys(cur["hidden"]))
    if prev["order"] != cur["order"] or prev["hidden"] != cur["hidden"]:
        _push_history(prev, reason)      # 先留旧状态，再做不可逆的写入
    cur["updated"] = int(time.time())
    store.save(_path(), cur)
    return cur


def order():
    return load()["order"]


def hidden_keys():
    """隐藏集合（set）。给 order.py 过滤、verify.py 豁免用。"""
    return set(load()["hidden"])


def is_hidden(key):
    return str(key or "") in hidden_keys()


# ---------------------------------------------------------------------------
# 编辑动作
# ---------------------------------------------------------------------------

def move(key, direction, current):
    """把 key 在**当前实时顺序**里上移/下移一位，落盘并返回新顺序。

    `current` 必须是**完整的、当前下发的 key 顺序**（含 `*_GW` 克隆）。
    不做「只在本名单内交换」——用户看到的是完整列表，也必须按完整列表动，
    否则一个藏在中间的接口上下点不动（它跟相邻项交换后可能位置不变）。
    """
    key = str(key or "")
    cur = [str(k) for k in (current or [])]
    if key not in cur:
        return None
    i = cur.index(key)
    j = i - 1 if direction == "up" else i + 1
    if j < 0 or j >= len(cur):
        return cur                      # 已经在头/尾，静默无操作
    cur[i], cur[j] = cur[j], cur[i]
    save({"order": cur})
    store.log_event("order", "接口顺序：%s 向%s移动一位（第 %d 位）"
                    % (key, "上" if direction == "up" else "下", j + 1))
    return cur


def set_order(keys):
    """整体覆盖顺序（前端拖拽/批量提交用）"""
    return save({"order": [str(k) for k in (keys or [])]})["order"]


def hide(key):
    """隐藏：加入名单。已在名单里则幂等。"""
    key = str(key or "")
    if not key:
        return hidden_keys()
    d = load()
    if key not in d["hidden"]:
        d["hidden"].append(key)
        save({"hidden": d["hidden"]})
        store.log_event("order", "接口隐藏：%s 不再下发给客户端" % key)
    return hidden_keys()


def show(key):
    """取消隐藏"""
    key = str(key or "")
    d = load()
    if key in d["hidden"]:
        d["hidden"] = [k for k in d["hidden"] if k != key]
        save({"hidden": d["hidden"]})
        store.log_event("order", "接口恢复显示：%s" % key)
    return hidden_keys()


def toggle_hidden(key):
    return show(key) if is_hidden(key) else hide(key)


def reset(what="all"):
    """what = all | order | hidden

    重置是灾难性动作（一次点错就把几十条手排顺序清光），所以**先压 major 快照**
    再清空，`undo()` 能一键回到重置之前。
    """
    d = load()
    prev = {"order": list(d["order"]), "hidden": list(d["hidden"])}
    _push_history(prev, "重置（%s）之前" % what, major=True)
    if what in ("all", "order"):
        d["order"] = []
    if what in ("all", "hidden"):
        d["hidden"] = []
    d["updated"] = int(time.time())
    store.save(_path(), {"order": d["order"], "hidden": d["hidden"],
                         "updated": d["updated"]})
    store.log_event("order", "接口顺序/显隐已重置（%s），原状态已留快照可撤销" % what)
    return load()


def history():
    """给 UI 用：有多少可撤销记录、最近一次重置是什么时候"""
    d = store.load(_hist_path(), {}) or {}
    snaps = list(d.get("snapshots") or [])
    majors = [x for x in snaps if x.get("major")]
    last = majors[-1] if majors else (snaps[-1] if snaps else None)
    return {"count": len(snaps), "major_count": len(majors),
            "last_at": int((last or {}).get("at") or 0),
            "last_reason": str((last or {}).get("reason") or ""),
            "last_major": bool((last or {}).get("major")),
            "last_order": len((last or {}).get("order") or []),
            "last_hidden": len((last or {}).get("hidden") or [])}


def undo():
    """撤销：优先回到最近一次「重置之前」，没有重置记录则回退一步。

    恢复后该快照会被消费掉（避免重复撤销同一份），返回 None 表示无记录。
    """
    d = store.load(_hist_path(), {}) or {}
    snaps = list(d.get("snapshots") or [])
    if not snaps:
        return None
    idx = None
    for i in range(len(snaps) - 1, -1, -1):
        if snaps[i].get("major"):
            idx = i
            break
    if idx is None:
        idx = len(snaps) - 1
    snap = snaps.pop(idx)
    cur = load()
    cur["order"] = [str(x) for x in (snap.get("order") or [])]
    cur["hidden"] = [str(x) for x in (snap.get("hidden") or [])]
    cur["updated"] = int(time.time())
    store.save(_path(), cur)                  # 直接写，不再压快照
    store.save(_hist_path(), {"snapshots": snaps})
    store.log_event("order", "撤销修改：恢复 %s 的状态（顺序 %d 项 / 隐藏 %d 项）"
                    % (time.strftime("%m-%d %H:%M", time.localtime(snap.get("at") or 0)),
                       len(cur["order"]), len(cur["hidden"])))
    return cur


def summary():
    d = load()
    h = history()
    return {"order_count": len(d["order"]), "hidden_count": len(d["hidden"]),
            "updated": d["updated"], "undo_count": h["count"],
            "last_reset_at": h["last_at"] if h["last_major"] else 0}
