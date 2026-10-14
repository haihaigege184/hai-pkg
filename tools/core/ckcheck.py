"""网盘 CK 有效性校验

只判「能真判出来的」渠道，判不了的宁可标 unverified，绝不误判：

    valid       明确能登录（拿到昵称/容量/驱动信息）
    invalid     明确登录态失效（服务端回了未登录）
    unverified  网络不通 / 接口改了 / 需要签名或验证码 —— **不参与覆盖判断**

为什么必须区分 invalid 和 unverified：
本机（11）走国内家宽，个别网盘接口可能被 DNS 污染或超时。
如果超时被当成 invalid，保险库里好用的 CK 会被一条网络抖动清掉 —— 这是最坏的结果。
"""
import json

import requests
import urllib3

urllib3.disable_warnings()

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
TIMEOUT = 8


def _get(url, cookie=None, headers=None):
    h = {"User-Agent": UA, "Accept": "application/json, text/plain, */*"}
    if cookie:
        h["Cookie"] = cookie
    if headers:
        h.update(headers)
    try:
        r = requests.get(url, headers=h, timeout=TIMEOUT, verify=False,
                         allow_redirects=True)
    except Exception:
        return None
    try:
        return r.status_code, r.text
    except Exception:
        return None


def _json(txt):
    try:
        return json.loads(txt)
    except Exception:
        return None


def _probe(cands, cookie=None, headers=None):
    """依次尝试多个接口，返回 (state, note, info)

    cands: [(url, judge_fn)] —— judge_fn(code, obj) 返回 None 表示「这个接口判不了」
    """
    reachable = False
    last = ""
    for url, judge in cands:
        out = _get(url, cookie=cookie, headers=headers)
        if out is None:
            continue
        code, txt = out
        reachable = True
        obj = _json(txt)
        if obj is None:
            last = "响应不是 JSON"
            continue
        try:
            v = judge(code, obj)
        except Exception:
            v = None
        if v is None:
            continue
        state, note = v
        return state, note, {"http": code}
    if not reachable:
        return "unverified", "接口不可达（网络问题，不判定失效）", {}
    return "unverified", last or "接口返回无法判定", {}


# ---------------------------------------------------------------- 各渠道实现


def _quark(fields):
    ck = (fields.get("quark_cookie") or "").strip()
    if not ck:
        return "unverified", "未填写", {}

    def judge(code, obj):
        data = obj.get("data") if isinstance(obj, dict) else None
        status = obj.get("status") if isinstance(obj, dict) else None
        if status == 200 and isinstance(data, dict):
            nick = data.get("nickname") or data.get("nick_name") or ""
            vip = data.get("member_type") or ""
            return "valid", ((f"昵称 {nick}" if nick else "已登录")
                             + (f" / {vip}" if vip and vip != "NORMAL" else ""))
        if status in (401, 403) or (isinstance(obj, dict) and obj.get("code") == 401):
            return "invalid", "登录态失效"
        return None

    return _probe([
        ("https://pan.quark.cn/account/info?fr=pc&platform=pc", judge),
        ("https://drive-pc.quark.cn/1/clouddrive/config?pr=ucpro&fr=pc", judge),
    ], cookie=ck)


def _uc(fields):
    ck = (fields.get("uc_cookie") or "").strip()
    if not ck:
        return "unverified", "未填写", {}

    def judge(code, obj):
        data = obj.get("data") if isinstance(obj, dict) else None
        status = obj.get("status") if isinstance(obj, dict) else None
        if status == 200 and isinstance(data, dict):
            nick = data.get("nickname") or data.get("nick_name") or ""
            return "valid", (f"昵称 {nick}" if nick else "已登录")
        if status in (401, 403):
            return "invalid", "登录态失效"
        return None

    return _probe([
        ("https://drive.uc.cn/account/info?fr=pc&platform=pc", judge),
        ("https://pc-api.uc.cn/1/clouddrive/config?pr=UCBrowser&fr=pc", judge),
    ], cookie=ck)


