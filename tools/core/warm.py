"""海报墙的内存索引 + 后台预热（2026-11-03）

海哥的需求：*「能否预先扫描各类目剧集列表与季集数据做成文本清单动态加载到
内存中，需要时实时从内存检索读取，有新数据时动态新增」*

可行性结论（先算账，再动手）：

| 数据 | 单次成本 | 能不能预扫 | 做法 |
|---|---|---|---|
| 类目列表（7 类 × 2 源 × 前 2 页 = 28 次） | 0.3~1.2s/次 | ✅ 能 | 后台线程每 12 分钟刷一遍，结果留在内存 |
| 详情元数据（海报/简介/演职员） | 0.3~0.8s/部 | ✅ 有限度 | 每类前 20 部预热，命中自己的 6~12h 缓存 |
| 选集/季集（网盘三站聚合） | **20~40s/部** | ❌ 绝对不能 | 只在焦点停在卡片上时排队预热一部，串行 |

所以「全集预扫描」是不做的 —— 7 类 × 20 部 × 30 秒 ≈ 70 分钟纯串行，还会把
三个采集站打到限流。真正能带来手感提升的是前两层，加上「用户快要点到的那一部」
按需预热。

设计原则：**只做缓存加速，不改变任何判定逻辑**。
  * 列表：调 `list_page` 一遍，模块自己的缓存就热了，本模块额外留一份
    `LISTS` 供 `/hai/poster/warm` 查状态；
  * 元数据：`detail(vid, with_plays=False)`；
  * 选集：通过 `set_eps_loader()` 注入 app 侧的实现（避免循环 import），
    串行队列 + 去重 + 上限，队列满了直接丢新请求（预热永远不该抢正事的带宽）。
"""

import threading
import time

from . import tmdbwall as T

# 预热周期（秒）：12 分钟刷一遍列表。TMDB 自己的缓存是 30 分钟，这里更勤一点，
# 保证用户看到的类目内容不会「一小时后才更新」。
LIST_EVERY = 12 * 60
# 详情元数据预热：每类只做前 N 部。20 部 × 7 类 = 140 次请求，按时限流跑。
META_PER_CLASS = 20
# 选集预热队列上限
EPS_QMAX = 6

LISTS = {}          # (src, tid, page) -> {"ts": float, "n": int, "first": str}
_state = {
    "started": 0.0,
    "last_list": 0.0,
    "list_runs": 0,
    "meta_done": 0,
    "eps_done": 0,
    "eps_fail": 0,
    "last_error": "",
}
_lock = threading.Lock()
_eps_lock = threading.Lock()
_eps_q = []         # [(vid, title)] 待预热
_eps_seen = {}      # vid -> ts（同一部 20 分钟内不重复排）
_eps_loader = None  # 由 app 注入：fn(vid) -> dict


def set_eps_loader(fn):
    """注入选集聚合实现（app._eps_payload），避免 core 反过来 import app。"""
    global _eps_loader
    _eps_loader = fn


# ---------------------------------------------------------------------------
# 类目列表预热
# ---------------------------------------------------------------------------

def _classes():
    """(src, tid) 组合。tid 两套数据源是对齐的，见 doubanx 注释。"""
    out = []
    for cid, _name, path, _extra in T.CLASSES:
        if path is None:          # 我的追剧：本地文件，不用预热
            continue
        out.append(("tmdb", cid))
        out.append(("douban", cid))
    return out


def warm_lists(pages=(1, 2)) -> int:
    """把所有类目的前 N 页过一遍。返回成功条数。

    串行而不是并发：TMDB 免费 key 有速率限制（约 4~5 次/秒），并发只会
    换来 429，反而更慢。
    """
    from . import posterweb as P
    n = 0
    for src, tid in _classes():
        M = P._mod(src)
        for pg in pages:
            try:
                data = M.list_page(P._cid_of(src, tid), pg)
                items = (data or {}).get("list") or []
                with _lock:
                    LISTS[(src, tid, pg)] = {
                        "ts": time.time(), "n": len(items),
                        "first": (items[0] or {}).get("vod_name") if items else "",
                    }
                n += 1
            except Exception as e:                    # 单类失败不影响其它类
                with _lock:
                    _state["last_error"] = "%s/%s/p%d %s" % (src, tid, pg, e)
            time.sleep(0.15)
    with _lock:
        _state["last_list"] = time.time()
        _state["list_runs"] += 1
    return n


def warm_meta():
    """每类前 META_PER_CLASS 部的元数据（海报墙第一屏的 hero 就在里面）。"""
    from . import posterweb as P
    for src, tid in _classes():
        if src != "tmdb":         # 豆瓣详情和 TMDB 是不同接口，先只热主源
            continue
        row = LISTS.get((src, tid, 1)) or {}
        M = P._mod(src)
        try:
            data = M.list_page(P._cid_of(src, tid), 1)
        except Exception:
            continue
        for it in ((data or {}).get("list") or [])[:META_PER_CLASS]:
            vid = str(it.get("vod_id") or "")
            if not vid:
                continue
            try:
                M.detail(vid, with_plays=False)
                with _lock:
                    _state["meta_done"] += 1
            except Exception:
                pass
            time.sleep(0.12)


# ---------------------------------------------------------------------------
# 选集（按需，绝不预扫）
# ---------------------------------------------------------------------------

def warm_vid(vid: str, title: str = "") -> bool:
    """把一部片的选集聚合排进后台队列（用户焦点停在这张卡上时调）。"""
    vid = str(vid or "").strip()
    if not vid or _eps_loader is None:
        return False
    now = time.time()
    with _eps_lock:
        if _eps_seen.get(vid, 0) > now - 20 * 60:
            return False                      # 20 分钟内已经热过
        if any(v == vid for v, _t in _eps_q):
            return False
        if len(_eps_q) >= EPS_QMAX:
            return False                      # 队列满 → 丢，预热不抢带宽
        _eps_q.append((vid, title))
        _eps_seen[vid] = now
    return True


def _eps_worker():
    while True:
        item = None
        with _eps_lock:
            if _eps_q:
                item = _eps_q.pop(0)
        if not item:
            time.sleep(0.6)
            continue
        vid, _title = item
        try:
            _eps_loader(vid)
            with _lock:
                _state["eps_done"] += 1
        except Exception as e:
            with _lock:
                _state["eps_fail"] += 1
                _state["last_error"] = "eps %s: %s" % (vid, e)


# ---------------------------------------------------------------------------
# 启动
# ---------------------------------------------------------------------------

def _loop():
    # 起来先让服务把首屏请求处理完，再开始占带宽
    time.sleep(6)
    while True:
        try:
            warm_lists()
            warm_meta()
        except Exception as e:
            with _lock:
                _state["last_error"] = "loop: %s" % e
        time.sleep(LIST_EVERY)


def start():
    """幂等启动：多 worker 各跑各的也没问题（都是只读缓存）。"""
    if _state["started"]:
        return False
    _state["started"] = time.time()
    threading.Thread(target=_loop, daemon=True, name="hai-warm-list").start()
    threading.Thread(target=_eps_worker, daemon=True, name="hai-warm-eps").start()
    return True


def stats() -> dict:
    with _lock:
        out = dict(_state)
    with _eps_lock:
        out["eps_queue"] = len(_eps_q)
    out["lists"] = len(LISTS)
    return out
