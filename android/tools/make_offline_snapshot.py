# -*- coding: utf-8 -*-
"""
把 10.0.0.11:8099 的海报墙 / 详情页导出成 APK 内置离线快照。

产物全部落在 android/assets/haiui/ 下（build_hai_tv.py 的 step_assets 会整体打包）：
    wall.html                      首页（沿用已有资产）
    pages/<md5>.html               分类 / 翻页页（key = 归一化 query）
    detail/<vid>.html              详情页
    dbimg/<原路径>.jpg             海报 / 剧照 / 演员图
    ui/wall.css / ui/detail.css    共享样式（从内联 <style> 抽出）
    lib/haiplay.js                 站源脚本
    manifest.json                  清单与统计

清洗原则：
  1. 页面里绝不残留绝对服务器地址，一律根相对，天然落 127.0.0.1:10090 同源；
  2. 详情页「播放」只保留 haiplay://search?q=片名 → PG 原链路，
     legacy 的 haiplay://play?site=TMDBWall 单集直达改成同样的搜索深链；
  3. /hai/poster/eps|play|progress|warm|cast 等服务端动态接口统一指向
     /hai/offline/empty.json（jar 本地返回空对象），脚本不再请求任何外部地址；
  4. 内联 CSS 抽成共享文件，详情页体积压到 ~10KB/页。

用法：
    python make_offline_snapshot.py --scan     # 只抓取并统计，不落盘
    python make_offline_snapshot.py            # 全量导出
"""
import os
import re
import sys
import json
import time
import hashlib
import urllib.parse
import urllib.request
import concurrent.futures

BASE = "http://10.0.0.11:8099"
HERE = os.path.dirname(os.path.abspath(__file__))
ASSETS = os.path.abspath(os.path.join(HERE, "..", "assets", "haiui"))
SCAN_ONLY = "--scan" in sys.argv

MAX_PAGES = 3          # 每个分类最多抓 3 页
MAX_DETAIL = 420       # 详情页上限
MAX_IMAGES = 3200      # 图片上限（实测引用 2601 张，全部压缩后打包）
TIMEOUT = 25

# 本机 envs/default 的 Python 才有 Pillow；用 versions/3.13.12 跑会在图片阶段提示缺库。
try:
    import io
    from PIL import Image
    HAS_PIL = True
except Exception:
    HAS_PIL = False


def shrink(data, path):
    """把服务器缓存图压成适合电视 WebView 的小 JPEG。"""
    if not HAS_PIL:
        return data
    # hero / 剧照用大一点，卡片与头像用小尺寸
    big = ("/l/public/" in path) or ("l_ratio_poster" in path) or ("/s_ratio_poster/" in path and "hero" in path)
    side = 960 if big else 420
    try:
        im = Image.open(io.BytesIO(data)).convert("RGB")
        im.thumbnail((side, side), Image.LANCZOS)
        out = io.BytesIO()
        im.save(out, "JPEG", quality=72, optimize=True, progressive=True)
        r = out.getvalue()
        return r if len(r) < len(data) else data
    except Exception:
        return data


