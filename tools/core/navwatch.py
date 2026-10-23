"""上游「站源」变动监视 —— 饭太硬导航页 + 各直连源接口地址

为什么需要它
────────────
用户 2026-10-22 提的诉求原话：

    上游更新时需包括检查**饭太硬导航中各站源域名**有无更新变化，
    解析**各站源中接口地址**有无更新变化。

饭太硬导航（`https://fty.888484.xyz/`）是这批站源的「发布台」：作者换域名、
换接口路径，第一时间改的是这个页面。页面结构极简，每个人一个
`data-clipboard-text`，就是该站当前该填的接口地址：

    <div class="inner copy-btn" id="qiaoer"
         data-clipboard-text="http://cdn.qiaoji8.com/tvbox.json">
      <span>巧技</span>
    </div>

所以我们做两件事：
  1. **导航页快照比对**（本模块 `scan()`）：新增站源 / 站源消失 / 域名变化 /
     接口路径变化，落盘 `data/nav_state.json`，维护页「上游源」下展示。
  2. **各直连源抓取地址比对**（`record_feeds()`）：记录每个源最近**抓成功**的
     地址，变了就记一条（比如南风的 XC.json 从 jsdelivr 换到 gh-proxy）。
     这条能抓住「导航页还没改、但源站自己 302/换路径」的情况。

顺带一个作用：导航页里的新地址会被 `urls_for()` 取出来，追加到同名 feed 的
候选地址上（运行期，不改配置文件）——所以作者一换域名，下一轮同步就能用上。

不做的事：不往 jsm.json 里塞任何东西，不改配置文件，不参与 core_ok 判定。
"""
import html as _html
import re
import time

import requests
import urllib3

from . import config as C
from . import sources as S
from . import store

urllib3.disable_warnings()

# 一个「站源条目」块：带 copy-btn 类的 div，内含 id / data-clipboard-text / <span>
_COPY_DIV = re.compile(r"<div[^>]*class=\"[^\"]*copy-btn[^\"]*\"[^>]*>", re.I)
_ATTR = re.compile(r'([A-Za-z_:][-A-Za-z0-9_:.]*)\s*=\s*"([^"]*)"')
_SPAN = re.compile(r"<span[^>]*>(.*?)</span>", re.I | re.S)

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")


def _norm_host(url):
    """主机名归一：punycode 与中文域视为同一个。

    config 里饭太硬写的是 `www.xn--sss604efuw.cc`，导航页里写的是
    `www.饭太硬.cc` —— 字符串不等但其实是同一个站，不归一就会每轮都报
    「域名变了」，把真正的变化淹掉。
    """
    h = S.norm_host(url)
    if not h:
        return ""
    try:
        return h.encode("idna").decode("ascii")
    except Exception:
        return h


def _norm_path(url):
    """接口地址归一：只看 path+query（换域名但路径没变时判定为「仅域名变化」）"""
    u = (url or "").strip()
    if "//" not in u:
        return "/"
    rest = u.split("//", 1)[1]
    i = rest.find("/")
    p = rest[i:] if i >= 0 else "/"
    return p.rstrip("/") or "/"


