"""相对路径绝对化 —— 让端对 ./lib/tokenm.json 的读与写都明确落在中枢 HTTP 上

问题
----
原版 PG 包是为「把 zip 解压到本地目录、再把配置指向本地 jsm.json」设计的。
那种用法下，配置里到处写的 `./lib/xxx`、`./js/xxx` 都是**本地文件路径**，
PG 直接 fopen 读写，天经地义。

但我们的用法是「配置指向中枢的 HTTP 地址」，此时 `./lib/tokenm.json` 到底
被解析成
    (a) http://<中枢>/hai/lib/tokenm.json   —— 走网络
    (b) 端本地的 <配置缓存目录>/lib/tokenm.json —— 走本地文件
取决于 PG 内部实现，而 tokenm.json 正是**网盘 CK 的载体**（见 tokentemplate.json）：
夸克/UC/百度/115/迅雷/123/189/移动/PikPak/光雅 的凭据字段全在里面。

如果是 (b)，那么扫码得到的 CK 会被写进端自己的本地文件 —— 中枢永远拿不到，
「一处保存多端同步」就断了上游。

做法
----
中枢返回配置时，把所有 `./lib/`、`./js/` 前缀替换成**当前请求的绝对地址**：

    ./lib/tokenm.json  ->  http://10.0.0.11:8099/hai/lib/tokenm.json

这样无论 PG 的实现偏向哪一边，路径本身都已经是明确的 HTTP URL，不存在
「它以为是本地文件」的歧义。同一份文件仍在原位置提供，功能零变化。

对「本地目录解压」用法的端没有任何影响 —— 那种端读的是自己盘上的 jsm.json，
根本不经过中枢的这次渲染。
"""
import re

# `./lib/` 与 `./js/`；前面不能紧跟字母数字（避免误伤 `x./lib/` 这类），
# 但允许 `$`、`"`、行首等 ext 分隔符场景。
_REL_RE = re.compile(r"(?<![A-Za-z0-9_./-])\./(lib|js)/")

_ALREADY_ABS = re.compile(r"(?i)^https?://")


def is_abs(u):
    return bool(_ALREADY_ABS.match(str(u or "")))


def absolutize_text(s, base, prefix="/hai"):
    """把一段文本里的 ./lib/ ./js/ 换成 {base}{prefix}/lib/ ..."""
    if not s or not base:
        return s
    base = base.rstrip("/")
    return _REL_RE.sub(lambda m: "%s%s/%s/" % (base, prefix, m.group(1)), s)


def absolutize(obj, base, prefix="/hai", _depth=0):
    """递归把所有字符串值里的相对资源路径绝对化。

    只处理 str/list/dict，其他原样返回。depth 限制防环（JSON 无环，纯保险）。
    """
    if not base or _depth > 24:
        return obj
    if isinstance(obj, str):
        return absolutize_text(obj, base, prefix)
    if isinstance(obj, list):
        return [absolutize(x, base, prefix, _depth + 1) for x in obj]
    if isinstance(obj, dict):
        return {k: absolutize(v, base, prefix, _depth + 1) for k, v in obj.items()}
    return obj


def base_from_request(req, fallback=""):
    """从 Flask request 推导出「客户端自己看到的」中枢基地址。

    优先用 Host 头 —— 端用 IP 访问就拿 IP，用域名访问就拿域名，
    这样返回给它的绝对地址一定是它自己能连上的那个。
    """
    try:
        host = (req.headers.get("X-Forwarded-Host") or req.headers.get("Host") or "").strip()
        proto = (req.headers.get("X-Forwarded-Proto") or req.scheme or "http").strip()
    except Exception:
        return fallback
    if not host:
        return fallback
    if "," in host:
        host = host.split(",")[0].strip()
    # 只允许合法的 host[:port]，挡住畸形头
    if not re.match(r"^[A-Za-z0-9\.\-_\[\]:]+$", host):
        return fallback
    return "%s://%s" % (proto, host)


def config_root(req):
    """这条请求落在哪个「配置根目录」下。

    `./lib/xxx` 这个相对路径的基准是**主配置 jsm.json 所在的目录**，不是主机根。
    请求 `/hai/lib/tokenm.json` 时配置根是 `/hai`，拼出来必须是
    `http://host/hai/lib/...`；少掉 `/hai` 这一层，端就会去一个不存在的地方取
    tokenm.json（也就等于把 CK 同步彻底掐断）。

    同一个 blueprint 同时挂在根路径和 `/oktv` 之下，所以只能从请求自身反推：
        /hai/jsm.json            -> /hai
        /hai/lib/tokenm.json     -> /hai
        /oktv/hai/lib/tokenm.json -> /oktv/hai
        /lib/tokenm.json         -> ''
        /jsm.json                -> ''
    """
    try:
        p = req.path or ""
    except Exception:
        return ""
    if p.endswith("/jsm.json"):
        return p[:-len("/jsm.json")]
    for t in ("/lib/", "/js/"):
        i = p.find(t)
        if i >= 0:
            return p[:i]
    return ""


def site_base(req, fallback=""):
    """返回 `协议 + 主机 + 配置根目录`，可直接当作替换基准。

    返回值已经含配置根，所以配套 prefix 传空串 —— 最终拼出来是 `<base>/lib/...`。
    """
    b = base_from_request(req, fallback)
    if not b:
        return "", ""
    root = config_root(req)
    if root and not re.match(r"^[A-Za-z0-9_\-/]*$", root):
        root = ""
    return b + root.rstrip("/"), ""
