# -*- coding: utf-8 -*-
"""豪华海报墙 · 中枢自托管网页（暗色 / 霓虹 / 大图）

为什么另做一套网页
------------------
app 原生详情页的排版在 pg.jar 里，改不了；豆瓣那个网页的样式也是 jar 注入的 CSS
（强制白底）。要真正的「豪华海报墙」，只能自己出一张页面。

这一页的特点
------------
* **服务端渲染**：电视盒子 / 老 WebView 也能开（不依赖现代 JS）
* **只用 flex + 基础 CSS**：避开 CSS Grid/flex-gap（Chrome 84+）等新特性，
  老引擎不会塌版
* 配色走深色科幻：近黑底 + 青绿/紫霓虹发光，海报自带评分角标
* 数据全部复用 `core/tmdbwall.py`（同一份 TMDB 缓存，不额外打接口）
"""
from __future__ import annotations

import html

from . import tmdbwall as T

_CSS = """
*{box-sizing:border-box;margin:0;padding:0}
body{background:#070a10;color:#e8edf6;
 font-family:-apple-system,"PingFang SC","Microsoft YaHei","Noto Sans CJK SC",sans-serif;
 min-height:100vh;
 background-image:radial-gradient(1200px 600px at 12% -8%,rgba(0,224,138,.16),transparent 60%),
                  radial-gradient(900px 500px at 92% 4%,rgba(168,85,247,.18),transparent 60%),
                  radial-gradient(700px 400px at 50% 110%,rgba(0,224,138,.08),transparent 65%);
 background-attachment:fixed}
a{color:inherit;text-decoration:none}
.wrap{max-width:1440px;margin:0 auto;padding:28px 30px 60px}
.brand{display:flex;align-items:baseline;margin-bottom:22px}
.brand b{font-size:26px;letter-spacing:3px;color:#00e08a;
 text-shadow:0 0 12px rgba(0,224,138,.75),0 0 34px rgba(0,224,138,.35)}
.brand span{margin-left:14px;font-size:13px;color:#7d8aa3;letter-spacing:1px}

/* 顶部大图 */
.hero{position:relative;border-radius:18px;overflow:hidden;margin-bottom:26px;
 border:1px solid rgba(0,224,138,.22);
 box-shadow:0 0 0 1px rgba(168,85,247,.10) inset,0 24px 60px rgba(0,0,0,.62),
            0 0 42px rgba(0,224,138,.10)}
.hero .bg{display:block;width:100%;height:auto;min-height:260px;object-fit:cover;opacity:.92}
.hero .shade{position:absolute;left:0;right:0;bottom:0;top:0;
 background:linear-gradient(90deg,rgba(7,10,16,.97) 0%,rgba(7,10,16,.86) 38%,
            rgba(7,10,16,.35) 72%,rgba(7,10,16,.65) 100%)}
.hero .info{position:absolute;left:38px;bottom:34px;right:44%;}
.hero h1{font-size:34px;line-height:1.24;margin-bottom:12px;
 text-shadow:0 2px 18px rgba(0,0,0,.85)}
.chips{margin-bottom:12px}
.chip{display:inline-block;margin:0 8px 8px 0;padding:3px 11px;border-radius:999px;
 font-size:12px;letter-spacing:.6px;color:#9ff5cd;
 border:1px solid rgba(0,224,138,.45);background:rgba(0,224,138,.10)}
.chip.p{color:#dcc4ff;border-color:rgba(168,85,247,.5);background:rgba(168,85,247,.12)}
.chip.score{color:#0a0d14;background:#00e08a;border-color:#00e08a;font-weight:700;
 box-shadow:0 0 16px rgba(0,224,138,.6)}
.hero p.desc{font-size:14px;line-height:1.75;color:#b9c4d6;max-height:5.2em;overflow:hidden}

/* 分类 */
.tabs{margin:0 0 20px}
.tabs a{display:inline-block;margin:0 10px 10px 0;padding:8px 16px;border-radius:10px;
 font-size:14px;color:#93a1b8;background:rgba(255,255,255,.035);
 border:1px solid rgba(255,255,255,.07);transition:none}
.tabs a.on{color:#00e08a;border-color:rgba(0,224,138,.55);
 background:rgba(0,224,138,.10);box-shadow:0 0 18px rgba(0,224,138,.22)}

/* 海报墙 */
.wall{display:flex;flex-wrap:wrap;margin:0 -9px}
.card{width:16.66%;padding:0 9px;margin-bottom:26px}
@media (max-width:1180px){.card{width:20%}}
@media (max-width:900px){.card{width:25%}}
@media (max-width:640px){.card{width:33.33%}}
.card .pic{position:relative;border-radius:12px;overflow:hidden;background:#111722;
 border:1px solid rgba(255,255,255,.07)}
.card .pic img{display:block;width:100%;height:auto;min-height:120px}
.card .pic .badge{position:absolute;top:8px;right:8px;padding:2px 8px;border-radius:8px;
 font-size:12px;font-weight:700;color:#00e08a;background:rgba(7,10,16,.82);
 border:1px solid rgba(0,224,138,.45)}
.card h3{margin-top:9px;font-size:14px;font-weight:500;line-height:1.45;color:#dfe7f3;
 max-height:2.9em;overflow:hidden}
.card .sub{margin-top:4px;font-size:12px;color:#6f7c93}

/* 详情页 */
.dtop{display:flex;margin-bottom:26px}
.dtop .poster{width:300px;flex:none;margin-right:30px}
.dtop .poster img{width:100%;border-radius:14px;border:1px solid rgba(0,224,138,.22);
 box-shadow:0 0 34px rgba(0,224,138,.12),0 18px 44px rgba(0,0,0,.6)}
.dtop .meta{flex:1;padding-top:6px}
.dtop h1{font-size:32px;margin-bottom:14px;text-shadow:0 2px 16px rgba(0,0,0,.7)}
.dtop .desc{font-size:15px;line-height:1.9;color:#c2cddd;white-space:pre-wrap}
.kv{margin-top:16px;font-size:13px;color:#8d9bb2;line-height:2}
.kv b{color:#9ff5cd;font-weight:500;margin-right:8px}
.sect{margin:26px 0 14px;font-size:15px;letter-spacing:2px;color:#a855f7;
 text-shadow:0 0 14px rgba(168,85,247,.55)}
.ep{display:inline-block;margin:0 9px 9px 0;padding:8px 15px;border-radius:9px;
 font-size:13px;color:#d8f7ea;background:rgba(0,224,138,.08);
 border:1px solid rgba(0,224,138,.3)}
.back{display:inline-block;margin-bottom:18px;font-size:14px;color:#7d8aa3}
.foot{margin-top:38px;font-size:12px;color:#4d5769;letter-spacing:1px}
"""


