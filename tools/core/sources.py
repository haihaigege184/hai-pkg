"""接口源解析：把 jsm.json 里的 ext 参数字符串拆解成结构化描述

影视/TVBox 系的 ext 约定（$$$ 分隔）：
    ./lib/tokenm.json$$$<baseURL>$$$<proxy>$$$<flag>$$$<rulesJson>$$$<TAG>$$$<USER>$$$<PASS>

其中：
  - parts[1] 是本接口的「站点根地址」，也是失效后需要替换的字段
  - 末尾一个全大写标识符是 TAG（同一 jar 类通过 TAG 区分不同站点与解析规则）
"""
import json
import os
import re
import urllib.parse

from . import config as C

SPLIT = "$$$"
# TAG 是 ext 尾部用来区分站点的标识：大写串（WOGG）、小写串（hdhaven）、含数字（4KZN）都出现过。
_TAG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_]{1,15}$")
# 这些是 ext 里的固定关键字/占位符，不是 TAG
_TAG_STOP = {"proxy", "noproxy", "null", "user", "pass", "password",
             "username", "db", "http", "https", "api", "token", "json"}

# ---------------------------------------------------------------------------
# 新增站点的独立 TAG 覆盖表
# ---------------------------------------------------------------------------
# 用户从「上游新站源」列表里主动加入的站点需要**独立 TAG**（不与原版共用），
# 否则上游常见的「多个不同站点共用一个解析类」（csp_TTian 同时是「追剧」和
# 「天天」）会让 A 站的地址被 B 站的域名顶掉。
#
# 覆盖表由 core/addsite.py 写入 `data/added_sites.json`，这里**只读**。
# 不让 sources 去 import addsite —— addsite 依赖 sources，会成环。
# 「同一数据不能有两套 TAG 规则」这条红线在这里落地：全工程只认 tag_of()。
_TAG_OVERRIDE_CACHE = {"fp": "", "map": {}}


