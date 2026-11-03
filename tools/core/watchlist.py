"""我的追剧 —— 海报墙第一个类目

需求（2026-10-29 海哥）：海报墙第一类目是「我的追剧」，详情页要有「一键追剧」按钮。

为什么不放端上：
  app 的 JS 源跑在 QuickJS 里，拿不到 WebView 的 localStorage，也拿不到
  SharedPreferences（`req()` 只能发 HTTP）。端上只有 app 自己的收藏（KeepActivity），
  但它按「站点+id」存且只能从原生详情页点星标 —— 这个魔改版连详情页都没有。
  所以追剧列表放中枢，端和网页共用同一份。

存储：data/watchlist.json
  {"m1101412": {"vod_id":"m1101412","vod_name":"…","vod_pic":"…",
                "vod_remarks":"⭐8.5","vod_year":"2026","ts":1761700000.0}}

单用户场景（就海哥家这一台电视 + 手机网页），不做多账号隔离。
"""

from __future__ import annotations

import json
import os
import threading
import time

_LOCK = threading.RLock()
_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "data", "watchlist.json")

# 只保留这些字段，避免把整份详情（含几百 K 的播放源）灌进文件
_KEEP = ("vod_id", "vod_name", "vod_pic", "vod_year", "vod_remarks", "type_name")


def _load() -> dict:
    try:
        with open(_FILE, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _save(d: dict) -> None:
    os.makedirs(os.path.dirname(_FILE), exist_ok=True)
    tmp = _FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=1)
    os.replace(tmp, _FILE)      # 原子替换：断电/并发都不会留半个文件


def items() -> list:
    """追剧列表（最近追的在最前）"""
    with _LOCK:
        d = _load()
        rows = [v for v in d.values() if isinstance(v, dict) and v.get("vod_id")]
        rows.sort(key=lambda x: float(x.get("ts") or 0), reverse=True)
        return rows


def has(vid: str) -> bool:
    with _LOCK:
        return str(vid or "") in _load()


def add(vid: str, meta: dict | None = None) -> bool:
    vid = str(vid or "").strip()
    if not vid:
        return False
    with _LOCK:
        d = _load()
        row = {k: meta.get(k) for k in _KEEP if meta and meta.get(k) is not None}
        old = d.get(vid) or {}
        # 老记录里有、这次没传的字段保留（避免 add 时只传 id 把海报冲掉）
        for k in _KEEP:
            if not row.get(k) and old.get(k):
                row[k] = old[k]
        row["vod_id"] = vid
        row["ts"] = time.time()
        d[vid] = row
        _save(d)
        return True


def remove(vid: str) -> bool:
    vid = str(vid or "").strip()
    with _LOCK:
        d = _load()
        if vid not in d:
            return False
        d.pop(vid, None)
        _save(d)
        return True


def toggle(vid: str, meta: dict | None = None) -> bool:
    """切换追剧状态，返回切换后是否「已追」"""
    vid = str(vid or "").strip()
    if not vid:
        return False
    with _LOCK:
        if has(vid):
            remove(vid)
            return False
        add(vid, meta)
        return True


def clear() -> int:
    with _LOCK:
        d = _load()
        n = len(d)
        _save({})
        return n