def _baidu(fields):
    ck = (fields.get("baidu_cookie") or "").strip()
    if not ck:
        return "unverified", "未填写", {}

    def judge(code, obj):
        if not isinstance(obj, dict):
            return None
        if obj.get("errno") == 0 and (obj.get("bdstoken") or obj.get("uk")):
            return "valid", f"uk={obj.get('uk')}"
        if obj.get("errno") in (-6, 2, 400023):
            return "invalid", "登录态失效"
        return None

    return _probe([
        ("https://pan.baidu.com/api/gettemplatevariable?clienttype=0&app_id=250528"
         "&web=1&fields=%5B%22bdstoken%22%2C%22token%22%2C%22uk%22%5D", judge),
    ], cookie=ck)


def _p115(fields):
    ck = (fields.get("pan115_cookie") or "").strip()
    if not ck:
        return "unverified", "未填写", {}

    def judge(code, obj):
        if not isinstance(obj, dict):
            return None
        if obj.get("state") is True:
            cnt = obj.get("count")
            return "valid", (f"文件数 {cnt}" if cnt is not None else "已登录")
        if obj.get("state") is False and obj.get("errno"):
            return "invalid", str(obj.get("error") or "登录态失效")
        return None

    return _probe([
        ("https://webapi.115.com/files?aid=1&cid=0&o=user_ptime&asc=0&offset=0"
         "&show_dir=1&limit=1&snap=0&natsort=0", judge),
    ], cookie=ck)


def _p123(fields):
    ck = (fields.get("pan123_cookie") or "").strip()
    if not ck:
        return "unverified", "未填写", {}

    def judge(code, obj):
        if not isinstance(obj, dict):
            return None
        if obj.get("code") == 0 and isinstance(obj.get("data"), dict):
            return "valid", f"昵称 {obj['data'].get('nickname') or '-'}"
        if obj.get("code") in (401, 403):
            return "invalid", "登录态失效"
        return None

    return _probe([("https://www.123pan.com/api/user/info", judge)], cookie=ck)


def _p189(fields):
    ck = (fields.get("pan189_cookie") or "").strip()
    if not ck:
        return "unverified", "未填写", {}

    def judge(code, obj):
        if not isinstance(obj, dict):
            return None
        if obj.get("res_code") == 0 or "cloudCapacityInfo" in obj:
            return "valid", "已登录"
        if obj.get("res_code") not in (None, 0):
            return "invalid", str(obj.get("res_message") or "登录态失效")
        return None

    return _probe([
        ("https://cloud.189.cn/api/portal/getUserSizeInfo.action", judge),
    ], cookie=ck)


def _aliyun(fields):
    tok = (fields.get("open_token") or fields.get("token") or "").strip()
    if not tok:
        return "unverified", "未填写", {}

    def judge(code, obj):
        if not isinstance(obj, dict):
            return None
        if obj.get("default_drive_id") or obj.get("user_id"):
            return "valid", f"drive={str(obj.get('default_drive_id'))[:12]}…"
        if obj.get("code") in ("AccessTokenInvalid", "InvalidParameter", 401):
            return "invalid", str(obj.get("message") or "token 失效")
        return None

    try:
        r = requests.post("https://openapi.alipan.com/adrive/v1.0/user/getDriveInfo",
                          headers={"Authorization": "Bearer " + tok, "User-Agent": UA},
                          timeout=TIMEOUT, verify=False)
        obj = _json(r.text)
        if obj is None:
            return "unverified", "接口返回无法判定", {}
        v = judge(r.status_code, obj)
        if v:
            return v[0], v[1], {"http": r.status_code}
        return "unverified", "接口返回无法判定", {}
    except Exception:
        return "unverified", "接口不可达（网络问题，不判定失效）", {}


_CHECKERS = {
    "quark": _quark,
    "uc": _uc,
    "baidu": _baidu,
    "115": _p115,
    "pan123": _p123,
    "pan189": _p189,
    "aliyun": _aliyun,
}

# 需要客户端签名/验证码，服务端判不了 —— 一律 unverified（靠「播放成功」升级为 valid）
_MANUAL = {"thunder", "pikpak", "yd", "guangya", "bili"}


def checkable(channel):
    return channel in _CHECKERS


def check(channel, fields):
    """返回 (state, note, info)"""
    if channel in _MANUAL:
        return "unverified", "该渠道需客户端登录态，由播放成功后自动置为有效", {}
    fn = _CHECKERS.get(channel)
    if not fn:
        return "unverified", "未知渠道", {}
    return fn(fields or {})