def _added_tag_map():
    """{key: 独立 TAG} —— 按文件指纹缓存，改完即时生效"""
    path = getattr(C, "F_ADDED_SITES", "")
    if not path or not os.path.exists(path):
        return {}
    try:
        st = os.stat(path)
        fp = "%d:%d" % (int(st.st_mtime), int(st.st_size))
    except OSError:
        return {}
    if _TAG_OVERRIDE_CACHE["fp"] == fp:
        return _TAG_OVERRIDE_CACHE["map"]
    out = {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            d = json.load(f)
        for k, rec in ((d.get("sites") or {}) if isinstance(d, dict) else {}).items():
            t = (rec or {}).get("tag")
            if t:
                out[str(k)] = str(t)
    except Exception:
        return {}
    _TAG_OVERRIDE_CACHE["fp"] = fp
    _TAG_OVERRIDE_CACHE["map"] = out
    return out


def parse_ext(ext):
    """返回 {'parts': [...], 'base': str, 'base_idx': int, 'proxy': str, 'tag': str, 'rules': str}"""
    if not isinstance(ext, str) or SPLIT not in ext:
        return None
    parts = ext.split(SPLIT)
    base = ""
    base_idx = -1
    for i, p in enumerate(parts):
        if p.startswith("http://") or p.startswith("https://"):
            base, base_idx = p, i
            break
    if not base:
        return None
    # TAG：从 base 之后找第一个合法标识（跳过路径、固定关键字、纯数字）
    tag = ""
    for p in parts[base_idx + 1:]:
        if p.startswith("./") or p.startswith("/") or "//" in p:
            continue
        if p.lower() in _TAG_STOP:
            continue
        if not _TAG_RE.match(p):
            continue
        if not re.search(r"[A-Za-z]", p):     # 纯数字不是 TAG
            continue
        tag = p
        break
    rules = ""
    for p in parts:
        if p.startswith("./") and p.endswith(".json") and "token" not in p:
            rules = p
            break
    proxy = ""
    for p in parts:
        if p in ("proxy", "noproxy", "null"):
            proxy = p
            break
    return {"parts": parts, "base": base, "base_idx": base_idx,
            "proxy": proxy, "tag": tag, "rules": rules}


def set_ext_base(ext, new_base):
    """把 ext 里的 baseURL 换成 new_base，其余原样保留"""
    info = parse_ext(ext)
    if not info:
        return ext
    parts = list(info["parts"])
    parts[info["base_idx"]] = new_base
    return SPLIT.join(parts)


def tag_of(site, info):
    """推导站点在**域名池里的 TAG** —— 必须与 build_sources 保持一致。

    池里的 TAG = `ext 尾部 TAG or 站点的 api or key`（见 build_sources）。
    而 ext 第二段经常**直接写域名**、没有独立 TAG，例如：

        ./lib/tokenm.json$$$https://www.hdmoli.com/$$$noproxy$$$1
        ./lib/tokenm.json$$$https://662688.xyz$$$

    这些站点在池里叫 csp_Moli / csp_EightEight（走 api 兜底）。凡是只认
    `info["tag"]` 的地方都会把它们整片漏掉 —— 表现为「本地」、不参与失效
    判定、域名死了也不换。用同一套规则推导即可全部接回。

    【2026-10-23】先问「新增站点覆盖表」：用户主动加入的新站源一律用独立
    TAG，不与原版共用 —— 否则共用解析类的两个站会互相顶掉域名。
    """
    ov = _added_tag_map()
    if ov:
        t = ov.get(str((site or {}).get("key") or ""))
        if t:
            return t
    if not info:
        return ""
    return str(info.get("tag") or site.get("api") or site.get("key") or "")


def norm_host(url):
    """取主机名（小写，去 www.）"""
    try:
        h = urllib.parse.urlparse(url if "//" in url else "//" + url).hostname or ""
    except Exception:
        h = ""
    return h.lower().lstrip(".")


_PRIVATE_HOSTS = ("localhost", "::1", "0.0.0.0")


def is_public_host(host):
    """主机名是否是可以真的去访问的公网地址。

    上游社区配置里大量使用 http://127.0.0.1/... 作为本地代理占位符，
    这些绝对不能进域名池——电视机拿到只会连自己。
    """
    if not host:
        return False
    h = host.lower()
    if h in _PRIVATE_HOSTS or h.endswith(".local") or h.endswith(".localhost"):
        return False
    if h.startswith(("127.", "0.", "10.", "192.168.", "169.254.")):
        return False
    if h.startswith("172."):
        try:
            if 16 <= int(h.split(".")[1]) <= 31:
                return False
        except (IndexError, ValueError):
            pass
    if "." not in h:            # 单段主机名（容器名/主机名）不可用
        return False
    return True


def norm_base(url):
    """规整成 scheme://host/ 形式，用于域名池去重；私有/占位地址返回空串"""
    u = url.strip()
    if not u.startswith("http"):
        u = "http://" + u
    p = urllib.parse.urlparse(u)
    if not p.hostname:
        return ""
    host = p.hostname.lower()
    if not is_public_host(host):
        return ""
    scheme = p.scheme or "http"
    return f"{scheme}://{host}/"


def same_site(d1, d2):
    """两个地址是否属于同一站点。

    只认「去掉 www. 前缀后完全相同」的主机名。
    不能用注册域比较——这些站常用同一注册域下的不同子域承载不同站点
    （如 wogg.888484.xyz 已废弃、而 woggpan.888484.xyz 仍可用），
    按注册域判重会把可用的新子域误杀。
    """
    h1, h2 = norm_host(d1), norm_host(d2)
    if not h1 or not h2:
        return False
    return _strip_www(h1) == _strip_www(h2)


def _strip_www(h):
    return h[4:] if h.startswith("www.") else h


def is_retired(tag, url):
    """该地址是否已被用户判为弃用（见 config.RETIRED_DOMAINS）

    比较口径与 same_site 一致：两边主机名都去掉 www. 后全等。
    返回 True 的地址一律 **不探测、不算可用、不写回配置**。
    """
    hosts = (getattr(C, "RETIRED_DOMAINS", {}) or {}).get(str(tag or ""))
    if not hosts:
        return False
    host = _strip_www(norm_host(url))
    if not host:
        return False
    for h in hosts:
        hh = _strip_www(norm_host(h if "//" in str(h) else "//" + str(h)))
        if hh and hh == host:
            return True
    return False


def build_sources(baseline_jsm):
    """从基线 jsm.json 抽取出所有「带远程地址」的接口源，按 TAG 归档。

    返回 { TAG: {tag, key, name, api, proxy, rules, domains:[...], sites:[key...]} }
    """
    out = {}
    for s in baseline_jsm.get("sites", []):
        ext = s.get("ext")
        info = parse_ext(ext) if isinstance(ext, str) else None
        if not info:
            continue
        tag = tag_of(s, info) or "UNKNOWN"
        rec = out.setdefault(tag, {
            "tag": tag, "key": s.get("key"), "name": s.get("name"),
            "api": s.get("api"), "proxy": info["proxy"], "rules": info["rules"],
            "domains": [], "sites": [],
        })
        base = norm_base(info["base"])
        if base and base not in rec["domains"]:
            rec["domains"].append(base)
        if s.get("key") not in rec["sites"]:
            rec["sites"].append(s.get("key"))
    return out


def merge_domains(pool_entry, candidates, origin="unknown"):
    """把候选域名并入域名池条目，返回新增列表。已存在（含同站不同后缀）则跳过。

    用户已判弃用的域名（config.RETIRED_DOMAINS）**直接拒收** —— 上游配置里
    往往还留着旧域名，不拦就会被每轮采集重新灌回池里。
    """
    added = []
    tag = pool_entry.get("tag") or ""
    known = [d["url"] for d in pool_entry.get("domains", [])]
    for c in candidates:
        nb = norm_base(c)
        if not nb:
            continue
        if is_retired(tag, nb):
            continue
        if any(same_site(nb, k) for k in known):
            continue
        if nb in known:
            continue
        pool_entry.setdefault("domains", []).append({
            "url": nb, "origin": origin, "added": _now(),
            "status": "unknown", "latency": 0, "http": 0, "bytes": 0,
            "checked": 0, "note": "",
        })
        known.append(nb)
        added.append(nb)
    return added


def _now():
    import time
    return int(time.time())