def _e(s) -> str:
    return html.escape(str(s or ""))


def _page(title: str, body: str) -> str:
    return (
        "<!DOCTYPE html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
        f"<title>{_e(title)}</title><style>{_CSS}</style></head><body>"
        f"<div class=\"wrap\">{body}"
        "<div class=\"foot\">TMDB 海报墙 · hai 本地包中枢自托管</div></div>"
        "</body></html>"
    )


def _card(it: dict) -> str:
    return (
        f"<a class=\"card\" href=\"/hai/poster/d/{_e(it.get('vod_id'))}\">"
        f"<div class=\"pic\"><img src=\"{_e(it.get('vod_pic'))}\" loading=\"lazy\" alt=\"\">"
        f"<span class=\"badge\">{_e(it.get('vod_remarks'))}</span></div>"
        f"<h3>{_e(it.get('vod_name'))}</h3>"
        f"<div class=\"sub\">{_e(it.get('vod_year'))}</div></a>"
    )


def page_home(type_id: int = 1, page: int = 1) -> str:
    data = T.list_page(type_id, page)
    items = data.get("list") or []

    hero = ""
    if items:
        top = items[0]
        d = T.detail(top.get("vod_id"), with_plays=False) if top.get("vod_id") else {}
        bg = (d or {}).get("vod_pic_slide") or top.get("vod_pic") or ""
        desc = (d or {}).get("vod_content", "").split("\n\n")[0][:180]
        chips = []
        if top.get("vod_remarks"):
            chips.append(f"<span class=\"chip score\">{_e(top['vod_remarks'])}</span>")
        if top.get("vod_year"):
            chips.append(f"<span class=\"chip\">{_e(top['vod_year'])}</span>")
        if (d or {}).get("vod_area"):
            chips.append(f"<span class=\"chip p\">{_e(d['vod_area'])}</span>")
        if (d or {}).get("type_name"):
            chips.append(f"<span class=\"chip\">{_e(d['type_name'])}</span>")
        hero = (
            "<div class=\"hero\">"
            f"<img class=\"bg\" src=\"{_e(bg)}\" alt=\"\">"
            "<div class=\"shade\"></div>"
            "<div class=\"info\">"
            f"<h1>{_e(top.get('vod_name'))}</h1>"
            f"<div class=\"chips\">{''.join(chips)}</div>"
            f"<p class=\"desc\">{_e(desc)}</p>"
            "</div></div>"
        )

    tabs = "".join(
        f"<a class=\"{'on' if tid == type_id else ''}\" "
        f"href=\"/hai/poster/?t={tid}\">{_e(name)}</a>"
        for tid, name, _, _ in T.CLASSES
    )
    cards = "".join(_card(x) for x in items[1:])
    body = (
        "<div class=\"brand\"><b>海报墙</b><span>TMDB · 深色影院</span></div>"
        f"{hero}<div class=\"tabs\">{tabs}</div><div class=\"wall\">{cards}</div>"
    )
    return _page("海报墙 · TMDB", body)


