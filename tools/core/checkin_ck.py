"""签到站点 CK 管理（DailyCheckIn /opt/dailycheckin/config/config.json）

沿用 ckcheck 的三态哲学：
    valid       明确登录成功（拿到昵称/ID/容量）
    invalid     明确失效（服务端回未登录）
    unverified  接口不通/需要签名/校验方式未知 —— 不误判成失效

支持：把整行 Cookie 或 refresh_token 写回 config.json（键=大写站点名，值=多账号 list）。

【为什么这份代码要进源码树 —— 2026-10-23】
它原来只活在 11 服务器上（`core/checkin_ck.py`），配套的 3 条路由和
「签到 CK · 提交与体检」卡片是**用补丁脚本打上去的**（tools/patch_checkin_ck.py）。
后果：oktv-hub 每次部署都会整体覆盖 `app.py` 与 `live.html`
⇒ 路由和卡片被反复冲掉，表现为「签到列表一直读取中…」（fetch 404）。
2026-10-22 已经踩过一次，10-23 又踩一次。
**根治**：模块 + 路由 + 卡片全部进源码树，部署即携带，不再依赖补丁重放。
"""
import json
import os
import shutil
import tempfile
import threading

import requests
import urllib3

urllib3.disable_warnings()

# 路径可覆盖：默认线上路径，本地开发/测试时用环境变量指到临时文件
CONFIG = os.environ.get("OKTV_CHECKIN_CONFIG",
                         "/opt/dailycheckin/config/config.json")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
TIMEOUT = 10
_LOCK = threading.Lock()

# key / 显示名 / 登录页（一键直达）/ 输入提示 / 写入 config 的字段名
SITES = [
    {"key": "TIEBA", "name": "百度贴吧", "login": "https://tieba.baidu.com/",
     "field": "COOKIE", "hint": "整行 Cookie（需含 BDUSS= 与 STOKEN=）"},
    {"key": "SMZDM", "name": "什么值得买", "login": "https://zhiyou.smzdm.com/user/login/",
     "field": "COOKIE", "hint": "整行 Cookie"},
    {"key": "BILIBILI", "name": "B站", "login": "https://passport.bilibili.com/login",
     "field": "COOKIE", "hint": "整行 Cookie（需含 SESSDATA=）"},
    {"key": "ALIYUN", "name": "阿里云盘", "login": "https://www.alipan.com/",
     "field": "REFRESH_TOKEN", "hint": "refresh_token（非 Cookie）"},
    {"key": "ACFUN", "name": "AcFun", "login": "https://www.acfun.cn/login",
     "field": "COOKIE", "hint": "整行 Cookie"},
    {"key": "IQIYI", "name": "爱奇艺", "login": "https://passport.iqiyi.com/login/",
     "field": "COOKIE", "hint": "整行 Cookie（需含 P00001=）"},
    {"key": "V2EX", "name": "V2EX", "login": "https://www.v2ex.com/signin",
     "field": "COOKIE", "hint": "整行 Cookie（需含 A2=）"},
    {"key": "YOUDAO", "name": "有道云笔记", "login": "https://note.youdao.com/web/",
     "field": "COOKIE", "hint": "整行 Cookie"},
    {"key": "KGQQ", "name": "全民K歌", "login": "https://kg.qq.com/",
     "field": "COOKIE", "hint": "整行 Cookie"},
    {"key": "BAIDU", "name": "百度站点", "login": "https://ziyuan.baidu.com/",
     "field": "COOKIE", "hint": "整行 Cookie"},
]
_BY_KEY = {s["key"]: s for s in SITES}


def _get(url, cookie=None, headers=None, method="GET", data=None):
    h = {"User-Agent": UA, "Accept": "application/json, text/plain, */*"}
    if cookie:
        h["Cookie"] = cookie
    if headers:
        h.update(headers)
    try:
        r = requests.request(method, url, headers=h, timeout=TIMEOUT,
                             verify=False, data=data)
        return r.status_code, r.text
    except Exception as exc:
        return None, str(exc)


def _j(txt):
    try:
        return json.loads(txt)
    except Exception:
        return None


