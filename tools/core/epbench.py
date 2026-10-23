"""对外入口地址测速

回答一个问题：**客户端该把配置地址填成哪一条？**

为什么必须自己测，而不是靠「内网 → FRP → CF」的固定排序
────────────────────────────────────────────────
固定排序是**结构假设**（同网段就不该绕穿透），但真实瓶颈有两个互相矛盾的维度：

  * **往返 + 首字节**（决定「打开 App 那一瞬间」的体感）：
    38KB 的 jsm.json 走 CF 隧道约 1.7s，走 gost 约 1.6s，走内网 4ms。
  * **带宽**（决定 4.6MB 的 pg.jar 要下多久）：
    实测同三条 CF 11.8s / gost 15.1s / 另一条 gost 36.5s。

而**同一条地址在两个维度上的排名可以是反的**：pg.gl-hk1 拉 jsm.json 最快
（0.94s），拉 pg.jar 却最慢（36.5s）。只测小文件就会把它误判成最优，
客户端换过去以后拉包反而从 12s 变成 37s。所以这里**两个维度都测，分开报**，
最终排序用「加权总分」而不是单一指标。

测法
────
* 串联，不并发 —— 并发会自己把出口挤爆，测出来的延迟全是假的
  （与 health.check_all 的教训同源）。
* 每条取 `EPB_ROUNDS` 轮**中位数**。穿透链路的延迟分布是长尾的，
  用均值会被偶发的一次 3s 拉偏，用中位数稳。
* pg.jar 用 `Range: bytes=0-N` 只拉 1MB：不下载全文件，也不给链路造成压力。
* 判「这条可用」的门槛：jsm.json 必须 200 且体积与本地那份同一量级
  （防止某个入口返回 CF 挑战页/错误页也算成功）。
"""
import time

import os

import requests
import urllib3

from . import config as C
from . import endpoints as EP
from . import store

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

_session = None

# 带宽探针的独立超时（秒）。比整体超时短：慢隧道拉 1MB 可能 30s+，
# 而带宽只要个量级，超时按「测不出」处理即可。
EPB_BIG_TIMEOUT = int(os.environ.get("OKTV_EPB_BIG_TIMEOUT", "6"))


def _sess():
    global _session
    if _session is None:
        _session = requests.Session()
        _session.headers.update({"User-Agent": C.UA})
    return _session


def _median(xs):
    xs = sorted(x for x in xs if x is not None and x >= 0)
    if not xs:
        return None
    n = len(xs)
    return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) // 2


def _probe_jsm(base, attempts=None):
    """测小文件：往返 + 首字节。返回 (ok, ms, code, nbytes, note)

    **必须重试** —— 实测 gost 隧道（hai.gl-hk1 / pg.gl-hk1）会抖动：
    同一个 URL 连打 8 次，偶发一次 HTTP 404 或 RemoteDisconnected。
    单次失败就判「这条入口不可用」⇒ 自动择优会误触发故障切换，
    配置来回翻（与 health.check_all 那个「假失效」是同一类错误）。
    所以：只有**连续 attempts 次全失败**才算不可用。
    """
    attempts = max(1, int(attempts or C.EPB_ATTEMPTS))
    last = (False, 0, 0, 0, "")
    for i in range(attempts):
        url = base.rstrip("/") + C.EPB_JSM_PATH
        t0 = time.time()
        try:
            r = _sess().get(url, timeout=C.EPB_TIMEOUT, verify=False,
                            allow_redirects=True)
            dt = int((time.time() - t0) * 1000)
            n = len(r.content or b"")
            if r.status_code != 200:
                last = (False, dt, r.status_code, n, "HTTP %d" % r.status_code)
                continue
            if n < 2000:
                last = (False, dt, r.status_code, n, "响应体过小(%dB)，疑似错误页" % n)
                continue
            return True, dt, r.status_code, n, ""
        except Exception as e:
            last = (False, int((time.time() - t0) * 1000), 0, 0,
                    "%s: %s" % (type(e).__name__, str(e)[:60]))
    return last


def _probe_big(base):
    """测带宽：Range 拉 EPB_BIG_BYTES。返回 (ok, ms, bytes, B_per_s, note)

    单独一个较短的超时（EPB_BIG_TIMEOUT）：慢隧道拉 1MB 可能要 30s+，
    而我们只是要个量级，超时按「测不出带宽」处理，不该把整轮测速拖成分钟级。
    """
    url = base.rstrip("/") + C.EPB_BIG_PATH
    hdr = {"Range": "bytes=0-%d" % (C.EPB_BIG_BYTES - 1)}
    t0 = time.time()
    try:
        r = _sess().get(url, timeout=EPB_BIG_TIMEOUT, verify=False,
                        allow_redirects=True, headers=hdr,
                        stream=True)
        n = 0
        for chunk in r.iter_content(65536):
            n += len(chunk)
        dt = time.time() - t0
        if r.status_code not in (200, 206):
            try:
                r.close()
            except Exception:
                pass
            return False, int(dt * 1000), n, 0, "HTTP %d" % r.status_code
        try:
            r.close()
        except Exception:
            pass
        if n < 4096:
            return False, int(dt * 1000), n, 0, "Range 未生效或响应过小"
        return True, int(dt * 1000), n, int(n / max(dt, 0.001)), ""
    except Exception as e:
        return False, int((time.time() - t0) * 1000), 0, 0, \
            "%s: %s" % (type(e).__name__, str(e)[:60])