def parse_nav(text):
    """从导航页 HTML 里抽出 [{id, key, name, url, host, path}]

    不依赖固定顺序：先定位每个 `copy-btn` 的起始标签，再在**下一个 copy-btn
    之前**的片段里找 span 文案（同一块的 span 一定在它自己的 div 里）。

    【`key` 而不是 `id` 做唯一标识 —— 2026-10-22 实测】
    作者在饭太硬 / 备用 / 江苏福建专用这三条上复制粘贴没改 id，三个都是
    `id="yunxing"`。拿 id 当键会让比对阶段互相覆盖（6 条只剩 4 条），
    所以键改成 `id|站名`，站名变了才视为换了一条。
    """
    out = []
    marks = [(m.start(), m.group(0)) for m in _COPY_DIV.finditer(text or "")]
    for i, (pos, tag) in enumerate(marks):
        end = marks[i + 1][0] if i + 1 < len(marks) else min(len(text), pos + 2000)
        seg = text[pos:end]
        attrs = dict((k.lower(), _html.unescape(v)) for k, v in _ATTR.findall(tag))
        url = (attrs.get("data-clipboard-text") or "").strip()
        if not url.lower().startswith(("http://", "https://")):
            continue
        m = _SPAN.search(seg)
        name = _html.unescape(re.sub(r"<[^>]+>", "", m.group(1))).strip() if m else ""
        sid = (attrs.get("id") or "").strip()
        name = name or sid or _norm_host(url)
        out.append({
            "id": sid or name,
            "key": "%s|%s" % (sid or name, name),
            "name": name,
            "url": url,
            "host": _norm_host(url),
            "path": _norm_path(url),
        })
    # 同 key 去重（页面上偶尔有重复块），保留第一次出现
    seen, uniq = set(), []
    for it in out:
        if it["key"] in seen:
            continue
        seen.add(it["key"])
        uniq.append(it)
    return uniq


def diff(prev, now):
    """比对两次快照，返回 {added, removed, moved, same}"""
    pm = {}
    for it in (prev or []):
        pm[str(it.get("key") or it.get("id"))] = it
    nm = {}
    for it in (now or []):
        nm[str(it.get("key") or it.get("id"))] = it

    added = [dict(it, kind="新增站源") for k, it in nm.items() if k not in pm]
    removed = [dict(it, kind="站源消失") for k, it in pm.items() if k not in nm]
    moved = []
    for k, cur in nm.items():
        old = pm.get(k)
        if not old:
            continue
        oh, nh = str(old.get("host") or ""), str(cur.get("host") or "")
        op, np_ = str(old.get("path") or ""), str(cur.get("path") or "")
        if oh == nh and op == np_:
            continue
        if oh != nh and op != np_:
            why = "域名 + 接口路径都变了"
        elif oh != nh:
            why = "换域名（接口路径未变）"
        else:
            why = "接口路径变了（域名未变）"
        moved.append({"id": cur.get("id") or k, "name": cur.get("name") or old.get("name"),
                      "old": old.get("url"), "new": cur.get("url"),
                      "old_host": oh, "new_host": nh,
                      "old_path": op, "new_path": np_,
                      "kind": why})
    return {"added": added, "removed": removed, "moved": moved,
            "same": len(nm) - len(added) - len(moved)}


def to_ascii_url(url):
    """中文域名转 punycode（requests 对 `www.饭太硬.cc` 不一定稳，转一下最保险）"""
    u = str(url or "")
    if not u or all(ord(c) < 128 for c in u):
        return u
    try:
        head, _, rest = u.partition("//")
        host, slash, tail = rest.partition("/")
        return head + "//" + host.encode("idna").decode("ascii") + slash + tail
    except Exception:
        return u


def _fetch(url, timeout=12):
    r = requests.get(url, timeout=timeout, verify=False,
                     headers={"User-Agent": _UA,
                              "Accept": "text/html,application/xhtml+xml,*/*"},
                     allow_redirects=True)
    return r