def page_detail(vid: str) -> str:
    d = T.detail(vid)
    if not d:
        return _page("未找到", "<div class=\"brand\"><b>海报墙</b></div>"
                              "<p class=\"desc\">TMDB 没有这条数据。</p>")
    bg = d.get("vod_pic_slide") or ""
    chips = []
    if d.get("vod_remarks"):
        chips.append(f"<span class=\"chip score\">{_e(d['vod_remarks'])}</span>")
    for k in ("vod_year", "vod_area", "vod_lang"):
        if d.get(k):
            chips.append(f"<span class=\"chip\">{_e(d[k])}</span>")
    if d.get("type_name"):
        chips.append(f"<span class=\"chip p\">{_e(d['type_name'])}</span>")

    kv = []
    if d.get("vod_director"):
        kv.append(f"<div><b>导演</b>{_e(d['vod_director'])}</div>")
    if d.get("vod_actor"):
        kv.append(f"<div><b>主演</b>{_e(d['vod_actor'])}</div>")
    # 只取简介正文：导演/主演/TMDB 评分在下面单独排版，避免重复出现两遍
    desc = str(d.get("vod_content") or "").split("\n\n")[0]

    # 播放源（m3u8 直链；电视上更建议回到 app 里用原生播放器）
    eps = ""
    if d.get("vod_play_url"):
        names = str(d.get("vod_play_from") or "").split("$$$")
        groups = str(d.get("vod_play_url")).split("$$$")
        html_eps = []
        for gi, g in enumerate(groups[:3]):
            src = names[gi] if gi < len(names) else "源%d" % (gi + 1)
            html_eps.append(f"<div class=\"sect\">{_e(src)}</div>")
            for ep in g.split("#")[:40]:
                if "$" not in ep:
                    continue
                nm, url = ep.split("$", 1)
                html_eps.append(f"<a class=\"ep\" href=\"{_e(url)}\">{_e(nm)}</a>")
        eps = "".join(html_eps)

    body = (
        f"<a class=\"back\" href=\"/hai/poster/\">← 返回海报墙</a>"
        "<div class=\"dtop\">"
        f"<div class=\"poster\"><img src=\"{_e(d.get('vod_pic'))}\" alt=\"\"></div>"
        "<div class=\"meta\">"
        f"<h1>{_e(d.get('vod_name'))}</h1>"
        f"<div class=\"chips\">{''.join(chips)}</div>"
        f"<div class=\"desc\">{_e(desc)}</div>"
        f"<div class=\"kv\">{''.join(kv)}</div>"
        "</div></div>"
        + (f"<div class=\"sect\">播放源</div>{eps}" if eps else "")
        + (f"<div class=\"hero\" style=\"margin-top:28px\">"
           f"<img class=\"bg\" src=\"{_e(bg)}\" alt=\"\"></div>" if bg else "")
    )
    return _page(f"{d.get('vod_name')} · 海报墙", body)