def _score(row):
    """加权总分，越小越好。None = 不可用。

    权重取「体感占比」：客户端一次典型动作 = 拉 jsm.json（等它）+
    拉 pg.jar（等它）。pg.jar 大 100 倍但客户端是流式用，感知上
    jsm 的等待更尖锐，所以给 jsm 更高权重。

    带宽测不出（big_ms=None）时**只按 jsm 计分**，不判它不可用 ——
    入口能不能用是 jsm 说了算（见 bench_one 的说明）。
    """
    if not row.get("ok"):
        return None
    j = row.get("jsm_ms")
    if j is None:
        return None
    b = row.get("big_ms")
    # pg.jar 用「等效耗时」折算：把 1MB 测速线性外推到 4.6MB 太粗，
    # 这里只取 1MB 耗时 × 3 作为带宽惩罚项，够区分量级即可。
    penalty = (b if b is not None else 0) * 3 / 1000.0
    return round(j + penalty, 1)


def bench_one(base, rounds=None):
    """测单条地址。

    **两条探针的成败是分开的**：
      · jsm.json 探不通 = 这条入口**不可用**（客户端拉不到配置）；
      · pg.jar 探不通 = 只是**测不出带宽**（慢/超时），入口本身仍可能能用。
    早先把两者混在一个 note 里，结果「带宽测超时」被当成「入口不可用」，
    会被自动择优误判成死链 —— 这是必须分开的原因。

    **轮次分配**：jsm 探针是「能不能用」的判据，必须跑满 rounds 轮
    （单轮抖动是隧道常态）；pg.jar 只是带宽参考，慢链路上 1MB 可能要
    20s+ ⇒ 只跑 1 轮就够，2 轮纯属浪费两分钟。
    """
    rounds = max(1, int(rounds or C.EPB_ROUNDS))
    js, bs, bps = [], [], []
    code = 0
    nbytes = 0
    jnote = ""
    bnote = ""
    jok_hits = 0
    for i in range(rounds):
        ok, ms, cd, n, nt = _probe_jsm(base)
        if ok:
            jok_hits += 1
            js.append(ms)
            code = cd
            nbytes = max(nbytes, n)
        else:
            js.append(ms)
            if nt:
                jnote = nt
        if i == 0:
            # 带宽只测一轮
            ok2, ms2, _n2, sp, nt2 = _probe_big(base)
            if sp:
                bps.append(sp)
                bs.append(ms2)
            else:
                bs.append(ms2)
                if nt2:
                    bnote = nt2
        time.sleep(0.25)
    # 只有**每一轮都失败**才算不可用（单轮抖动是隧道常态）
    jok = jok_hits >= 1
    bok = bool(bps)
    return {
        "url": base,
        "ok": bool(jok),                       # 入口能不能用，看 jsm
        "jsm_ms": _median(js) if jok else None,
        "jsm_note": "" if jok else jnote,
        "jsm_hits": "%d/%d" % (jok_hits, rounds),
        "big_ms": _median(bs) if bok else None,
        "big_bps": _median(bps) if bok else None,
        "big_note": "" if bok else bnote,
        "code": code,
        "bytes": nbytes,
        "note": "" if jok else jnote,
    }


def run(rounds=None, only=None):
    """测速全部入口（内网 + 清单里的公网），落盘并返回排行。

    only 给定时只测这一条（renderbase 的 failover 二审用）。
    """
    rows = EP.list_ordered()
    if only:
        rows = [r for r in rows if r["url"].rstrip("/") == only.rstrip("/")]
    out = []
    for r in rows:
        row = bench_one(r["url"], rounds=rounds)
        row["kind"] = r.get("kind") or ""
        row["label"] = r.get("label") or ""
        row["jsm"] = r.get("jsm") or ""
        row["_i"] = len(out)
        out.append(row)
    for row in out:
        row["score"] = _score(row)
        row.pop("_i", None)

    # 排序：可用的按 score 升序在前，不可用沉底（保持清单原序，便于比对）
    good = sorted([x for x in out if x.get("score") is not None],
                  key=lambda x: x["score"])
    bad = [x for x in out if x.get("score") is None]
    ranked = good + bad

    best = ranked[0]["url"] if good else ""
    payload = {
        "checked": int(time.time()),
        "best": best,
        "rows": ranked,
        "jsm_path": C.EPB_JSM_PATH,
        "big_path": C.EPB_BIG_PATH,
    }
    try:
        store.save(C.F_EPBENCH, payload)
    except Exception as e:
        store.log_event("error", "入口测速落盘失败: %s: %s" % (type(e).__name__, e))
    return payload


def latest():
    """读上次测速结果（没有就现测一次）"""
    d = store.load(C.F_EPBENCH, None)
    if not d or not d.get("checked"):
        return run()
    return d


def summary_line():
    """给通知用的一行摘要：最快那条 + 它的两个实测数"""
    try:
        d = latest()
    except Exception:
        return ""
    rows = [x for x in (d.get("rows") or []) if x.get("ok")]
    if not rows:
        return "入口测速：全部不可用"
    b = rows[0]
    bps = b.get("big_bps") or 0
    return ("入口测速：最快 %s（jsm %sms / pg.jar %sms，%.0f KB/s）"
            % (b.get("url"), b.get("jsm_ms"), b.get("big_ms"), bps / 1024.0))