def get(url, retry=2):
    last = None
    for _ in range(retry + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 hai-snapshot"})
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                return r.read()
        except Exception as e:
            last = e
            time.sleep(0.4)
    raise last


def norm_key(query):
    """归一化 query 供 jar 侧算 md5 定位静态页。"""
    # 注意：必须用「原始未解码」的 query 片段排序，jar 侧拿到的是同一串 raw query。
    # 一旦在这里 unquote，中文分类（%E5%89%A7%E6%83%85）就对不上了。
    d = {}
    for pair in (query or "").split("&"):
        if not pair:
            continue
        k, _, v = pair.partition("=")
        d[k] = v
    d.setdefault("pg", "1")
    return "&".join("%s=%s" % (k, d[k]) for k in sorted(d))


def md5(s):
    return hashlib.md5(s.encode("utf-8")).hexdigest()


def page_url(query, pg):
    d = {}
    for pair in (query or "").split("&"):
        if not pair:
            continue
        k, _, v = pair.partition("=")
        d[k] = v
    d["pg"] = str(pg)
    return BASE + "/hai/poster/?" + "&".join("%s=%s" % (k, v) for k, v in d.items())


def queries_from_wall():
    """从内置墙页里抽出所有分类链接（HTML 里是 &amp;）。"""
    s = open(os.path.join(ASSETS, "wall.html"), encoding="utf-8", errors="replace").read()
    out = [""]
    for m in re.findall(r"['\"](?:https?://[^'\"]*)?/hai/poster/\?([^'\"<>\s]+)", s):
        q = m.replace("&amp;", "&")
        if "src=douban" not in q and "src=tmdb" not in q:
            q += "&src=douban" if q else "src=douban"
        if q not in out:
            out.append(q)
    return out


def extract_css(html):
    blocks = re.findall(r"<style[^>]*>(.*?)</style>", html, re.S | re.I)
    return "\n".join(blocks), re.sub(r"<style[^>]*>.*?</style>", "", html, flags=re.S | re.I)


def clean_wall(html, css_link):
    css, html = extract_css(html)
    html = html.replace(BASE, "").replace("http://10.0.0.11:8099", "")
    html = html.replace("<head>", "<head><link rel=\"stylesheet\" href=\"" + css_link + "\">", 1)
    # 与 APK 内置 wall.html 同一处理：翻页不要按 location.pathname 拼，
    # 否则整页导航到 /hai/poster/d/ 时会拼出详情路由。
    html = re.sub(r"(return\s+)location\.pathname(\s*\+)", r"\1'/hai/poster/'\2", html)
    # 切源 302 / 演员页 / 追剧等服务端接口：本地 jar 会兜底，无需改链接
    return html, css


def clean_detail(html, vid, css_link):
    css, html = extract_css(html)
    html = html.replace(BASE, "").replace("http://10.0.0.11:8099", "")
    html = html.replace("<head>", "<head><link rel=\"stylesheet\" href=\"" + css_link + "\">", 1)

    # 主播放按钮已是 haiplay://search?q=...；把 legacy 单集直达也改成同一条 PG 原链路
    html = re.sub(
        r"var\s+SEL\s*=\s*'haiplay://play\?site=TMDBWall&id='[^\n;]*;",
        "var SEL = 'haiplay://search?q=' + encodeURIComponent(TITLE);", html)
    html = html.replace("haiplay://play?site=TMDBWall&id=", "haiplay://search?q=")

    # 服务端动态取链 / 选集合并 / 预热 / 进度 / 演职员补全 → 本地空响应
    for ep in ("eps", "play", "progress", "warm", "cast"):
        html = re.sub(r"/hai/poster/" + ep + r"\?[^\'\"]*", "/hai/offline/empty.json", html)
    html = html.replace("/hai/poster/eps?vid=", "/hai/offline/empty.json?v=")
    html = re.sub(r"/hai/poster/(eps|play|progress|warm|cast)\?[^\'\"]*",
                  "/hai/offline/empty.json", html)

    # 站内搜索按钮改成 PG 搜索深链（不再请求服务器 /hai/poster/q）
    html = re.sub(r"href=\"/hai/poster/q\?kw=([^\"]+)\"", r"href=\"haiplay://search?q=\1\"", html)

    # 详情/墙页内联脚本会给 #btnPlay 绑 click 抢先自建取链（「本地取链…」+ 遮罩），
    # 快照里那些接口已指向本地空对象，点了会卡住。用 cloneNode 替换掉按钮：
    # DOM 监听器不随克隆复制，href 的 haiplay://search 才能直接生效。
    patch = ("<script>/* offline-snapshot: play -> PG original chain only */"
             "(function(){try{var b=document.getElementById('btnPlay');"
             "if(!b||!b.parentNode)return;var c=b.cloneNode(true);"
             "c.innerHTML='\\u25b6 \\u64ad\\u653e';c.removeAttribute('data-ready');"
             "b.parentNode.replaceChild(c,b);}catch(e){}})();</script>")
    html = html.replace("</body>", patch + "</body>", 1) if "</body>" in html else html + patch
    return html, css


def collect_paths(html):
    vids = set(re.findall(r"/hai/poster/d/([A-Za-z0-9_]+)", html))
    imgs = set(re.findall(r"(/hai/dbimg/[A-Za-z0-9_/.\-]+\.(?:jpg|jpeg|png|webp))", html))
    return vids, imgs


def main():
    t0 = time.time()
    queries = queries_from_wall()
    print("分类组合: %d" % len(queries), flush=True)

    # ---------- 1) 抓墙页 ----------
    pages = {}          # key(md5) -> html bytes
    all_vids, all_imgs = set(), set()
    wall_css = ""
    wall_jobs = []
    for q in queries:
        for pg in range(1, MAX_PAGES + 1):
            wall_jobs.append((q, pg))

    def fetch_wall(job):
        q, pg = job
        key = md5(norm_key(page_url(q, pg).split("?", 1)[1]))
        try:
            b = get(page_url(q, pg))
            return q, pg, key, b
        except Exception as e:
            return q, pg, key, None

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
        for q, pg, key, b in ex.map(fetch_wall, wall_jobs):
            if b is None:
                continue
            s = b.decode("utf-8", "replace")
            vids, imgs = collect_paths(s)
            if pg > 1 and not vids:
                continue
            all_vids |= vids
            all_imgs |= imgs
            pages[key] = b
            if not wall_css:
                wall_css = extract_css(s)[0]

    print("墙页静态文件: %d  影片 vid: %d  图片: %d  耗时 %.1fs" %
          (len(pages), len(all_vids), len(all_imgs), time.time() - t0), flush=True)

    # ---------- 2) 抓详情 ----------
    vids = sorted(all_vids)[:MAX_DETAIL]
    details, detail_css = {}, ""

    def fetch_detail(vid):
        try:
            return vid, get(BASE + "/hai/poster/d/" + vid)
        except Exception:
            return vid, None

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
        for vid, b in ex.map(fetch_detail, vids):
            if b is None:
                continue
            s = b.decode("utf-8", "replace")
            v2, i2 = collect_paths(s)
            all_imgs |= i2
            details[vid] = b
            if not detail_css:
                detail_css = extract_css(s)[0]

    print("详情页静态文件: %d  累计图片: %d  耗时 %.1fs" %
          (len(details), len(all_imgs), time.time() - t0), flush=True)

    # ---------- 3) 统计图片体积 ----------
    imgs = sorted(all_imgs)[:MAX_IMAGES]
    if SCAN_ONLY:
        print("SCAN-ONLY: 图片 %d 张；墙页 CSS %.1f KB、详情 CSS %.1f KB；Pillow=%s" %
              (len(imgs), len(wall_css) / 1024.0, len(detail_css) / 1024.0, HAS_PIL), flush=True)
        return

    # ---------- 4) 落盘 ----------
    def w(rel, data):
        p = os.path.join(ASSETS, rel.replace("/", os.sep))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        mode = "wb" if isinstance(data, bytes) else "w"
        kw = {} if isinstance(data, bytes) else {"encoding": "utf-8"}
        with open(p, mode, **kw) as f:
            f.write(data)

    w("ui/wall.css", wall_css + "\n/* 离线快照：选集区不再由服务端供数 */\n#epSec{display:none!important}\n")
    w("ui/detail.css", detail_css + "\n/* 离线快照：隐藏 legacy 选集区 */\n#epSec{display:none!important}\n")
    for key, b in pages.items():
        s, _ = clean_wall(b.decode("utf-8", "replace"), "/hai/ui/wall.css")
        w("pages/" + key + ".html", s.encode("utf-8"))
    for vid, b in details.items():
        s, _ = clean_detail(b.decode("utf-8", "replace"), vid, "/hai/ui/detail.css")
        w("detail/" + vid + ".html", s.encode("utf-8"))

    # haiplay.js 固化
    try:
        w("lib/haiplay.js", get(BASE + "/hai/lib/haiplay.js"))
    except Exception as e:
        print("haiplay.js 拉取失败(可忽略):", e, flush=True)

    # 缺图占位：给一张深色渐变卡，避免整墙灰块
    if HAS_PIL:
        from PIL import ImageDraw
        ph = Image.new("RGB", (240, 360), (18, 22, 30))
        dr = ImageDraw.Draw(ph)
        for y in range(360):
            k = min(40, y // 9)
            dr.line([(0, y), (240, y)], fill=(18 + k, 22 + k, 30 + k))
        pb = io.BytesIO()
        ph.save(pb, "JPEG", quality=70)
        w("dbimg/_placeholder.jpg", pb.getvalue())

    # 图片：下载后立刻压成小 JPEG 再落盘，控制 APK 增量
    stat = {"ok": 0, "raw": 0, "out": 0, "fail": 0}

    def dl(p):
        try:
            b = get(BASE + p, retry=1)
            if len(b) < 100:
                stat["fail"] += 1
                return False
            c = shrink(b, p)
            w("dbimg/" + p.split("/hai/dbimg/", 1)[1], c)
            stat["ok"] += 1
            stat["raw"] += len(b)
            stat["out"] += len(c)
            return True
        except Exception:
            stat["fail"] += 1
            return False

    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as ex:
        list(ex.map(dl, imgs))

    manifest = {
        "built": time.strftime("%Y-%m-%d %H:%M:%S"),
        "source": "offline snapshot of poster wall (no runtime server dependency)",
        "pages": len(pages), "details": len(details), "images": stat["ok"],
        "images_failed": stat["fail"],
        "images_raw_mb": round(stat["raw"] / 1048576.0, 2),
        "images_packed_mb": round(stat["out"] / 1048576.0, 2),
        "page_key": "md5(normalized query)",
        "css_shared": True,
        "play": "haiplay://search?q=<title> (PG original chain)",
    }
    w("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
    print("导出完成: pages=%d detail=%d images=%d 耗时 %.1fs" %
          (len(pages), len(details), stat["ok"], time.time() - t0), flush=True)
    print(json.dumps(manifest, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
