# -*- coding: utf-8 -*-
"""百度分享解析（照 Playwright 抓到的真实请求实现）。

链路：
  1. GET  /s/1<surl>                       抠 shareid / share_uk / bdstoken
  2. POST /share/verify  {pwd}             拿 BDCLND cookie
  3. GET  /share/tplconfig?fields=sign,timestamp   → sign, timestamp
  4. GET  /share/list?web=5&shorturl=&root=1&view_mode=1   → 文件列表
  5. GET  /share/streaming?uk=&fid=&sign=&timestamp=&shareid=&type=M3U8_AUTO_480&vip=0
      或 /share/download → 原始文件直链

关键点（之前一直 errno 2 的原因）：
  - `web=5` 和 `web=1` **两个都要带**
  - 用 `shorturl=<surl>`，不是 uk+shareid
  - 必须带 `root=1`、`view_mode=1`、`logid`、`dp-logid`
"""
import base64
import http.cookiejar
import json
import os
import random
import re
import sys
import time
import urllib.parse
import urllib.request

APP_ID = "250528"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36")


def _logid():
    # 形如 QzdFNTFDMEU3NEUzMUE0MjA0QUQxQzAxNzI5MUMxQzA6Rkc9MQ==  → "XXXX:FG=1"
    raw = ("%032X" % random.getrandbits(128))[:32] + ":FG=1"
    return base64.b64encode(raw.encode()).decode()


def _dplogid():
    return "%d" % random.randint(10 ** 18, 10 ** 19)