def load_config():
    """读 config.json，坏文件返回空 dict（不覆盖原文）"""
    try:
        with open(CONFIG, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def save_config(d):
    """原子写回（tmp + rename），保留备份"""
    os.makedirs(os.path.dirname(CONFIG), exist_ok=True)
    if os.path.exists(CONFIG):
        shutil.copyfile(CONFIG, CONFIG + ".bak")
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(CONFIG), suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
    os.replace(tmp, CONFIG)


def _first_item(cfg, key):
    v = cfg.get(key)
    if isinstance(v, list) and v and isinstance(v[0], dict):
        return v[0]
    return None


def _preview(val):
    """脱敏预览：前 6 后 4"""
    if not val:
        return ""
    val = str(val)
    if len(val) <= 14:
        return val[:4] + "…"
    return f"{val[:6]}…{val[-4:]}（{len(val)}字符）"


def sites_payload():
    """给前端的站点列表：是否已配置 + 脱敏预览"""
    cfg = load_config()
    out = []
    for s in SITES:
        item = _first_item(cfg, s["key"])
        val = (item or {}).get(s["field"], "")
        out.append({
            "key": s["key"], "name": s["name"], "login": s["login"],
            "field": s["field"], "hint": s["hint"],
            "configured": bool(val),
            "preview": _preview(val),
        })
    return out


def save_ck(key, value):
    """写入某站点 CK。value 空字符串 = 清空"""
    key = (key or "").strip().upper()
    if key not in _BY_KEY:
        raise ValueError(f"未知站点 {key}")
    site = _BY_KEY[key]
    value = (value or "").strip()
    # 去掉可能粘进来的 "Cookie:" 前缀
    if value.lower().startswith("cookie:"):
        value = value.split(":", 1)[1].strip()
    with _LOCK:
        cfg = load_config()
        if value:
            old = _first_item(cfg, key)
            item = dict(old) if old else {}
            item[site["field"]] = value
            cfg[key] = [item]
        else:
            cfg[key] = []
        save_config(cfg)
    return {"ok": True, "key": key, "configured": bool(value)}


# ---------------- 校验 ----------------
def _v_tieba(item):
    ck = item.get("COOKIE", "")
    st, txt = _get("https://tieba.baidu.com/dc/common/tbs", cookie=ck)
    if st is None:
        return "unverified", f"网络不通: {txt[:60]}"
    d = _j(txt) or {}
    if d.get("is_login") == 1:
        st2, t2 = _get("https://zhidao.baidu.com/api/loginInfo", cookie=ck)
        name = (_j(t2) or {}).get("userName", "")
        return "valid", f"已登录 {name}" if name else "已登录"
    if "is_login" in d:
        return "invalid", "未登录（BDUSS/STOKEN 失效）"
    return "unverified", "接口返回异常"


def _v_smzdm(item):
    ck = item.get("COOKIE", "")
    st, txt = _get("https://zhiyou.smzdm.com/user/info/jsonp_get_current", cookie=ck)
    if st is None:
        return "unverified", f"网络不通: {txt[:60]}"
    if st == 200 and ("nickname" in txt or '"email"' in txt):
        d = _j(txt) or {}
        nick = (d.get("data") or {}).get("nickname") or d.get("nickname") or ""
        return "valid", f"已登录 {nick}" if nick else "已登录"
    if "登录" in txt or st in (301, 302):
        return "invalid", "未登录（需要重新登录）"
    return "unverified", f"HTTP {st}"


def _v_bilibili(item):
    ck = item.get("COOKIE", "")
    st, txt = _get("https://api.bilibili.com/x/web-interface/nav", cookie=ck)
    if st is None:
        return "unverified", f"网络不通: {txt[:60]}"
    d = _j(txt) or {}
    if d.get("code") == 0 and (d.get("data") or {}).get("isLogin"):
        return "valid", f"已登录 {(d['data'].get('uname') or '')}"
    if d.get("code") == -101:
        return "invalid", "未登录（SESSDATA 失效）"
    return "unverified", f"code={d.get('code')}"


def _v_acfun(item):
    ck = item.get("COOKIE", "")
    st, txt = _get("https://www.acfun.cn/rest/pc-direct/user/personalInfo", cookie=ck)
    if st is None:
        return "unverified", f"网络不通: {txt[:60]}"
    d = _j(txt) or {}
    info = (d.get("result") or {}).get("userInfo")
    if info:
        return "valid", f"已登录 {info.get('userName', '')}"
    if d.get("result") == 0 or d.get("error_code"):
        return "invalid", "未登录"
    return "unverified", "接口返回异常"


def _v_iqiyi(item):
    ck = item.get("COOKIE", "")
    st, txt = _get("https://passport.iqiyi.com/apis/user/info.json", cookie=ck)
    if st is None:
        return "unverified", f"网络不通: {txt[:60]}"
    d = _j(txt) or {}
    if d.get("code") == "A00000":
        return "valid", f"已登录 {(d.get('data') or {}).get('email', '')}"
    if d.get("code") in ("A00001", "Q00301"):
        return "invalid", "未登录（P00001 失效）"
    return "unverified", f"code={d.get('code')}"


def _v_aliyun(item):
    rt = item.get("REFRESH_TOKEN", "")
    if not rt:
        return "invalid", "未填写 refresh_token"
    st, txt = _get("https://auth.aliyuncs.com/v2/account/token", method="POST",
                   data={"grant_type": "refresh_token", "refresh_token": rt})
    if st is None:
        return "unverified", f"网络不通: {txt[:60]}"
    d = _j(txt) or {}
    if d.get("access_token"):
        return "valid", "refresh_token 有效"
    if d.get("code") in ("InvalidParameter", "Unauthorized"):
        return "invalid", "refresh_token 失效"
    return "unverified", f"HTTP {st}"


CHECKERS = {"TIEBA": _v_tieba, "SMZDM": _v_smzdm, "BILIBILI": _v_bilibili,
            "ACFUN": _v_acfun, "IQIYI": _v_iqiyi, "ALIYUN": _v_aliyun}


def verify_one(key):
    """校验单站；未配置返回 unconfigured"""
    key = (key or "").strip().upper()
    if key not in _BY_KEY:
        return {"key": key, "state": "unverified", "note": "未知站点"}
    item = _first_item(load_config(), key)
    if not item or not (item.get(_BY_KEY[key]["field"])):
        return {"key": key, "state": "unconfigured", "note": "未配置"}
    fn = CHECKERS.get(key)
    if not fn:
        return {"key": key, "state": "unverified", "note": "该站无在线校验，已配置"}
    try:
        state, note = fn(item)
    except Exception as exc:
        state, note = "unverified", f"校验异常: {exc}"
    return {"key": key, "state": state, "note": note}


def verify_all():
    return [verify_one(s["key"]) for s in SITES]
