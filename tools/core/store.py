"""JSON 持久化 / 事件日志"""
import json
import os
import time
import threading

from . import config as C

_lock = threading.RLock()


def _ensure():
    for d in (C.DATA_DIR, C.DIST_DIR, C.LOG_DIR):
        os.makedirs(d, exist_ok=True)


def load(path, default=None):
    _ensure()
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save(path, obj):
    """原子写：先写临时文件再 rename，避免读到半个文件"""
    _ensure()
    with _lock:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)


def log_event(kind, msg, **extra):
    _ensure()
    rec = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "kind": kind, "msg": msg}
    if extra:
        rec.update(extra)
    try:
        with _lock:
            with open(C.F_LOG, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            # 控制体积：超过 4000 行则截断保留后 1500 行
            if os.path.getsize(C.F_LOG) > 2_000_000:
                with open(C.F_LOG, "r", encoding="utf-8") as f2:
                    lines = f2.readlines()[-1500:]
                with open(C.F_LOG, "w", encoding="utf-8") as f2:
                    f2.writelines(lines)
    except Exception:
        pass


def tail_events(n=200):
    _ensure()
    if not os.path.exists(C.F_LOG):
        return []
    with open(C.F_LOG, "r", encoding="utf-8") as f:
        lines = f.readlines()[-n:]
    out = []
    for ln in lines:
        try:
            out.append(json.loads(ln))
        except Exception:
            continue
    return list(reversed(out))


def get_state():
    return load(C.F_STATE, {"last_sync": 0, "last_health": 0, "mode": "direct",
                            "version": "1.0.0"})


def set_state(**kw):
    st = get_state()
    st.update(kw)
    save(C.F_STATE, st)
    return st