class Bd:
    def __init__(self, ck: str):
        self.ck = ck or ""
        self.jar = http.cookiejar.CookieJar()
        for kv in self.ck.split(";"):
            kv = kv.strip()
            if "=" in kv:
                k, v = kv.split("=", 1)
                self.jar.set_cookie(http.cookiejar.Cookie(
                    0, k, v, None, False, ".baidu.com", False, False, "/",
                    True, False, None, False, None, None, {}))
        self.op = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar))
        self.shareid = ""
        self.uk = ""
        self.bdstoken = ""
        self.surl = ""
        self.ref = ""

    def get(self, url, post=None, ref=None, timeout=20):
        h = {"User-Agent": UA, "Referer": ref or self.ref or "https://pan.baidu.com/",
             "Accept": "application/json, text/plain, */*",
             "Accept-Language": "zh-CN,zh;q=0.9",
             "X-Requested-With": "XMLHttpRequest"}
        data = None
        if post is not None:
            data = urllib.parse.urlencode(post).encode()
            h["Content-Type"] = "application/x-www-form-urlencoded; charset=UTF-8"
        try:
            with self.op.open(urllib.request.Request(url, data=data, headers=h),
                              timeout=timeout) as f:
                return f.read().decode("utf-8", "ignore")
        except urllib.error.HTTPError as e:
            return e.read().decode("utf-8", "ignore")
        except Exception as e:
            return "ERR " + str(e)

    def open_share(self, share_url: str) -> bool:
        m = re.search(r"pan\.baidu\.com/s/1?([\w\-]+)", share_url)
        if not m:
            return False
        self.surl = m.group(1)
        self.ref = "https://pan.baidu.com/s/1" + self.surl
        html = self.get(self.ref, ref="https://pan.baidu.com/")
        self.shareid = (re.findall(r'"shareid"\s*:\s*"?(\d{6,})"?', html) or [""])[0]
        self.uk = (re.findall(r'"share_uk"\s*:\s*"(\d+)"', html) or [""])[0]
        self.bdstoken = (re.findall(r'"bdstoken"\s*:\s*"([^"]+)"', html) or [""])[0]
        return bool(self.shareid and self.uk)

    def verify(self, pwd: str) -> bool:
        q = ("?t=%d&bioc=1&surl=%s&channel=chunlei&web=1&app_id=%s"
             "&bdstoken=%s&logid=%s&clienttype=0&dp-logid=%s"
             % (int(time.time() * 1000), self.surl, APP_ID, self.bdstoken,
                _logid(), _dplogid()))
        r = self.get("https://pan.baidu.com/share/verify" + q,
                     post={"pwd": pwd, "vcode": "", "vcode_str": ""},
                     ref="https://pan.baidu.com/share/init?surl=" + self.surl)
        try:
            d = json.loads(r)
            return int(d.get("errno") or 0) == 0
        except Exception:
            return False

    def sign_ts(self):
        q = ("?surl=1%s&fields=sign,timestamp&view_mode=1&channel=chunlei&web=1"
             "&app_id=%s&bdstoken=%s&logid=%s&clienttype=0&dp-logid=%s"
             % (self.surl, APP_ID, self.bdstoken, _logid(), _dplogid()))
        r = self.get("https://pan.baidu.com/share/tplconfig" + q)
        try:
            d = (json.loads(r).get("data") or {})
            return d.get("sign") or "", d.get("timestamp") or 0
        except Exception:
            return "", 0

    def ls(self, dir_path: str = "/", page: int = 1, num: int = 100):
        """列目录。**子目录不能带 root=1** —— 带了百度永远回分享根
        （2026-11-02 实测：换个 dir 也是同一条结果，导致套娃分享扫不出文件）。"""
        is_root = (not dir_path) or dir_path == "/"
        q = ("?web=5&app_id=%s&desc=1&showempty=0&page=%d&num=%d&order=time"
             "&shorturl=%s&view_mode=1&channel=chunlei&web=1"
             "&bdstoken=%s&logid=%s&clienttype=0&dp-logid=%s"
             % (APP_ID, page, num, self.surl, self.bdstoken, _logid(), _dplogid()))
        if is_root:
            q += "&root=1"
        else:
            q += "&dir=" + urllib.parse.quote(dir_path)
        # 百度这个接口**偶尔空回**（同一条分享上一秒 5 项、下一秒 0 项，
        # 2026-11-02 实测），所以空结果重试两次再认。
        last = None
        for _try in range(3):
            r = self.get("https://pan.baidu.com/share/list" + q)
            try:
                d = json.loads(r)
            except Exception:
                d = {"errno": -99, "raw": r[:300]}
            last = d
            if (d.get("list") or []) or int(d.get("errno") or 0) != 0:
                return d
            time.sleep(0.35)
        return last or {"errno": -99}

    def streaming(self, fid, sign, ts, typ="M3U8_AUTO_480"):
        q = ("?channel=chunlei&uk=%s&fid=%s&sign=%s&timestamp=%s&shareid=%s"
             "&type=%s&vip=0&jsToken=undefined&isplayer=1&check_blue=1&adToken="
             % (self.uk, fid, sign, ts, self.shareid, typ))
        return self.get("https://pan.baidu.com/share/streaming" + q)

    def download(self, fid, sign, ts):
        """原始文件直链（不转码）。"""
        q = ("?sign=%s&timestamp=%s&fid=%s&uk=%s&shareid=%s&channel=chunlei"
             "&web=1&app_id=%s&bdstoken=%s&clienttype=0&logid=%s"
             % (sign, ts, fid, self.uk, self.shareid, APP_ID, self.bdstoken, _logid()))
        for path in ("/share/download", "/api/download"):
            r = self.get("https://pan.baidu.com" + path + q)
            if r.startswith("{"):
                try:
                    d = json.loads(r)
                    if d.get("dlink") or d.get("url"):
                        return d
                    if int(d.get("errno") or 0) == 0:
                        return d
                except Exception:
                    pass
            elif r.startswith("http"):
                return {"dlink": r}
        return {}


    # ---------- 转存 + 原盘直链 ----------
    # ---------- PCS 在线播放（2026-11-03 新增，普通账号可用）-------------
    #
    # 关键事实：百度对**非会员的下载直链**限到 ~100 KB/s，而且单次 Range 超
    # 4~8MB 直接 `403 error_code:31326`。所以拿 `rt=pr` 直链去喂播放器只能
    # 转圈到 3003（这就是我们之前判成「非会员不能播」的原因，判断错了）。
    #
    # 别人（含 PG 这类本地包）走的是**百度网页版「在线播放」用的 PCS 接口**：
    #   GET https://d.pcs.baidu.com/rest/2.0/pcs/file
    #       ?method=streaming&app_id=250528&path=<自己网盘里的路径>
    #       &type=M3U8_AUTO_1080
    #   → 200 `#EXTM3U`，分片在 v1-ant.baidu.com（转码集群，**不走下载限速**，
    #     且分片**不需要 Cookie**，播放器可以直连）。
    # 前提只有两个：① 文件已转存到自己网盘；② 带上 BDUSS/STOKEN 的 Cookie。
    # 注意域名必须是 **pcs.baidu.com / d.pcs.baidu.com**，不是 pan.baidu.com
    #（后者对 rest/2.0/pcs/file 直接回「页面不存在」）。
    def _pcs(self, path, typ="M3U8_AUTO_1080"):
        q = urllib.parse.urlencode({
            "method": "streaming", "app_id": APP_ID,
            "path": path, "type": typ,
        })
        h = {"User-Agent": "netdisk;P2SP;3.0.0.8",
             "Referer": "https://pan.baidu.com/", "Accept": "*/*"}
        try:
            with self.op.open(urllib.request.Request(
                    "https://d.pcs.baidu.com/rest/2.0/pcs/file?" + q,
                    headers=h), timeout=25) as f:
                return f.read().decode("utf-8", "ignore")
        except Exception as e:
            return "ERR " + str(e)

    def streaming_path(self, path, typ="M3U8_AUTO_1080"):
        """自己网盘里的文件 → HLS m3u8 文本（普通账号可用）。"""
        txt = self._pcs(path, typ)
        if "#EXTM3U" in txt[:200]:
            return txt
        return ""

    def own_path(self, name, max_depth=3):
        """在自己网盘里按文件名找**完整路径**（转存过的文件用）。"""
        target = str(name or "")

        def walk(p, depth):
            q = urllib.parse.urlencode({
                "order": "name", "desc": "0", "showempty": "0", "web": "1",
                "page": "1", "num": "200", "dir": p, "t": "",
                "channel": "chunlei", "app_id": APP_ID,
                "bdstoken": self.bdstoken, "clienttype": "0",
            })
            r = self.get("https://pan.baidu.com/api/list?" + q)
            try:
                d = json.loads(r)
            except Exception:
                return ""
            for it in (d.get("list") or []):
                fn = it.get("server_filename") or ""
                pp = it.get("path") or ""
                if str(it.get("isdir")) == "1":
                    if depth < max_depth:
                        got = walk(pp, depth + 1)
                        if got:
                            return got
                elif fn == target:
                    return pp
            return ""

        return walk("/", 0)

    def own_find(self, name, max_depth=2, budget=24):
        """在自己网盘里按文件名找 {fid, path}。**有预算**，不再无限递归。

        2026-11-03 教训：不设上限时这里会把 42 个目录 × 3 层 × 200 条全扫一遍，
        一次解析 3 分 49 秒。正常布局（`/剧名/文件`）两层就够，给 24 次列表
        请求的预算，超了就放弃，交给上层走「转存到根目录」。
        """
        target = str(name or "")
        hit = {}
        left = [int(budget)]

        def walk(p, depth):
            if left[0] <= 0:
                return False
            left[0] -= 1
            q = urllib.parse.urlencode({
                "order": "name", "desc": "0", "showempty": "0", "web": "1",
                "page": "1", "num": "200", "dir": p, "t": "",
                "channel": "chunlei", "app_id": APP_ID,
                "bdstoken": self.bdstoken, "clienttype": "0",
            })
            r = self.get("https://pan.baidu.com/api/list?" + q)
            try:
                d = json.loads(r)
            except Exception:
                return False
            for it in (d.get("list") or []):
                if str(it.get("isdir")) == "1":
                    if depth < max_depth and walk(it.get("path") or p, depth + 1):
                        return True
                elif (it.get("server_filename") or "") == target:
                    hit["fid"] = str(it.get("fs_id") or "")
                    hit["path"] = it.get("path") or ""
                    return True
            return False

        walk("/", 0)
        return hit

    # ---------- 自有文件台账（2026-11-03）---------------------------------
    #
    # 事故：`own_find` 递归扫整个网盘（42 个目录 × 3 层 × 200 条）—— 一次
    # 解析花了 **3 分 49 秒**。转存成功后我们明明知道路径（transfer 返回的
    # `to`），没必要再找。这里记一份 name → {fid, path} 的台账，之后直接命中。
    _LEDGER_PATH = None

    def _ledger_file(self):
        if Bd._LEDGER_PATH:
            return Bd._LEDGER_PATH
        try:
            from . import config as C
            d = os.path.join(C.DATA_DIR)
        except Exception:
            d = "/tmp"
        Bd._LEDGER_PATH = os.path.join(d, "bd_own.json")
        return Bd._LEDGER_PATH

    def _ledger(self):
        try:
            with open(self._ledger_file(), encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def remember(self, name, fid="", path=""):
        tab = self._ledger()
        cur = tab.get(name) or {}
        tab[name] = {"fid": str(fid or cur.get("fid") or ""),
                     "path": str(path or cur.get("path") or ""),
                     "t": int(time.time())}
        try:
            with open(self._ledger_file(), "w", encoding="utf-8") as f:
                json.dump(tab, f, ensure_ascii=False)
        except Exception:
            pass

    def own_lookup(self, name):
        """先查台账，再（不得已才）扫网盘。返回 {fid, path}。"""
        tab = self._ledger().get(str(name or ""))
        if tab and (tab.get("path") or tab.get("fid")):
            return {"fid": tab.get("fid") or "", "path": tab.get("path") or ""}
        info = self.own_find(name)
        if info:
            self.remember(name, info.get("fid"), info.get("path"))
        return info

    def stat_path(self, path):
        """按路径取 fid（只列父目录一次，绝不递归）。"""
        path = str(path or "")
        if not path.startswith("/"):
            return ""
        parent = path.rsplit("/", 1)[0] or "/"
        base = path.rsplit("/", 1)[-1]
        q = urllib.parse.urlencode({
            "order": "name", "desc": "0", "showempty": "0", "web": "1",
            "page": "1", "num": "200", "dir": parent, "t": "",
            "channel": "chunlei", "app_id": APP_ID,
            "bdstoken": self.bdstoken, "clienttype": "0",
        })
        r = self.get("https://pan.baidu.com/api/list?" + q)
        try:
            d = json.loads(r)
        except Exception:
            return ""
        for it in (d.get("list") or []):
            if (it.get("server_filename") or "") == base:
                return str(it.get("fs_id") or "")
        return ""

    def transfer(self, fs_id, to_path="/"):
        """把分享里的文件转存到自己的网盘，返回 (ok, 新 fid)。

        **必须登录**（BDUSS 有效）。CK 过期时百度回 errno -6 未登录。
        """
        q = ("?shareid=%s&from=%s&bdstoken=%s&channel=chunlei&web=1"
             "&app_id=%s&clienttype=0"
             % (self.shareid, self.uk, self.bdstoken, APP_ID))
        body = urllib.parse.urlencode(
            {"fsidlist": "[%s]" % fs_id, "path": to_path})
        h = {"User-Agent": UA, "Referer": self.ref,
             "Content-Type": "application/x-www-form-urlencoded",
             "X-Requested-With": "XMLHttpRequest"}
        try:
            with self.op.open(urllib.request.Request(
                    "https://pan.baidu.com/share/transfer" + q,
                    data=body.encode(), headers=h), timeout=25) as f:
                d = json.loads(f.read().decode("utf-8", "ignore"))
        except Exception as e:
            return False, "transfer err %s" % str(e)[:60]
        if int(d.get("errno") or 0) != 0:
            return False, "errno %s %s" % (d.get("errno"), d.get("show_msg") or "")
        try:
            nf = str(d["extra"]["list"][0]["to"])
            return True, nf
        except Exception:
            # 已经存在同名的也算成功：回去按名字找
            return False, "transfer ok but no fid: %s" % str(d)[:120]

    def dlink(self, fid):
        """自己的文件 → 原始下载直链（4K 原盘走这条，不转码）。

        2026-11-03 修正：`filemetas` 的 id 参数叫 **`fsids`**，值必须是
        JSON 数组（`fsids=%5B<fid>%5D`）。老代码写的 `fids=` 百度一律回
        **errno 2**（参数错），我们当成"取不到直链"就退回 m3u8 试看，
        而那个 `share/streaming` 试看口现在恒回 `{"errno":71}`
        （sign 校验失败）⇒ 播放器拿到一坨 JSON ⇒ 界面 3003。

        **2026-11-03 晚：尾巴必须挂 `&origin=dlna`（这是全部秘密）。**
        同一文件、同一非会员账号、同一条 `rt=pr` dlink，实测：
            裸链 + Cookie            → 206，**69 KB/s**（这就是"非会员限速"）
            + `&origin=dlna`         → 206，**7.16 MB/s**
            + `&chkv=1`              → 206，**8.12 MB/s**
        `origin` 换成 pcs/web/android 一律 `403 31326 hitcode:104`，
        只有 `dlna` 算"投屏播放"，走播放带宽而不是下载带宽。
        电视上 PG 播 25.3GB 2160p 原盘的 URL 尾巴正是 `&origin=dlna`
        （从 `/media` 抓到的原文可对），它的 32 线程代理也只跑 4.9 MB/s，
        我们单连接就 8 MB/s。
        """
        q = ("?dlink=1&fsids=%5B" + str(fid) + "%5D&type=dlink"
             "&channel=chunlei&web=1&app_id=" + APP_ID
             + "&bdstoken=" + self.bdstoken + "&clienttype=0")
        r = self.get("https://pan.baidu.com/api/filemetas" + q)
        try:
            d = json.loads(r)
        except Exception:
            return ""
        if int(d.get("errno") or 0) != 0:
            return ""
        for it in (d.get("info") or []):
            if it.get("dlink"):
                u = str(it["dlink"]).strip()
                if "origin=" not in u:
                    u += ("&" if "?" in u else "?") + "origin=dlna"
                return u
        return ""

    def find_in_mine(self, name):
        """在自己网盘根目录按文件名找 fid。

        2026-11-03 修：`dir` 必须编码成 `%2F`。`urllib.parse.quote("/")` 默认
        **不转义斜杠**，于是发出去的是 `dir=/`，百度回 `errno 140`（参数错），
        我们当成"没转存过" → 又去转存 → 死循环。
        """
        q = ("?order=name&desc=0&showempty=0&web=1&page=1&num=200&dir=%2F"
             "&t=&channel=chunlei&app_id=%s&bdstoken=%s&clienttype=0"
             % (APP_ID, self.bdstoken))
        r = self.get("https://pan.baidu.com/api/list" + q)
        try:
            d = json.loads(r)
        except Exception:
            return ""
        for it in (d.get("list") or []):
            if (it.get("server_filename") or "") == name:
                return str(it.get("fs_id") or "")
        return ""


if __name__ == "__main__":
    ck = ""
    try:
        ck = json.load(open("/tmp/bdck.json", encoding="utf-8")).get("baidu") or ""
    except Exception:
        pass
    if not ck:
        ck = sys.argv[2] if len(sys.argv) > 2 else ""
    share = sys.argv[1] if len(sys.argv) > 1 else "https://pan.baidu.com/s/1AwuCboQHoj7nGwKbu341Wg?pwd=237v"
    pwd = ""
    m = re.search(r"[?&]pwd=(\w+)", share)
    if m:
        pwd = m.group(1)

    b = Bd(ck)
    print("open_share:", b.open_share(share), "shareid", b.shareid, "uk", b.uk,
          "bdstoken", b.bdstoken[:8])
    print("verify:", b.verify(pwd) if pwd else "(无提取码)")
    d = b.ls("/")
    print("list errno:", d.get("errno"), "文件数", len(d.get("list") or []))
    for it in (d.get("list") or [])[:5]:
        print("   ", it.get("server_filename"), it.get("size"), "isdir", it.get("isdir"),
              "fs_id", it.get("fs_id"))
    sign, ts = b.sign_ts()
    print("sign:", sign[:16], "ts:", ts)
    lst = d.get("list") or []
    if lst and sign:
        fid = lst[0].get("fs_id")
        print("\n--- download 原始直链 ---")
        print(json.dumps(b.download(fid, sign, ts), ensure_ascii=False)[:300])
        print("\n--- streaming 各画质 ---")
        for t in ("M3U8_AUTO_2160", "M3U8_AUTO_1080", "M3U8_AUTO_720", "M3U8_AUTO_480"):
            r = b.streaming(fid, sign, ts, t)
            print("  %-16s -> %s" % (t, r[:110].replace("\n", " ")))