def scan(force=False):
    """抓一次导航页并与上次快照比对，落盘 `data/nav_state.json`。

    返回值就是落盘的那份结构。失败时**保留上次快照**（不清空），只在
    note 里写失败原因 —— 站方临时挂掉不应该让「已知站源清单」归零。
    """
    spec = (getattr(C, "NAV_WATCH", None) or [{}])[0]
    url = spec.get("url") or "https://fty.888484.xyz/"
    name = spec.get("name") or "饭太硬导航"
    path = getattr(C, "F_NAV_STATE", None) or ""
    prev = store.load(path, {}) or {}
    state = dict(prev) if isinstance(prev, dict) else {}
    state.setdefault("items", [])
    state.setdefault("changes", {})

    # 刚抓过就不重复抓（一次同步里可能被多处调用）
    if not force and state.get("at") and time.time() - int(state["at"]) < 120:
        return state

    try:
        r = _fetch(url)
        if r.status_code != 200:
            raise RuntimeError("HTTP %s" % r.status_code)
        text = r.content.decode("utf-8", "ignore")
        now = parse_nav(text)
        if not now:
            raise RuntimeError("页面里没解析到站源条目（%d 字节）" % len(r.content))
    except Exception as e:
        state.update({"ok": False, "note": "%s: %s" % (type(e).__name__, str(e)[:120]),
                      "checked": int(time.time())})
        if path:
            store.save(path, state)
        store.log_event("sync", "导航页抓取失败（保留上次快照）：%s" % state["note"])
        return state

    before = state.get("items") or []
    first_time = not before
    d = diff(before, now)
    if first_time:
        # 首次抓取只是「建立基线」，不是「新增了 6 个站源」。不特殊处理的话
        # 维护页一打开就是 6 条红色告警，把人吓一跳，也会掩盖真正的变化。
        d = {"added": [], "removed": [], "moved": [], "same": len(now), "baseline": True}
    first_seen = {str(it.get("key") or it.get("id")): it.get("since") for it in before}
    for it in now:
        it["since"] = first_seen.get(str(it.get("key") or it.get("id"))) or int(time.time())

    changed_total = len(d["added"]) + len(d["removed"]) + len(d["moved"])
    state.update({
        "ok": True, "name": name, "url": url, "at": int(time.time()),
        "note": ("首次建立基线（%d 个站源），下次起比对变化" % len(now))
                if first_time else ("站点 %d 个" % len(now)),
        "baseline": first_time,
        "items": now,
        "changes": d,
        "changed_total": changed_total,
    })
    if changed_total:
        state["changes_at"] = int(time.time())
        parts = []
        for x in d["moved"]:
            parts.append("%s：%s" % (x["name"], x["kind"]))
        for x in d["added"]:
            parts.append("新增 %s" % x["name"])
        for x in d["removed"]:
            parts.append("消失 %s" % x["name"])
        store.log_event("sync", "导航页站源变化 %d 条 —— %s"
                        % (changed_total, "；".join(parts)[:400]))
    if path:
        store.save(path, state)
    return state


def urls_for(feed_name, nav_names=None):
    """从**已落盘**的导航快照里取某个 feed 对应的地址（用于并入候选）。

    两种认领方式，优先显式绑定：
      ① `nav_names` —— `UPSTREAM_FEEDS` 里用 `"nav": ["备用", "江苏福建专用"]`
         写明该 feed 对应导航页里的哪几条。名字互相包含即命中。
         为什么需要显式绑定：feed 叫「饭太硬备用（江苏福建）」，而导航页里是
         「备用」「江苏福建专用」两条，靠 feed 名硬猜会把「饭太硬」主地址
         也一起拉进来（实测就是这样，两个 feed 拿到同一份地址）。
      ② 没写 `nav` 时退回「名字互相包含」的宽松匹配，聊胜于无。

    返回的地址已转 punycode（中文域名 `www.饭太硬.cc` → `www.xn--sss604efuw.cc`）。
    """
    state = store.load(getattr(C, "F_NAV_STATE", "") or "", {}) or {}
    items = state.get("items") or []
    want = str(feed_name or "")
    names = [str(n) for n in (nav_names or []) if str(n or "").strip()]
    if not items or (not want and not names):
        return []
    out = []
    for it in items:
        n = str(it.get("name") or "")
        if names:
            hit = any(nm and (n == nm or n in nm or nm in n) for nm in names)
        else:
            hit = bool(want and n and (n in want or want in n))
        if not hit:
            continue
        u = to_ascii_url(str(it.get("url") or ""))
        if u and u not in out:
            out.append(u)
    return out


# ---------------------------------------------------------------------------
# 各直连源「接口地址」变化留档
# ---------------------------------------------------------------------------

def record_feeds(rows):
    """记录每个上游源本次抓取用的地址，返回发生地址变化的列表。

    rows: `[(feed_name, url, ok, note[, track]), ...]`
      url   用**实际抓成功的那个**（`fetch_config` 返回的 final_url），这样
            站方自己 302 到新地址也能被发现。
      track 是否参与「地址变化」判定，默认 True。
            **GitHub 镜像源必须传 False** —— 它们有 fastly / cdn / gh-proxy
            多个镜像，本轮谁先成功就用谁，地址每轮都在换，记成「地址变化」
            纯属噪音（实测第一次就误报 3 条：ikun、小盒子4K…）。真正会漂的
            是「站方自己发的那份配置」，也就是直连源。

    为什么单独存一份：`UPSTREAM_FEEDS` 是我们手写的配置，而站方换地址时
    配置不会自己变。把「最近一次成功的地址」记下来，下一轮一比对就知道
    「这个源的接口地址已经变了」，维护页能提示去更新配置。
    """
    path = getattr(C, "F_FEED_STATE", "") or ""
    prev = store.load(path, {}) or {}
    if not isinstance(prev, dict):
        prev = {}
    cur = dict(prev)
    changes = []
    now = int(time.time())
    for row in rows or []:
        try:
            name, url, ok = row[0], row[1], row[2]
            note = row[3] if len(row) > 3 else ""
            track = bool(row[4]) if len(row) > 4 else True
        except Exception:
            continue
        if not name:
            continue
        rec = dict(cur.get(name) or {})
        if not ok:
            rec.update({"last_try": now, "last_error": str(note or "")[:160]})
            cur[name] = rec
            continue
        old = str(rec.get("url") or "")
        if track and old and _addr_key(old) != _addr_key(url):
            changes.append({"name": name, "old": old, "new": url, "at": now,
                            "kind": "接口地址变化"})
            rec.setdefault("history", [])
            rec["history"] = (rec["history"] + [{"at": now, "old": old, "new": url}])[-20:]
            store.log_event("sync", "上游源 %s 的接口地址变了：%s → %s" % (name, old, url))
        rec.update({"url": url, "at": now, "ok": True, "note": str(note or "")[:120],
                    "track": track})
        cur[name] = rec

    if path:
        store.save(path, cur)
    return changes


def _addr_key(url):
    """地址指纹：host(归一) + path，忽略协议与尾斜杠"""
    return "%s%s" % (_norm_host(url), _norm_path(url))


def feed_state():
    return store.load(getattr(C, "F_FEED_STATE", "") or "", {}) or {}


def summary():
    """给 /api/upstream 用的紧凑摘要"""
    st = store.load(getattr(C, "F_NAV_STATE", "") or "", {}) or {}
    ch = st.get("changes") or {}
    return {
        "name": st.get("name") or "饭太硬导航",
        "url": st.get("url") or "",
        "ok": bool(st.get("ok")),
        "at": st.get("at") or 0,
        "checked": st.get("checked") or 0,
        "note": st.get("note") or "",
        "count": len(st.get("items") or []),
        "items": st.get("items") or [],
        "changes": ch,
        "changed_total": st.get("changed_total") or 0,
        "changes_at": st.get("changes_at") or 0,
        "baseline": bool(st.get("baseline")),
        "feed_changes": _recent_feed_changes(),
    }


def _recent_feed_changes():
    out = []
    for name, rec in (feed_state() or {}).items():
        hist = rec.get("history") or []
        if hist:
            last = hist[-1]
            out.append({"name": name, "old": last.get("old"), "new": last.get("new"),
                        "at": last.get("at")})
    out.sort(key=lambda x: -(x.get("at") or 0))
    return out[:12]
