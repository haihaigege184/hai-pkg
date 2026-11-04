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
import json
import os
import re
import threading
import urllib.parse

from . import tmdbwall as T

# ---------------------------------------------------------------------------
# 海报墙配置（维护页 /live 的「海报墙配置」卡可改）
# 持久化到 data/poster.cfg.json —— data/ 是服务器状态目录，不进部署包，
# 也不参与 /api/verify 的文件比对，随便改。
# ---------------------------------------------------------------------------
_CFG_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "data", "poster.cfg.json")
_CFG_DEFAULT = {"rotate": True, "interval": 3, "effect": "random", "imgs": 6}
_FX = ("fade", "zoom", "slide", "ken")
_cfg_lock = threading.Lock()


def load_cfg() -> dict:
    out = dict(_CFG_DEFAULT)
    try:
        with open(_CFG_PATH, encoding="utf-8") as f:
            d = json.load(f)
        for k in _CFG_DEFAULT:
            if k in d:
                out[k] = d[k]
    except Exception:
        pass
    out["interval"] = max(1, min(15, int(out["interval"] or 3)))
    out["imgs"] = max(1, min(8, int(out["imgs"] or 6)))
    if out["effect"] not in _FX + ("random",):
        out["effect"] = "random"
    out["rotate"] = bool(out["rotate"])
    return out


def save_cfg(d: dict) -> dict:
    cur = load_cfg()
    for k in _CFG_DEFAULT:
        if k in (d or {}):
            cur[k] = d[k]
    cur["interval"] = max(1, min(15, int(cur["interval"] or 3)))
    cur["imgs"] = max(1, min(8, int(cur["imgs"] or 6)))
    if cur["effect"] not in _FX + ("random",):
        cur["effect"] = "random"
    cur["rotate"] = bool(cur["rotate"])
    with _cfg_lock:
        try:
            os.makedirs(os.path.dirname(_CFG_PATH), exist_ok=True)
            with open(_CFG_PATH, "w", encoding="utf-8") as f:
                json.dump(cur, f, ensure_ascii=False, indent=1)
        except Exception:
            pass
    return cur


# ---------------------------------------------------------------------------
# 数据源：tmdb / douban（2026-10-31 海哥需求）
# ---------------------------------------------------------------------------
#
# 为什么存在：TMDB 的国产剧榜单更新太慢，「嫁金钗」这种当下最热的剧在
# TMDB 热门剧集里根本没有。豆瓣榜单更贴国内观众，所以做成可切换的第二
# 数据源 —— 电视上按遥控**设置键**呼出菜单就能换，选择存在服务端，
# 换一台设备打开也是同一个数据源。
#
# 两套数据的 `vod_id` 前缀不同（tmdb 用 m/t 开头，豆瓣统一 `db`），
# 详情页据此自动选模块，不需要额外参数。

_SRC_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "data", "poster.src.json")
_SRC_ALL = ("tmdb", "douban")
_src_lock = threading.Lock()


def load_src() -> str:
    try:
        with open(_SRC_PATH, "r", encoding="utf-8") as f:
            s = str((json.load(f) or {}).get("src") or "").strip()
        return s if s in _SRC_ALL else "tmdb"
    except Exception:
        return "tmdb"


def save_src(src: str) -> str:
    src = str(src or "").strip()
    if src not in _SRC_ALL:
        src = "tmdb"
    with _src_lock:
        try:
            os.makedirs(os.path.dirname(_SRC_PATH), exist_ok=True)
            with open(_SRC_PATH, "w", encoding="utf-8") as f:
                json.dump({"src": src}, f, ensure_ascii=False)
        except Exception:
            pass
    return src


def _mod(src: str):
    """按数据源取模块。接口只要 list_page / detail / classes 三样。"""
    if src == "douban":
        from . import doubanx as D
        return D
    from . import tmdbwall as T
    return T


def _classes(src: str) -> list[tuple[str, str]]:
    """统一成 [(cid(str), 名称)]，**不含「我的追剧」**。

    我的追剧是本地列表（watchlist），两个数据源共用同一份，所以由页面单独
    加在类目条最前面。豆瓣的 cid 是 `db2`..`db11`，TMDB 是 `2`..`11` ——
    数字部分一一对应（见 doubanx._DB_CLASS），切源后同一个位置还是同一类。
    """
    if src == "douban":
        from . import doubanx as D
        return [(str(a), b) for a, b in D.classes()]
    return [(str(c[0]), c[1]) for c in T.CLASSES if c[0] != T.WATCH_TYPE]


def _filters(src: str):
    """筛选三行：地区 / 排序 / 类型。两套数据各用各的（豆瓣走 tag）。"""
    if src == "douban":
        from . import doubanx as D
        return D.COUNTRIES, D.SORTS, D.GENRES
    # T.GENRES 是 4 元组（key, 名称, 动画/真人标记, 语言），筛选面板只要前两位
    return T.COUNTRIES, T.SORTS, [(k, n) for k, n, _, _ in T.GENRES]


def _src_name(src: str) -> str:
    return "豆瓣" if src == "douban" else "TMDB"


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
.brand b{font-size:34px;letter-spacing:3px;color:#00e08a;
 text-shadow:0 0 12px rgba(0,224,138,.75),0 0 34px rgba(0,224,138,.35)}
.brand span{margin-left:14px;font-size:17px;color:#7d8aa3;letter-spacing:1px}

/* 满屏背景大图（固定层，双层轮播见 #heroFx；内容在其上滚动） */
/* 2026-11-01 海哥：第一屏全亮度看剧照，翻下去当背景时再压暗。
   以前一进来就是 .50/.74/.95 的三段压暗，剧照基本看不出画面。 */
#herobgshade{position:fixed;top:0;left:0;width:100vw;height:100vh;z-index:-1;
 pointer-events:none;transition:background .55s ease;
 background:linear-gradient(180deg,rgba(7,10,16,0) 0%,rgba(7,10,16,.10) 46%,
            rgba(7,10,16,.55) 82%,rgba(7,10,16,.78) 100%)}
body.below #herobgshade{
 background:linear-gradient(180deg,rgba(7,10,16,.72) 0%,rgba(7,10,16,.88) 55%,
            rgba(7,10,16,.96) 100%)}
/* 顶部信息区：透明容器，叠在满屏背景之上，标题/按钮位置不变。
   2026-10-30 海哥：整体向上提（bottom 40→150）—— 二屏演职员上移后
   左上不再露出播放按钮下沿；进演职员/推荐态再把整块淡出（双保险）。 */
.hero{position:relative;min-height:100vh;margin-bottom:26px}
.hero .info{position:absolute;left:38px;bottom:150px;right:44%;transition:opacity .45s ease}
body.below .hero .info{opacity:0;pointer-events:none}
.hero h1{font-size:42px;line-height:1.24;margin-bottom:12px;
 text-shadow:0 2px 18px rgba(0,0,0,.85)}
.chips{margin-bottom:12px}
.chip{display:inline-block;margin:0 8px 8px 0;padding:4px 13px;border-radius:999px;
 font-size:15px;letter-spacing:.6px;color:#9ff5cd;
 border:1px solid rgba(0,224,138,.45);background:rgba(0,224,138,.10)}
.chip.p{color:#dcc4ff;border-color:rgba(168,85,247,.5);background:rgba(168,85,247,.12)}
.chip.score{color:#0a0d14;background:#00e08a;border-color:#00e08a;font-weight:700;
 box-shadow:0 0 16px rgba(0,224,138,.6)}
.hero p.desc{font-size:17px;line-height:1.75;color:#b9c4d6;max-height:5.2em;overflow:hidden}

/* 分类 */
.tabs{margin:0 0 14px}
.tabs a{display:inline-block;margin:0 12px 12px 0;padding:10px 20px;border-radius:11px;
 font-size:19px;color:#93a1b8;background:rgba(255,255,255,.035);
 border:1px solid rgba(255,255,255,.07);transition:none}
.tabs a.on{color:#00e08a;border-color:rgba(0,224,138,.55);
 background:rgba(0,224,138,.10);box-shadow:0 0 18px rgba(0,224,138,.22)}
.tabs a:focus,.card:focus{outline:none}
.tabs a:focus,.tabs a.sel{color:#0a0d14;background:#00e08a;border-color:#00e08a;
 box-shadow:0 0 22px rgba(0,224,138,.6)}
.tabs a.fbtn{color:#dcc4ff;border-color:rgba(168,85,247,.5);
 background:rgba(168,85,247,.12)}
.tabs a.fbtn:focus,.tabs a.fbtn.sel{color:#0a0d14;background:#a855f7;border-color:#a855f7;
 box-shadow:0 0 22px rgba(168,85,247,.65)}
/* 数据源分段开关（豆瓣 / TMDB）—— 常驻在类目条最前面 */
.tabs a.srcbtn{color:#dcc4ff;border-color:rgba(168,85,247,.45);
 background:rgba(168,85,247,.10);font-weight:600;letter-spacing:1px}
.tabs a.srcbtn.on{color:#0a0d14;background:linear-gradient(135deg,#a855f7,#7c3aed);
 border-color:#a855f7;box-shadow:0 0 20px rgba(168,85,247,.5)}
.tabs a.srcbtn.sel{color:#0a0d14;background:#fff;border-color:#fff;
 box-shadow:0 0 22px rgba(255,255,255,.55)}
.tabs i.tsep{display:inline-block;width:1px;height:20px;margin:0 4px 10px 2px;
 background:rgba(255,255,255,.16);vertical-align:middle}
.fsum{font-size:12px;color:#6ea88f;letter-spacing:1px;margin-left:2px}
.fsum b{color:#00e08a;font-weight:600}
/* 海报墙首图横幅（2026-11-01 新做）。
   原来首页 hero 用的是详情页那套 .hero/.bg/.shade，但那三条样式**从来没定义过**
   ——结果是竖版海报被当成普通图片流式排版，撑满 100vh、类目条被顶到屏外。
   这里给首页一套独立的横幅样式：横图铺满 + 左侧渐变压暗 + 文案压底。 */
.whero{position:relative;height:44vh;min-height:260px;border-radius:16px;
 overflow:hidden;margin:2px 0 20px;background:#0b1018;
 border:1px solid rgba(0,224,138,.14);
 box-shadow:0 18px 46px rgba(0,0,0,.55)}
.whero .bg{position:absolute;top:0;left:0;width:100%;height:100%;
 object-fit:cover;object-position:center 28%}
.whero .shade{position:absolute;top:0;left:0;width:100%;height:100%;
 background:linear-gradient(90deg,rgba(7,10,16,.96) 0%,rgba(7,10,16,.80) 42%,
            rgba(7,10,16,.25) 72%,rgba(7,10,16,.55) 100%)}
.whero .info{position:absolute;left:34px;right:44%;bottom:30px}
.whero h1{font-size:30px;line-height:1.25;margin-bottom:10px;
 text-shadow:0 2px 18px rgba(0,0,0,.9)}
.whero p.desc{font-size:13.5px;line-height:1.75;color:#b9c4d6;
 display:-webkit-box;-webkit-box-orient:vertical;-webkit-line-clamp:3;
 max-height:5.25em;overflow:hidden}

/* 海报墙 */
.wall{display:flex;flex-wrap:wrap;margin:0 -9px}
/* 2026-11-03 海哥：每行 4 个（电视 1280 宽下原先是 6 个，海报太小） */
.card{width:25%;padding:0 9px;margin-bottom:26px}
@media (max-width:1180px){.card{width:25%}}
@media (max-width:900px){.card{width:33.33%}}
@media (max-width:640px){.card{width:50%}}
.card .pic{position:relative;border-radius:12px;overflow:hidden;background:#111722;
 border:1px solid rgba(255,255,255,.07)}
.card .pic img{display:block;width:100%;height:auto;min-height:120px}
.card .pic .badge{position:absolute;top:8px;right:8px;padding:2px 8px;border-radius:8px;
 font-size:12px;font-weight:700;color:#00e08a;background:rgba(7,10,16,.82);
 border:1px solid rgba(0,224,138,.45)}
.card h3{margin-top:9px;font-size:14px;font-weight:500;line-height:1.45;color:#dfe7f3;
 max-height:2.9em;overflow:hidden}
.card .sub{margin-top:4px;font-size:12px;color:#6f7c93}
.card:focus,.card.sel{outline:none}
.card:focus .pic,.card.sel .pic{border-color:#00e08a;
 box-shadow:0 0 0 2px rgba(0,224,138,.55),0 0 26px rgba(0,224,138,.35)}
.card:focus h3,.card.sel h3{color:#9ff5cd}

/* 类目筛选面板（2026-10-31）：按 OK 在类目条上呼出，三组条件，
   每组一行横向 chip，选中即整页重渲染（服务端滤镜，翻页才不会断层） */
#fpanel{display:none;position:fixed;top:0;left:0;width:100vw;height:100vh;
 z-index:60;background:rgba(4,6,10,.86)}
#fpanel.show{display:block}
#fpanel .fbox{position:absolute;top:50%;left:50%;width:1090px;max-width:92vw;
 margin-top:-232px;margin-left:-545px;padding:26px 30px 20px;border-radius:18px;
 background:linear-gradient(160deg,rgba(16,22,32,.99),rgba(9,12,18,.99));
 border:1px solid rgba(0,224,138,.32);
 box-shadow:0 0 60px rgba(0,0,0,.8),0 0 40px rgba(0,224,138,.14)}
#fpanel .fh{font-size:19px;color:#00e08a;letter-spacing:3px;margin-bottom:6px;
 text-shadow:0 0 14px rgba(0,224,138,.6)}
#fpanel .fh i{font-style:normal;color:#8ea0bb;font-size:13px;letter-spacing:1px;
 margin-left:12px;text-shadow:none}
.frow{margin-top:18px}
.frow .fl{font-size:12px;color:#6f7c93;letter-spacing:3px;margin-bottom:9px}
.fopts{display:flex;flex-wrap:wrap;margin:0 -5px}
.fopt{display:inline-block;margin:0 5px 9px;padding:7px 15px;border-radius:9px;
 font-size:13.5px;color:#a9b6ca;background:rgba(255,255,255,.045);
 border:1px solid rgba(255,255,255,.08)}
.fopt.sel{color:#9ff5cd;border-color:rgba(0,224,138,.5);
 background:rgba(0,224,138,.12)}
.fopt:focus,.fopt.focus{outline:none;color:#0a0d14;background:#00e08a;border-color:#00e08a;
 box-shadow:0 0 20px rgba(0,224,138,.55)}
#fpanel .fk{margin-top:16px;font-size:12px;color:#5f6b80;letter-spacing:1px}

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
/* ---- 选集（2026-11-01）：站源行 + 季行 + 集按钮 -----------------------
   集数是从网盘分享的文件名里解析出来的（core/pan_ep.py），点某一集直接
   走 app 本地代理起播，不再进站源的选集页手动翻。 */
.epsrc,.epsea{display:flex;flex-wrap:wrap;margin:0 -5px 10px}
.ep.src,.ep.sea{margin:0 5px 9px;color:#d5c2fb;
 border-color:rgba(168,85,247,.42);background:rgba(168,85,247,.10)}
.ep.on{color:#04120c;background:#a855f7;border-color:#a855f7;font-weight:700}
.ep.off{opacity:.35}
.ep.sel{color:#04120c;background:#00e08a;border-color:#eafff5;
 box-shadow:0 0 0 3px rgba(0,224,138,.38),0 0 26px rgba(0,224,138,.72)}
.ep.src.sel,.ep.sea.sel{background:#a855f7;border-color:#f3e8ff;
 box-shadow:0 0 0 3px rgba(168,85,247,.38),0 0 26px rgba(168,85,247,.72)}
.epgrid{margin-top:2px}
.epwait{display:inline-block;padding:6px 0;font-size:13px;color:#6f7c93}
/* 聚合后的集按钮：右上角小绿点 = 这集有备份线路（其它网盘也有） */
.epgrid .ep{position:relative}
.epgrid .ep i.dot{position:absolute;right:5px;top:5px;width:6px;height:6px;
 border-radius:50%;background:#00e08a;box-shadow:0 0 7px rgba(0,224,138,.9)}
.epstat{margin:0 0 12px;font-size:12.5px;color:#6ea88f;letter-spacing:1.5px}
.epstat b{color:#00e08a;font-weight:600}
.epstat .esrc{color:#7f8ba1;letter-spacing:0}
/* ---- 点「第N集」弹出的线路浮层（2026-11-01）--------------------------
   为什么要有它：选集是真数据（12 集 / 每集 2 条线路），但**播放**必须交给
   原生播放器（网盘要转存、要 Cookie，网页播不了）。与其让集按钮假装能播、
   点下去却跳到一个不相干的地方，不如把这一集的全部线路（网盘 / 文件名 /
   体积）摊开给人看 —— 信息是真的，再一键交给播放器。
   APK 改造完成后这里每条线路换成 haiplay 直链，结构一行都不用重写。 */
#epBox{transition:opacity .2s ease}
#epDlg{position:fixed;top:0;left:0;right:0;bottom:0;z-index:70;display:none;
 background:rgba(4,6,11,.72);-webkit-backdrop-filter:blur(3px)}
#epDlg.on{display:block}
#epDlg .ebox{position:absolute;left:50%;top:50%;
 width:720px;margin-left:-360px;transform:translateY(-50%);
 background:linear-gradient(160deg,rgba(13,18,27,.985),rgba(9,13,20,.985));
 border:1px solid rgba(0,224,138,.30);border-radius:18px;padding:24px 26px 20px;
 box-shadow:0 0 0 1px rgba(168,85,247,.10) inset,0 24px 80px rgba(0,0,0,.75),
            0 0 40px rgba(0,224,138,.10)}
#epDlg .ebox:before{content:"";position:absolute;left:26px;right:26px;top:0;
 height:1px;background:linear-gradient(90deg,transparent,
 rgba(0,224,138,.85),rgba(168,85,247,.7),transparent)}
#epDlg h3{margin:0 0 5px;font-size:26px;color:#eafff5;letter-spacing:1px;
 text-shadow:0 0 16px rgba(0,224,138,.6)}
#epDlg .esub{font-size:12px;color:#6f7c93;letter-spacing:1.4px;margin-bottom:18px}
#epDlg .eroute{display:block;position:relative;padding:11px 15px;margin-bottom:9px;
 border-radius:11px;background:rgba(255,255,255,.035);
 border:1px solid rgba(255,255,255,.07)}
#epDlg .eroute .pan{display:inline-block;min-width:42px;padding:2px 9px;
 margin-right:11px;border-radius:6px;font-size:11.5px;font-weight:700;
 color:#04120c;background:#00e08a}
#epDlg .eroute .pan.uc{background:#7cc4ff}
#epDlg .eroute .pan.baidu{background:#8fb4ff}
#epDlg .eroute .pan.ali{background:#ffcf7a}
#epDlg .eroute .fn{font-size:13px;color:#dbe4f2}
#epDlg .eroute .sz{position:absolute;right:15px;top:11px;font-size:12px;color:#7f8ba1}
#epDlg .eroute.sel{border-color:#eafff5;background:rgba(0,224,138,.13);
 box-shadow:0 0 0 3px rgba(0,224,138,.30),0 0 26px rgba(0,224,138,.45)}
#epDlg .eroute .fn{display:inline-block;vertical-align:middle;
 max-width:430px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
#epDlg .efoot{margin-top:16px;padding-top:14px;
 border-top:1px solid rgba(255,255,255,.07)}
#epDlg .efoot .btn{margin-bottom:0}
#epDlg .enote{margin-top:11px;font-size:11.5px;color:#5d6a80;letter-spacing:.6px}
/* ---- 起播过渡（2026-11-01）------------------------------------------
   点「播放」或任一集时，先铺一张**全屏横版剧照**（就是屏幕上正在放的那张），
   再切到原生播放器。中间不出现黑屏、不出现站源选集页，播放链条全程看不到
   PG 原生界面。返回详情页时网页还在（Activity 栈在下面），遮罩自动撤掉并
   做一次淡入，观感上是「从播放器特效回到详情页」。 */
#curtain{position:fixed;top:0;left:0;right:0;bottom:0;z-index:9000;
 opacity:0;pointer-events:none;transition:opacity .26s ease;
 background:#05080d;overflow:hidden}
#curtain.on{opacity:1;pointer-events:auto}
#curtain .cbg{position:absolute;top:0;left:0;width:100%;height:100%;
 object-fit:cover;animation:czoom 14s ease-out forwards}
@keyframes czoom{from{transform:scale(1.02)}to{transform:scale(1.12)}}
#curtain .cshade{position:absolute;top:0;left:0;width:100%;height:100%;
 background:linear-gradient(180deg,rgba(5,8,13,.10) 0%,rgba(5,8,13,.28) 42%,
 rgba(5,8,13,.90) 100%)}
#curtain .ctbox{position:absolute;left:64px;bottom:10vh;max-width:60%}
#curtain .ctname{font-size:40px;font-weight:700;color:#fff;letter-spacing:2px;
 text-shadow:0 4px 24px rgba(0,0,0,.8)}
#curtain .ctsub{margin-top:12px;font-size:16px;color:#9ff5cd;letter-spacing:3px}
/* 状态行（2026-11-03 海哥要「加载状态/片源信息/缓冲」）：一行一行的短句，
   解析阶段由 /hai/poster/progress 轮询刷新，字号比片名小、比副标题亮。 */
#curtain .ctstat{margin-top:14px;font-size:19px;color:#cfe9ff;letter-spacing:1px;
 line-height:1.5;text-shadow:0 2px 12px rgba(0,0,0,.85);min-height:26px}
#curtain .ctstat b{color:#00e08a;font-weight:600}
#curtain .ctstat .slow{color:#ffcc55}
#curtain .ctstat .dim{color:#8fa8bd}
#curtain .cbar{margin-top:16px;width:320px;height:4px;border-radius:2px;
 background:rgba(255,255,255,.14);overflow:hidden}
#curtain .cbar i{display:block;height:100%;width:30%;border-radius:2px;
 background:linear-gradient(90deg,#00e08a,#39d0ff);animation:cbar 1.1s ease-in-out infinite}
@keyframes cbar{0%{margin-left:-30%}100%{margin-left:100%}}
#curtain .cspin{position:absolute;right:78px;bottom:11vh;width:34px;height:34px;
 border-radius:50%;border:2px solid rgba(0,224,138,.20);border-top-color:#00e08a;
 animation:cspin .85s linear infinite}
@keyframes cspin{to{transform:rotate(360deg)}}
/* 从播放器返回时整页淡入，别硬切 */
body.backfade #heroFx,body.backfade .hero,body.backfade #titleCard,
body.backfade #epSec,body.backfade #castSec,body.backfade #recSec{
 animation:bfade .5s ease-out}
@keyframes bfade{from{opacity:.2}to{opacity:1}}
.back{display:inline-block;margin-bottom:18px;font-size:14px;color:#7d8aa3}
.foot{font-size:15px;margin-top:38px;font-size:12px;color:#4d5769;letter-spacing:1px}

/* ---- 数据源切换浮层（遥控「设置」键呼出）---- */
#srcMenu{display:none;position:fixed;left:0;top:0;right:0;bottom:0;z-index:60;
 background:rgba(4,7,12,.82)}
#srcMenu.on{display:block}
#srcMenu .srcbox{position:absolute;left:50%;top:50%;width:640px;max-width:86vw;
 transform:translate(-50%,-50%);padding:26px 28px 20px;border-radius:18px;
 background:rgba(12,17,26,.97);border:1px solid rgba(0,224,138,.38);
 box-shadow:0 0 60px rgba(0,224,138,.22),0 24px 60px rgba(0,0,0,.6)}
#srcMenu .srctitle{font-size:15px;letter-spacing:3px;color:#9ff5cd;margin-bottom:16px}
#srcMenu .srcopts{display:flex;gap:14px}
#srcMenu .srcopt{flex:1;display:block;padding:20px 18px;border-radius:14px;
 text-decoration:none;color:#cfdae8;background:rgba(255,255,255,.04);
 border:1px solid rgba(255,255,255,.09);font-size:19px;letter-spacing:1px}
#srcMenu .srcopt small{display:block;margin-top:8px;font-size:12.5px;color:#77839a;
 letter-spacing:0}
#srcMenu .srcopt.sel{color:#04110b;background:#00e08a;border-color:#00e08a;
 box-shadow:0 0 30px rgba(0,224,138,.6)}
#srcMenu .srcopt.sel small{color:#0a3d28}
#srcMenu .srcopt.close{flex:0 0 96px;text-align:center;font-size:22px}
#srcMenu .srcopt.close small{font-size:12px}
#srcMenu .srcopt.cur:after{content:"当前";position:absolute;margin:-4px 0 0 6px;
 font-size:11px;color:#00e08a}
#srcMenu .srcopt.cur.sel:after{color:#0a3d28}
#srcMenu .srchint{margin-top:16px;font-size:12.5px;color:#5d697e;text-align:center}
.srcpill{float:right;padding:5px 12px;border-radius:9px;font-size:12.5px;
 text-decoration:none;color:#9ff5cd;border:1px solid rgba(0,224,138,.42);
 background:rgba(0,224,138,.09)}

/* 操作区：播放 / 追剧。电视上这两个是主按钮，所以做得比 chips 大一圈。
   注意：**默认态不能是实心亮色** —— 遥控器焦点只有一个，如果两个按钮都亮着，
   用户根本看不出光标在哪（实拍反馈过）。所以默认是描边态，只有 .sel 才实心。 */
.acts{margin:22px 0 6px}
.btn{display:inline-block;margin:0 12px 12px 0;padding:13px 30px;border-radius:12px;
 font-size:16px;letter-spacing:2px;border:2px solid rgba(0,224,138,.5);
 color:#9ff5cd;background:rgba(0,224,138,.07);font-weight:700;cursor:pointer;
 box-shadow:none;transition:background .12s,color .12s,box-shadow .12s}
.btn.ghost{color:#9ff5cd;background:rgba(0,224,138,.05);font-weight:500;
 box-shadow:none;border-color:rgba(0,224,138,.35)}
.btn.on{color:#dcc4ff;background:rgba(168,85,247,.10);border-color:rgba(168,85,247,.55);
 box-shadow:none}
.btn[disabled]{opacity:.42;box-shadow:none;cursor:default}
.line{display:inline-block;margin:0 10px 10px 0;padding:11px 24px;border-radius:10px;
 font-size:15px;color:#9ff5cd;background:rgba(0,224,138,.09);
 border:1px solid rgba(0,224,138,.34);transition:background .12s,color .12s}
/* 遥控器焦点：电视上没有鼠标，方向键左右切按钮，OK 键触发。
   .sel 态用「实心 + 白边 + 双圈光晕」，和默认描边态拉开明显差异。 */
.btn.sel{color:#04120c;background:#00e08a;border-color:#eafff5;
 box-shadow:0 0 0 3px rgba(0,224,138,.38),0 0 34px rgba(0,224,138,.78)}
.btn.on.sel{color:#0a0d14;background:#a855f7;border-color:#f3e8ff;
 box-shadow:0 0 0 3px rgba(168,85,247,.38),0 0 34px rgba(168,85,247,.82)}
.line.sel{color:#04120c;background:#00e08a;border-color:#eafff5;
 box-shadow:0 0 0 3px rgba(0,224,138,.38)}
.keys{margin-top:4px;font-size:12px;color:#4f5a6b;letter-spacing:1px}
/* 2026-11-01 海哥：类目条下的遥控提示整块删了 —— 页面要简洁高级感，
   筛选靠浮层自己说明（← → 选择　OK 确认），不再在页面上铺说明文字。 */
.empty{padding:40px 0;font-size:15px;color:#6f7c93}
.tip{margin-top:10px;font-size:12px;color:#5d6879;line-height:1.8}

/* ---- 影人页（点头像进来）：左头像 + 右资料 + 作品墙 ---- */
.phead{display:flex;margin:6px 0 26px}
.phead .pav{width:280px;flex:none;margin-right:32px}
.phead .pav img{width:100%;border-radius:16px;border:1px solid rgba(168,85,247,.3);
 box-shadow:0 0 34px rgba(168,85,247,.18),0 18px 44px rgba(0,0,0,.62)}
.phead .pinfo{flex:1;min-width:0;padding-top:4px;padding-right:56px}
.phead h1{font-size:32px;margin-bottom:6px;color:#eafff5;
 text-shadow:0 0 14px rgba(0,224,138,.55)}
.phead .platin{font-size:14px;color:#7d8aa3;letter-spacing:1px;margin-bottom:16px}
.phead .pintro{font-size:14px;line-height:1.9;color:#b9c4d6;
 display:-webkit-box;-webkit-box-orient:vertical;-webkit-line-clamp:7;
 max-height:13.3em;overflow:hidden}
.pinfo2{margin-top:14px;font-size:13px;color:#8d9bb2;line-height:2}
.pinfo2 b{color:#9ff5cd;font-weight:500;margin-right:10px}
.cip{color:#d8f7ea}
.ci a.sel,.cip.sel{color:#04120c;background:#00e08a;padding:1px 6px;border-radius:6px}
/* 影人作品墙：一行 6 张，比首页小一号，能一屏看到更多作品 */
#workSec .wall .card{width:25%}
@media(max-width:1180px){#workSec .wall .card{width:25%}}
@media(max-width:900px){#workSec .wall .card{width:33.33%}}

/* 演职员表（圆形头像 + 角色） */
.cast{display:flex;flex-wrap:wrap;margin:0 -7px 8px}
.citem{width:14.28%;padding:0 7px;margin-bottom:18px;text-align:center}
@media(max-width:1180px){.citem{width:16.66%}}
@media(max-width:900px){.citem{width:20%}}
@media(max-width:640px){.citem{width:25%}}
.cface{position:relative;width:100%;padding-top:100%;border-radius:50%;
 overflow:hidden;background:#0e141f;border:1px solid rgba(0,224,138,.18)}
.cface img{position:absolute;top:0;left:0;width:100%;height:100%;object-fit:cover}
.cface.noimg{display:flex;align-items:center;justify-content:center;
 color:#5d6879;font-size:22px;font-weight:700}
.cname{margin-top:8px;font-size:13px;color:#e6edf7;line-height:1.35}
.crole{font-size:11px;color:#8d9bb2;line-height:1.35;max-height:2.8em;overflow:hidden}
.crew{margin:0 -7px 6px}
.crew .ci{display:inline-block;margin:0 8px 8px 0;padding:6px 14px;border-radius:9px;
 font-size:13px;color:#d8f7ea;background:rgba(0,224,138,.07);
 border:1px solid rgba(0,224,138,.3)}
.crew .ci b{color:#9ff5cd;font-weight:500;margin-right:6px}
/* 「主演」那一串人名可能几十个字，chip 会撑满整行；单行省略，遥控焦点不受影响 */
.crew .ci.wide{max-width:100%;overflow:hidden;text-overflow:ellipsis;
 white-space:nowrap;display:inline-block;vertical-align:top}

/* 演职员头像：点按 = 在 App 里搜该演员（活链接） */
.cast a.sel .cface{border-color:#00e08a;
 box-shadow:0 0 0 3px rgba(0,224,138,.4),0 0 18px rgba(0,224,138,.5)}
.cast a.sel .cname{color:#9ff5cd}

/* 相关推荐卡：遥控选中态（和按钮共用 sel 机制） */
.card.sel .pic{border-color:#00e08a;
 box-shadow:0 0 0 3px rgba(0,224,138,.4),0 0 26px rgba(0,224,138,.6)}
.card.sel h3{color:#9ff5cd}

/* ---- 满屏背景双层轮播（转场：fade / zoom / slide / ken） ------------------
   注意：**不能用 inset:0 简写** —— Android 9 电视的 WebView 是 Chrome 84，
   inset 是 87+ 才有的简写，写了整层塌缩不可见（2026-10-30 真机黑屏实锤）。
   必须显式 top/left/width/height。 */
#heroFx{position:fixed;top:0;left:0;width:100vw;height:100vh;z-index:-2;
 overflow:hidden;background:#070a10}
#heroFx img{position:absolute;top:0;left:0;width:100%;height:100%;
 object-fit:cover;opacity:0;will-change:opacity,transform;
 transition:opacity .9s ease,transform 2.8s ease}
/* 相关推荐：进推荐态时一次只亮一行，按下键再展开下一行 */
.rowhide{display:none}
/* 推荐区大海报：一行 4 张（2026-10-30 海哥：再次加大尺寸） */
#recSec .wall .card{width:25%}
@media (max-width:1180px){#recSec .wall .card{width:25%}}
@media (max-width:900px){#recSec .wall .card{width:33.33%}}
#recSec .wall .card h3{font-size:16px}

/* ---- 左上角片名动画条 ------------------------------------------------
   hero 态隐藏；进演职员/推荐态时延迟 1 秒从左侧滑入 + 发光。
   剧名优先用 TMDB 官方 logo 图（美化设计），没有就退回霓虹文字。 */
#titleCard{position:fixed;top:26px;left:34px;z-index:40;max-width:44vw;
 opacity:0;transform:translateX(-30px);pointer-events:none;
 transition:opacity .55s ease,transform .55s ease}
#titleCard.show{opacity:1;transform:translateX(0)}
#titleCard .tcwrap{padding:14px 20px;border-radius:14px;
 background:linear-gradient(135deg,rgba(7,10,16,.88),rgba(7,10,16,.55));
 border:1px solid rgba(0,224,138,.32);
 box-shadow:0 0 0 1px rgba(168,85,247,.10) inset,0 12px 38px rgba(0,0,0,.6),
            0 0 28px rgba(0,224,138,.14)}
#titleCard img.tlogo{display:block;max-width:300px;max-height:88px;margin-bottom:8px;
 filter:drop-shadow(0 0 14px rgba(0,224,138,.35))}
#titleCard h2{font-size:26px;line-height:1.3;color:#eafff5;letter-spacing:1px;
 text-shadow:0 0 14px rgba(0,224,138,.8),0 0 36px rgba(0,224,138,.4);
 margin:0 0 6px}
#titleCard p{font-size:12.5px;line-height:1.62;color:#aeb9cc;margin:0;
 max-height:3.4em;overflow:hidden;display:-webkit-box;
 -webkit-line-clamp:2;-webkit-box-orient:vertical}
/* 推荐态只留特效剧名卡：有 logo 图时把文字剧名也藏掉（2026-10-30 海哥） */
#titleCard.nomin h2{display:none}
#titleCard p b{color:#9ff5cd;font-weight:600;letter-spacing:2px;
 text-shadow:0 0 10px rgba(0,224,138,.6)}
"""

# 遥控器导航脚本 —— 详情页三态状态机。
#
# 2026-10-30 按海哥的交互稿重写：
#   hero 态：满屏背景 + 标题/按钮，←→ 切按钮，OK 触发；
#   ↓ 一步 → cast 态：演职员整块丝滑上移到屏幕下 2/3，延迟 1 秒左上角
#     片名动画条滑入（TMDB logo 优先，简介小字垫底）；光标在头像网格里
#     ↑↓ 跨行、←→ 跨列；
#   cast 末行再按 ↓ → rec 态：推荐区同样一步上移，一次只亮一行，
#     末行按 ↓ 再展开下一行；光标逻辑同理；↑ 从推荐第一行回演职员。
#
# 三条实拍踩出来的老规矩仍然有效：
#   1. 焦点先全清再补一个（sel 不许同时亮两个）；
#   2. 页面滚动全部自己驱动（WebView 方向键不滚页）；
#   3. OK 键直接 location.href 到 <a> 的 href。
_JS_NAV = """
(function(){
  var vh = function () { return window.innerHeight; };
  var btns = [], epEls = [], castEls = [], recEls = [];
  // 四态：hero（按钮） → ep（选集） → cast（演职员） → rec（相关推荐）
  // 2026-11-01 插入 ep：海哥要「播放前先选集」，所以 ↓ 的第一站是选集，
  // 没有集数时自动跳过（avail/step 会绕过空态）。
  var ORDER = ['hero', 'ep', 'cast', 'rec'];
  var state = 'hero';
  var grid = [], row = 0, col = 0;
  var recRowsShown = 1;          // 推荐区当前展开的行数
  var recFull = [];              // 推荐区**完整**的行 × 列（隐藏行也在里面）
  var tcTimer = null;

  function all(sel) {
    var a = document.querySelectorAll(sel), out = [];
    for (var i = 0; i < a.length; i++) { out.push(a[i]); }
    return out;
  }
  function makeGrid(els) {
    // 按 offsetTop 聚成行（同一行内按 offsetLeft 排）
    var rows = [];
    for (var i = 0; i < els.length; i++) {
      var el = els[i], placed = false;
      for (var r = 0; r < rows.length; r++) {
        if (Math.abs(rows[r][0].offsetTop - el.offsetTop) < 24) {
          rows[r].push(el); placed = true; break;
        }
      }
      if (!placed) { rows.push([el]); }
    }
    rows.sort(function (a, b) { return a[0].offsetTop - b[0].offsetTop; });
    for (var r2 = 0; r2 < rows.length; r2++) {
      rows[r2].sort(function (a, b) { return a.offsetLeft - b.offsetLeft; });
    }
    return rows;
  }
  // 数组每次现取：epEls 会在集数异步到达后被整体替换，闭包里的旧引用会失效
  function pools() { return [btns, epEls, castEls, recEls]; }
  function avail(st) {
    if (st === 'ep') { return epEls.length > 0; }
    if (st === 'cast') { return castEls.length > 0; }
    if (st === 'rec') { return recEls.length > 0; }
    return true;
  }
  function step(st, dir) {
    var i = ORDER.indexOf(st);
    for (var j = i + dir; j >= 0 && j < ORDER.length; j += dir) {
      if (avail(ORDER[j])) { return ORDER[j]; }
    }
    return st;
  }
  function hi() {
    var ps = pools();
    for (var p = 0; p < ps.length; p++) {
      for (var i = 0; i < ps[p].length; i++) { ps[p][i].classList.remove('sel'); }
    }
    var cur = grid[row] && grid[row][col];
    if (cur) { cur.classList.add('sel'); }
  }
  function anchorsClear() { clearTimeout(tcTimer); }

  // ---- 左上角片名动画条 ------------------------------------------------
  var tc = document.getElementById('titleCard');
  function titleCard(mode) {
    if (!tc) { return; }
    if (mode === 'hide') { tc.classList.remove('show'); return; }
    var sub = document.getElementById('tcSub');
    if (mode === 'cast' && sub) {
      tc.classList.remove('nomin');
      sub.innerHTML = (tc.getAttribute('data-desc') || '');
    } else if (sub) {
      // 推荐态只留特效剧名卡：有 TMDB logo 就把文字剧名和提示语全收掉
      tc.classList.toggle('nomin', !!tc.querySelector('.tlogo'));
      sub.innerHTML = '';
    }
    tc.classList.remove('show');
    anchorsClear();
    // 海哥的交互稿：延迟 1 秒再让片名动画出场
    tcTimer = setTimeout(function () { tc.classList.add('show'); }, 1000);
  }

  // ---- 滚动 ------------------------------------------------------------
  function smoothTo(y) {
    try { window.scrollTo({ top: y, behavior: 'smooth' }); }
    catch (e) { window.scrollTo(0, y); }
  }
  function secTop(id) {
    var el = document.getElementById(id);
    return el ? Math.max(0, el.offsetTop - Math.round(vh() * 0.30)) : 0;
  }
  function ensureVisible(el) {
    if (!el) { return; }
    var r = el.getBoundingClientRect();
    var topBand = Math.round(vh() * 0.33);
    if (r.top < topBand) { smoothTo(window.pageYOffset + r.top - topBand - 8); }
    else if (r.bottom > vh()) { smoothTo(window.pageYOffset + r.bottom - vh() + 18); }
  }

  // ---- 推荐区行展开 ------------------------------------------------------
  //
  // 2026-11-01 修 bug（海哥实拍：按一次↓第二行不出来，再按一次直接跳到第三行）：
  // 原来每次都 `makeGrid(recEls)` 重排，而被 rowhide 藏起来的卡片 display:none、
  // offsetTop 全是 0，会被并成同一行 —— 算出来的行数比真实少，于是第一次 ↓
  // 以为「已经到底」直接 +1 行，第二次又按错的表跳过去。
  // 现在**进入推荐态时一次性算完整行表**（那时还没藏任何行），之后只按
  // recRowsShown 切片，不再重排。
  function recApply() {
    for (var i = 0; i < recEls.length; i++) {
      var el = recEls[i], rIdx = -1;
      for (var r = 0; r < recFull.length; r++) {
        if (recFull[r].indexOf(el) >= 0) { rIdx = r; break; }
      }
      if (rIdx >= recRowsShown) { el.classList.add('rowhide'); }
      else { el.classList.remove('rowhide'); }
    }
    return recFull.slice(0, Math.max(1, recRowsShown));
  }

  function enter(st) {
    if (!avail(st)) { st = step(st, st === 'hero' ? 1 : -1); }
    state = st;
    if (st === 'hero') {
      grid = [btns]; row = 0; col = 0;
      titleCard('hide');
      document.body ? document.body.classList.remove('below') : 0;
      smoothTo(0);
    } else if (st === 'ep') {
      grid = makeGrid(epEls); row = 0; col = 0;
      titleCard('cast');
      document.body ? document.body.classList.add('below') : 0;
      smoothTo(secTop('epSec'));
    } else if (st === 'cast') {
      grid = makeGrid(castEls); row = 0; col = 0;
      titleCard('cast');
      document.body ? document.body.classList.add('below') : 0;
      smoothTo(secTop('castSec'));
    } else {
      recRowsShown = 1;
      recFull = makeGrid(recEls);   // 必须在 rowhide 之前算，否则行会被并掉
      grid = recApply(); row = 0; col = 0;
      titleCard('rec');
      document.body ? document.body.classList.add('below') : 0;
      smoothTo(secTop('recSec'));
    }
    hi();
  }

  function move(dr, dc) {
    if (state === 'hero') {
      if (dr > 0) {
        var nx0 = step('hero', 1);
        if (nx0 !== 'hero') { enter(nx0); return; }
      }
      if (!btns.length) { return; }
      col = (col + dc + btns.length) % btns.length;
      hi(); return;
    }
    var nr = row + dr;
    if (nr >= grid.length) {
      if (state === 'rec' && dr > 0 && recRowsShown < recFull.length) {
        recRowsShown += 1;
        grid = recApply();
        row = grid.length - 1;
        col = Math.min(col, grid[row].length - 1);
        hi();
        // 单张海报 720px 高，一屏放不下两整行 —— 所以滚到**焦点那一行**
        // 完整可见，上一行自然露出一截当「还有更多」的提示。
        ensureVisible(grid[row][col]);
        return;
      }
      var nx = step(state, 1);
      if (nx !== state) { enter(nx); return; }
      nr = grid.length - 1;
    }
    if (nr < 0) {
      var pv = step(state, -1);
      if (pv !== state) { enter(pv); return; }
      nr = 0;
    }
    row = nr;
    var r = grid[row];
    col = Math.max(0, Math.min(col + dc, r.length - 1));
    hi(); ensureVisible(grid[row][col]);
  }

  function init() {
    btns = all('.acts a');
    epEls = all('#epSec a.ep');
    castEls = all('#castSec .cast a');
    recEls = all('#recSec .wall a.card');
    enter('hero');
  }

  // 集数是异步到达的：渲染完通知一声，重新收集选集按钮
  window.__haiEpRefresh = function () {
    epEls = all('#epSec a.ep');
    if (state === 'ep') { grid = makeGrid(epEls); row = 0; col = 0; hi(); }
  };

  document.addEventListener('keydown', function (e) {
    var k = e.keyCode || e.which;
    if (k === 37) { move(0, -1); e.preventDefault(); }
    else if (k === 39) { move(0, 1); e.preventDefault(); }
    else if (k === 38) { move(-1, 0); e.preventDefault(); }
    else if (k === 40) { move(1, 0); e.preventDefault(); }
    else if (k === 13 || k === 23) {
      var b = grid[row] && grid[row][col];
      if (b) {
        var h = b.getAttribute('href') || '';
        // href="#" 的是「站源 / 季」切换按钮，页面自己接管点击；
        // 其余（剧集、海报、按钮）直接跳，包括 haiplay:// 自定义 scheme。
        // 统一走 click：这样页面自己挂的监听器（起播遮罩等）有机会先介入；
        // 没有监听器时 <a> 的默认行为就是跳 href，效果等价。
        if (h) { try { b.click(); } catch (err) { window.location.href = h; } }
      }
      e.preventDefault();
    }
  }, false);

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init, false);
  } else { init(); }
  try { document.body.setAttribute('tabindex', '-1'); document.body.focus(); } catch (err) { }
})();
"""

# 满屏背景轮播：双层 <img> 交叉淡入，转场效果由维护页「海报墙配置」下发。
# interval 秒切一张；effect = fade/zoom/slide/ken，random = 每张随机挑一样。
_JS_HERO = """
(function () {
  var imgs = %IMGS%;
  var INTERVAL = %INTERVAL%;      // 秒
  var EFFECT = '%EFFECT%';
  var A = document.getElementById('fxA'), B = document.getElementById('fxB');
  if (!A || !B || !imgs || imgs.length < 2) {
    if (A) { A.src = imgs ? imgs[0] : ''; A.style.opacity = 1; }
    return;
  }
  var cur = A, bak = B, i = 0;
  cur.src = imgs[0]; cur.style.opacity = 1;
  var FXS = ['fade', 'zoom', 'slide', 'ken'];
  var HIDE_TF = { zoom: 'scale(1.07)', slide: 'translateX(-3.5%)', ken: 'scale(1.12)' };
  function pick() { return EFFECT === 'random' ? FXS[Math.floor(Math.random() * FXS.length)] : EFFECT; }
  setInterval(function () {
    i = (i + 1) % imgs.length;
    var fx = pick();
    bak.src = imgs[i];
    bak.style.transition = 'none';
    bak.style.transform = HIDE_TF[fx] || 'none';
    bak.style.opacity = 0;
    void bak.offsetWidth;                 // 强制 reflow，让起始态生效
    bak.style.transition = '';
    bak.style.opacity = 1;
    bak.style.transform = 'none';
    cur.style.opacity = 0;
    if (fx === 'ken') { cur.style.transform = 'scale(1.1)'; }
    var old = cur; cur = bak; bak = old;
  }, INTERVAL * 1000);
})();
"""


def _jsver() -> str:
    """静态 JS 的版本戳（文件 mtime）。**必须带** —— WebView 对
    /hai/lib/*.js 有 5 分钟强缓存，不带版本号改了脚本电视上不生效
    （2026-11-02 踩过：修了百度子目录，电视跑的还是旧脚本）。"""
    try:
        fp = os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "assets", "lib", "haiplay.js")
        return str(int(os.path.getmtime(fp)))
    except Exception:
        return "1"


def _e(s) -> str:    return html.escape(str(s or ""))


def _pic(u) -> str:
    """图片地址改成站内相对路径，走中枢代理（TMDB /hai/img、豆瓣 /hai/dbimg）。

    为什么必须代理：
      * TMDB —— 电视直连 images.tmdb.org 慢且容易裂图；
      * 豆瓣 —— 直连一律 **418**，只有带 Referer 才给图（实测）；
    两种都改成相对路径，网页从局域网或公网任意入口打开都能出图。
    """
    s = str(u or "")
    s = re.sub(r"^https?://images\.tmdb\.org/t/p/", "/hai/img/t/p/", s)
    s = re.sub(r"^https?://[a-z0-9]*\.doubanio\.com/((?:view|img)/.+)$",
               r"/hai/dbimg/\1", s)
    s = re.sub(r"^https?://[^/]+(/hai/(?:db)?img/.*)$", r"\1", s)
    return html.escape(s)


def _pic_raw(u) -> str:
    """同 `_pic`，但不做 HTML 转义 —— 给嵌进 <script> 的 JSON 用。

    <script> 是 HTML 的 raw text 元素，里面的 `&amp;` 不会被解码，
    若把 html.escape 过、且含 `&` 的 URL 塞进 JS 字符串，赋值给 img.src 时
    会变成字面量 `&amp;`，图片就裂了。海报/背景地址不含 `&<>\"'`，
    正常也等于原串，这里单独留一份原始串更稳。
    """
    s = str(u or "")
    s = re.sub(r"^https?://images\.tmdb\.org/t/p/", "/hai/img/t/p/", s)
    s = re.sub(r"^https?://[a-z0-9]*\.doubanio\.com/((?:view|img)/.+)$",
               r"/hai/dbimg/\1", s)
    s = re.sub(r"^https?://[^/]+(/hai/(?:db)?img/.*)$", r"\1", s)
    return s

# 选集渲染（2026-11-01）：详情页首屏不等它，渲染完由 JS 后台取。
# 数据来自 /hai/poster/eps —— 每集都已经带好 haiplay 定向地址（见 app.py）。
_JS_EPS = """
(function(){
  var box = document.getElementById('epBox');
  if (!box) { return; }
  var VID = '%VID%', TITLE = %TITLE%;
  // 播放必须先交给原生播放器（网盘要转存、要 Cookie，网页播不了）。
  // 目前 app 侧只有「按片名聚合搜索」这条入口，所以集按钮点开的是**线路浮层**
  // —— 把这一集的所有网盘线路摊开给人看，再一键交给播放器。
  // APK 改造完成后，把 playRoute() 里的 SEARCH 换成 routes[i].href 即可，
  // 浮层结构一行都不用重写（2026-11-01）。
  var SEARCH = 'haiplay://search?q=' + encodeURIComponent(TITLE);
  // 唯一的播放入口：TMDBWall 站点自己（app 里必定存在，detail 里带
  // push:// 网盘分享）。打开就是这一部的选集页 → 再点集就是画面。
  // 单集直达（不经过选集页）要等 APK 换了调用方式；到那天把这里换成
  // routes[i].href 就行，routes 里已经存好每集的 127.0.0.1:9978 直链了。
  var SEL = 'haiplay://play?site=TMDBWall&id=' + encodeURIComponent(VID)
    + '&name=' + encodeURIComponent(TITLE);
  var merged = [], stats = {}, srclist = [], sea = 0;
  var dlg = null, items = [], sel = 0, curEp = null, pushed = false;

  function esc(s) {
    return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;')
      .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }
  function fmt(sz) {
    sz = parseInt(sz, 10) || 0;
    if (sz <= 0) { return ''; }
    if (sz >= 1073741824) { return (sz / 1073741824).toFixed(1) + ' GB'; }
    return Math.round(sz / 1048576) + ' MB';
  }
  function panCls(p) {
    if (p === 'UC') { return ' uc'; }
    if (p === '百度') { return ' baidu'; }
    if (p === '阿里') { return ' ali'; }
    return '';
  }
  // 网盘文件名长得没法看（漫长的季节.The.Long.Season.S01E01.2023.V2.2160p
  // .WEB-DL.H265.DDP2.0-SeeWEB.mp4），片名和集号上面标题里已经写清楚了，
  // 这里只把**画质/编码**这些真正有区分度的标签挑出来。
  function tidy(fn) {
    var s = String(fn || '').replace(/\\.(mp4|mkv|avi|ts|mov|flv|m3u8|wmv|rmvb)$/i, '');
    var tags = [], m;
    var re = /(2160p|1080p|720p|480p|4k|hdr|h265|h264|hevc|x265|x264|web-?dl|bluray)/ig;
    while ((m = re.exec(s))) {
      var t = m[1].toUpperCase();
      if (tags.indexOf(t) < 0) { tags.push(t); }
    }
    return tags.length ? tags.join(' \u00b7 ') : s;
  }
  function flat() {
    var a = [], i, j, e;
    for (i = 0; i < merged.length; i++) {
      e = merged[i].eps || [];
      for (j = 0; j < e.length; j++) {
        if (e[j].routes && e[j].routes.length) { a.push(e[j]); }
      }
    }
    return a;
  }
  function wrap(e) {
    var r = (e.routes && e.routes[0]) || null;
    if (!r) { return null; }
    return { href: r.href, ep: e.ep || 0, pan: r.pan || '',
             push: r.push || '', site: r.site || '',
             label: e.label || (e.ep ? '第' + e.ep + '集' : '正片') };
  }
  // 「▶ 播放」用：优先本地记的续播集，没有就第一集
  window.__haiPick = function (ep) {
    var f = flat(), i, want = ep || 0;
    if (!f.length) { return null; }
    if (!want) {
      try { want = parseInt(localStorage.getItem('hai.ep.' + VID), 10) || 0; } catch (x) {}
    }
    if (want) {
      for (i = 0; i < f.length; i++) { if ((f[i].ep || 0) === want) { return wrap(f[i]); } }
    }
    return wrap(f[0]);
  };

  // 诊断：直接上报服务器（页面里画不出来 —— 覆盖层被 stacking context 压住、
  // 插进 flex 行又被挤成 0 宽）。只打一个 fire-and-forget 的 XHR，读访问日志即可。
  var _dbgOn = /[?&]dbg=1/.test(location.search);
  window.__haiDbg = function (s) {
    if (!_dbgOn) { return; }
    try {
      var x = new XMLHttpRequest();
      x.open('GET', '/hai/dbg?m=' + encodeURIComponent(String(s).slice(0, 180)), true);
      x.send();
    } catch (e) {}
  };

  // 本地取链候选：**跨线路**收集，夸克优先（2026-11-02）
  // routes[0] 十有八九是 UC，而 UC 非会员账号 CDN 只给占位文件 ——
  // 只试第一条必然失败，白退服务器。
  window.__haiRoutes = function (ep) {
    var f = flat(), out = [], i, j, w = ep || 0;
    for (i = 0; i < f.length; i++) {
      if (w && (f[i].ep || 0) !== w) { continue; }
      var rs = f[i].routes || [];
      for (j = 0; j < rs.length; j++) {
        if (rs[j].push) {
          out.push({ push: rs[j].push, pan: rs[j].pan || '',
                     ep: f[i].ep || 0, site: rs[j].site || '' });
        }
      }
    }
    out.sort(function (a, b) {
      return (/\u5938\u514b/.test(a.pan) ? 0 : 1) - (/\u5938\u514b/.test(b.pan) ? 0 : 1);
    });
    return out;
  };

  /** 本地算这一集的直链（完全不碰服务器）。返回 Promise<href|null>。 */
  window.__haiLocalHref = function (ep) {
    return new Promise(function (res) {
      window.__haiDbg('[hai] enter ep=' + ep + ' hp=' + (typeof window.HaiPlay)
        + ' rt=' + (typeof window.__haiRoutes));
      if (!window.HaiPlay || !window.__haiRoutes) { return res(null); }
      var w = ep || 0;
      if (!w) {
        var p0 = window.__haiPick && window.__haiPick(0);
        w = (p0 && p0.ep) || 0;
      }
      var cs = window.__haiRoutes(w).slice(0, 4), i = 0;
      try { window.__haiDbg('[hai] cands=' + cs.length + ' ep=' + w + '\\n  '
        + cs.map(function (c) { return c.pan + ':' + String(c.push).slice(-14); }).join('\\n  ')); } catch (e) {}
      function next() {
        if (i >= cs.length) { window.__haiDbg('[hai] local FAIL all'); return res(null); }
        var c = cs[i++];
        window.HaiPlay.ep({ share: c.push, ep: c.ep || w || 0, title: TITLE })
          .then(function (r) {
            if (r && r.ok) {
              try { window.__haiDbg('[hai] local OK ' + c.pan + ' ' + (r.size || 0)); } catch (e) {}
              res({ href: window.HaiPlay.href(r, c.ep || w || 0, r.name || TITLE),
                    ep: c.ep || w || 0, pan: c.pan, size: r.size || 0 });
            } else {
              try { window.__haiDbg('[hai] miss ' + c.pan + ' -> '
                + String((r && r.error) || '?')); } catch (e) {}
              next();
            }
          })
          .catch(function (e2) {
            try { window.__haiDbg('[hai] EX ' + c.pan + ' ' + e2); } catch (e) {}
            next();
          });
      }
      next();
    });
  };

  // ---------------- 选集区渲染 ----------------
  function render() {
    if (!merged.length) {
      box.innerHTML = '<span class="epwait">暂时列不出集数'
        + '　点「▶ 播放」交给播放器搜片</span>';
      if (window.__haiEpRefresh) { window.__haiEpRefresh(); }
      return;
    }
    var html = '', i, s, e;
    if (stats.eps) {
      html += '<div class="epstat">共 <b>' + stats.eps + '</b> 集';
      var pns = [], k;
      for (k = 0; k < srclist.length; k++) {
        var pn = String(srclist[k]).split('\u00b7').pop();
        if (pns.indexOf(pn) < 0) { pns.push(pn); }
      }
      // 有备份线路时才说线路数，否则「12 集 · 12 条线路」纯属废话
      if (stats.routes > stats.eps) {
        html += '\u3000\u00b7\u3000<b>' + stats.routes + '</b> \u6761\u7ebf\u8def';
      }
      if (pns.length) {
        html += ' <span class="esrc">' + esc(pns.join(' \u00b7 ')) + '</span>';
      }
      html += '</div>';
    }
    if (merged.length > 1) {
      html += '<div class="epsea">';
      for (i = 0; i < merged.length; i++) {
        html += '<a class="ep sea' + (i === sea ? ' on' : '') + '" data-t="sea" data-i="'
          + i + '" href="#">' + esc(merged[i].label) + '</a>';
      }
      html += '</div>';
    }
    s = merged[sea] || merged[0] || {};
    var eps = s.eps || [];
    // 绿点只在「这集比其它集多一条线路」时亮（2026-11-01 修）。
    // 以前是 routes.length>1 就亮 —— 12 集全 2 路，12 个点一起闪，
    // 等于没有信息量，纯噪音。现在取全集最小线路数当基准，只有差异才标。
    var minR = 99, rn, kk;
    for (kk = 0; kk < eps.length; kk++) {
      rn = (eps[kk].routes || []).length;
      if (rn && rn < minR) { minR = rn; }
    }
    if (minR === 99) { minR = 1; }
    html += '<div class="epgrid">';
    for (i = 0; i < eps.length; i++) {
      e = eps[i];
      // 按钮上**只放集号**（2026-11-01 海哥：第几集按钮为什么还显示长串字符）。
      // e.label 在「认不出季集号」的源里就是原始文件名，一整行
      // "The.Long.Season.S01E01.2023.V2.2160p.WEB-DL.H265-XXX" 直接糊在格子
      // 里，既撑破布局又没信息。全名仍然有用 —— 塞进 data-nm 交给线路浮层
      // 的副标题，按钮本身保持极简。
      var lbl = e.ep ? ('第' + e.ep + '集') : '正片';
      var full = e.label || lbl;
      if (e.routes && e.routes.length) {
        html += '<a class="ep" href="#" data-i="' + i + '" data-ep="'
          + (e.ep || 0) + '" data-nm="' + esc(full) + '">' + esc(lbl)
          + (e.routes.length > minR ? '<i class="dot"></i>' : '') + '</a>';
      } else {
        html += '<span class="ep off">' + esc(lbl) + '</span>';
      }
    }
    html += '</div>';
    box.innerHTML = html;
    if (window.__haiEpRefresh) { window.__haiEpRefresh(); }
  }

  // ---------------- 线路浮层 ----------------
  function buildDlg() {
    if (dlg) { return dlg; }
    dlg = document.createElement('div');
    dlg.id = 'epDlg';
    dlg.innerHTML = '<div class="ebox"><h3></h3><div class="esub"></div>'
      + '<div class="elist"></div>'
      + '<div class="efoot"><a class="btn ghost" href="#" data-act="close">关闭</a></div>'
      + '</div>';
    document.body.appendChild(dlg);
    dlg.addEventListener('click', function (ev) {
      var a = ev.target;
      while (a && a !== dlg) {
        if (a.getAttribute && (a.getAttribute('data-act') || a.getAttribute('data-ri') !== null)) { break; }
        a = a.parentNode;
      }
      if (!a || a === dlg) { return; }
      ev.preventDefault();
      if (a.getAttribute('data-act') === 'close') { closeDlg(); return false; }
      var ri = parseInt(a.getAttribute('data-ri'), 10);
      if (!isNaN(ri)) { playRoute(ri); }
      return false;
    }, false);
    return dlg;
  }

  function paintSel() {
    for (var i = 0; i < items.length; i++) {
      items[i].className = 'eroute' + (i === sel ? ' sel' : '');
    }
  }

  function openDlg(e) {
    buildDlg();
    curEp = e;
    var h = dlg.querySelector('h3');
    var sub = dlg.querySelector('.esub');
    var list = dlg.querySelector('.elist');
    h.innerHTML = esc(e.label || (e.ep ? '第' + e.ep + '集' : '正片'));
    var rs = e.routes || [], html = '', pans = [], i, r;
    for (i = 0; i < rs.length; i++) {
      r = rs[i];
      if (pans.indexOf(r.pan) < 0) { pans.push(r.pan); }
      var fn = r.name || (e.label || '');
      html += '<a class="eroute" data-ri="' + i + '" href="#">'
        + '<i class="pan' + panCls(r.pan) + '">' + esc(r.pan) + '</i>'
        + '<span class="fn">' + esc(tidy(fn)) + '</span>'
        + '<span class="sz">' + esc(fmt(r.size)) + '</span></a>';
    }
    list.innerHTML = html || '<div class="epwait">这一集暂时没有可用线路</div>';
    sub.innerHTML = esc(pans.join(' / '));
    items = [].slice.call(list.querySelectorAll('.eroute'));
    sel = 0;
    paintSel();
    dlg.className = 'on';
    if (!pushed) {
      pushed = true;
      try { history.pushState({ epdlg: 1 }, '', location.href); } catch (x) {}
    }
  }

  function closeUI() { if (dlg) { dlg.className = ''; } }
  // 返回键不会传到 JS（Activity 的 onKeyDown 先吃掉），但它的实现是
  // 「canGoBack() 就 goBack()，否则才 finish」。所以打开浮层时先 pushState
  // 垫一条历史：按返回 → WebView goBack → popstate 到这里 → 只关浮层，
  // 页面不动。不垫这条，按返回会直接把整个详情页退掉（真机实锤）。
  function closeDlg() {
    closeUI();
    if (pushed) { pushed = false; try { history.back(); } catch (x) {} }
  }
  window.addEventListener('popstate', function () {
    if (dlg && dlg.className.indexOf('on') >= 0) { pushed = false; closeUI(); }
  }, false);

  function playRoute(i) {
    if (!curEp) { return; }
    var r = (curEp.routes && curEp.routes[i]) || (curEp.routes && curEp.routes[0]);
    var ep = curEp.ep || 0;
    try {
      if (ep) { localStorage.setItem('hai.ep.' + VID, String(ep)); }
    } catch (x) {}
    // 兜底：本地 + 服务端都算不出来时，**退聚合搜索**，绝不拼死链。
    //
    // 2026-11-05 修（真 bug，海哥「所有资源找不到数据」的根因）：
    // 旧版这里用 `r.push`（分享链，不是可播直链）+ 真实集号 ep 拼
    //   haiplay://play?site=HaiDirect&id=<分享链>&ep=N
    // HaiDirect 蜘蛛只回单集（1 条），fongmi 用 **0 基** ep 取
    // playUrls[N]（N>=1）→ 越界取到 null → Objects.requireNonNull →
    // 播放器弹「找不到数据」。分享链本身也不可播。两个坑叠加，必挂。
    // 现在失败就跳聚合搜索（SEL），至少让用户能搜到片、重选源再播。
    var fb = SEL;
    var lbl = curEp.label || '';
    closeUI();
    pushed = false;          // 马上要跳走，别再去回退 history
    if (window.__haiCurtain) { window.__haiCurtain(TITLE, lbl); }
    // 遮罩上显示「正在解析第 N 集（夸克）/ 已就绪」—— 服务端每步都会写进度
    var _watch = window.__haiWatch ? window.__haiWatch(VID, ep || 0) : null;
    function jump(u) {
      if (_watch) { _watch.stop(); }
      setTimeout(function () { window.location.href = u; }, 300);
    }
    // 点第 N 集 = 服务端现算第 N 集的夸克直链（扫目录→转存→取链，3~4 秒，
    // 遮罩挡着不难受）。算不出来再退回 push 兜底。
    // 本地优先（2026-11-02）：电视自己算这一集直链，不碰服务器；
    // 跨线路候选 + 夸克优先。本地全失败**再问中枢**（不能跳 fb —— 
    // `push://<分享>` 这条路已证实是死的：push_agent 会把链接原样丢给播放器）。
    function askServer() {
      var x2 = new XMLHttpRequest();
      x2.open('GET', '/hai/poster/play?vid=' + encodeURIComponent(VID)
        + '&direct=1&ep=' + encodeURIComponent(ep)
        + (r && r.push ? '&link=' + encodeURIComponent(r.push) : ''), true);
      x2.onload = function () {
        var d = null;
        try { d = JSON.parse(x2.responseText); } catch (e) {}
        jump((d && d.ok && d.href) ? d.href : fb);
      };
      x2.onerror = function () { jump(fb); };
      x2.send();
    }
    if (window.__haiLocal2) {
      window.__haiLocal2(ep, function (h) { if (h) { jump(h); } else { askServer(); } });
      return;
    }
    var x = new XMLHttpRequest();
    x.open('GET', '/hai/poster/play?vid=' + encodeURIComponent(VID)
      + '&direct=1&ep=' + encodeURIComponent(ep)
      + (r && r.push ? '&link=' + encodeURIComponent(r.push) : ''), true);
    x.onload = function () {
      var d = null;
      try { d = JSON.parse(x.responseText); } catch (e) {}
      jump((d && d.ok && d.href) ? d.href : fb);
    };
    x.onerror = function () { jump(fb); };
    x.send();
  }

  document.addEventListener('keydown', function (e) {
    if (!dlg || dlg.className.indexOf('on') < 0) { return; }
    var k = e.keyCode || e.which;
    e.stopPropagation();
    e.preventDefault();
    if (k === 38 || k === 19 || k === 37 || k === 21) {
      if (items.length) { sel = (sel + items.length - 1) % items.length; paintSel(); }
    } else if (k === 40 || k === 20 || k === 39 || k === 22) {
      if (items.length) { sel = (sel + 1) % items.length; paintSel(); }
    } else if (k === 13 || k === 23 || k === 66) {
      if (items.length) { playRoute(sel); } else { closeDlg(); }
    } else if (k === 4 || k === 8 || k === 27) {
      closeDlg();
    }
  }, true);

  // ---------------- 事件绑定 ----------------
  box.addEventListener('click', function (ev) {
    var a = ev.target, t = null;
    while (a && a !== box) {
      if (a.getAttribute) {
        t = a.getAttribute('data-t');
        if (t || a.hasAttribute('data-ep')) { break; }
      }
      a = a.parentNode;
    }
    if (!a || a === box) { return; }
    ev.preventDefault();
    if (t === 'sea') {
      sea = parseInt(a.getAttribute('data-i'), 10) || 0;
      render();
      return false;
    }
    if (a.hasAttribute('data-ep')) {
      var s = merged[sea] || merged[0] || {};
      var it = (s.eps || [])[parseInt(a.getAttribute('data-i'), 10)];
      // 只有一条线路就别弹浮层了 —— 浮层里也只有一行可点，白白多按一次。
      // 多线路才摊开来给人挑（也让人看清这集各网盘的画质和体积）。
      if (it) {
        if ((it.routes || []).length > 1) { openDlg(it); }
        else { curEp = it; playRoute(0); }
      }
    }
    return false;
  }, false);

  var x = new XMLHttpRequest();
  x.open('GET', '/hai/poster/eps?vid=%VID%', true);
  x.onreadystatechange = function () {
    if (x.readyState !== 4) { return; }
    var r = null;
    try { r = JSON.parse(x.responseText); } catch (e) { r = null; }
    merged = [];
    stats = {};
    srclist = [];
    if (r) {
      if (r.merged) { merged = r.merged; }
      stats = r.stats || {};
      srclist = r.srclist || [];
    }
    sea = 0;
    render();
  };
  x.send();
})();
"""

# 起播过渡层（2026-11-01 海哥：播放链条不许露 PG 原生界面）
#   点「播放」/「第N集」→ 先铺这张全屏横版剧照 → 300ms 后切原生播放器。
#   播放结束返回时（WebView 重新可见）自动撤遮罩并整页淡入一次。
_JS_CURTAIN = """
(function(){
  var host = document.getElementById('heroFx');
  var bg = host ? (host.getAttribute('data-cbg') || '') : '';
  if (!bg) {
    var f = document.getElementById('fxA');
    bg = f ? (f.getAttribute('src') || '') : '';
  }
  var cov = document.createElement('div');
  cov.id = 'curtain';
  cov.innerHTML = '<img class="cbg" alt=""><div class="cshade"></div>'
    + '<div class="ctbox"><div class="ctname"></div><div class="ctsub"></div>'
    + '<div class="ctstat"></div><div class="cbar"><i></i></div></div>'
    + '<div class="cspin"></div>';
  document.body.appendChild(cov);
  var im = cov.querySelector('.cbg');
  if (bg) { im.setAttribute('src', bg); } else { im.style.display = 'none'; }
  window.__haiCurtain = function (name, sub) {
    cov.querySelector('.ctname').textContent = name || '';
    cov.querySelector('.ctsub').textContent = sub || '';
    cov.querySelector('.ctstat').textContent = '';
    cov.className = 'on';
  };
  // 状态行：解析阶段由调用方轮询 /hai/poster/progress 后写进来。
  // html 只由本文件拼（不接收远端字符串），不会 XSS。
  window.__haiCurtainStatus = function (html) {
    var el = cov.querySelector('.ctstat');
    if (el) { el.innerHTML = html || ''; }
    var bar = cov.querySelector('.cbar');
    if (bar) { bar.style.display = html ? 'block' : 'none'; }
  };
  window.__haiCurtainHide = function () { cov.className = ''; };
  document.addEventListener('visibilitychange', function () {
    if (document.visibilityState === 'visible') {
      window.__haiCurtainHide();
      var b = document.body;
      b.classList.add('backfade');
      setTimeout(function () { b.classList.remove('backfade'); }, 760);
    }
  }, false);
  window.addEventListener('pageshow', function () { window.__haiCurtainHide(); }, false);
  setTimeout(function () { window.__haiCurtainHide(); }, 9000);

  // ---- 解析状态轮询（2026-11-03）----------------------------------------
  // 点「▶ 播放」之后服务端要 3~15 秒（汇总站源 → 转存 → 取链 → 量速度），
  // 这段时间遮罩只写片名，看着像卡死。这里每 400ms 拉一次 /hai/poster/progress
  // 把「正在解析第 N 集（夸克）」「已就绪 · 夸克 · 5.5 GB」写进状态行。
  function _esc(s) {
    return String(s == null ? '' : s).replace(/&/g, '&amp;')
      .replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }
  window.__haiWatch = function (vid, ep) {
    var stop = false, n = 0, t0 = new Date().getTime();
    function paint(d) {
      if (!window.__haiCurtainStatus) { return; }
      var txt = _esc(d.text || '');
      if (d.stage === 'ready') { txt = '<b>' + txt + '</b>'; }
      else if (d.stage === 'slow') { txt = '<span class="slow">' + txt + '</span>'; }
      var sec = Math.round((new Date().getTime() - t0) / 1000);
      window.__haiCurtainStatus(txt + ' <span class="dim">' + sec + 's</span>');
    }
    function tick() {
      if (stop || n++ > 120) { return; }
      var x = new XMLHttpRequest();
      x.open('GET', '/hai/poster/progress?vid=' + encodeURIComponent(vid)
        + '&ep=' + encodeURIComponent(ep || 0), true);
      x.onload = function () {
        var d = null;
        try { d = JSON.parse(x.responseText); } catch (e) {}
        if (d && d.text) { paint(d); }
        tick();
      };
      x.onerror = function () { setTimeout(tick, 900); };
      x.send();
    }
    setTimeout(tick, 300);
    return { stop: function () { stop = true; } };
  };
})();
"""

# 头像加载失败的兜底：先重试一次（加 cache-buster，绕开代理的 502 缓存），
# 还不行就换成首字母圆 —— 演职员区永远不出现纯黑空洞。
# 必须**定义在演职员 section 之前**（inline onerror 在图片失败瞬间就会被调用，
# 函数晚一步定义就只剩一次未捕获异常，连重试都没有）。
# 首字母走 data-ch 而不是拼进 onerror 参数：中文名里可能出现引号，拼进去
# 会把 HTML 属性截断；取值后用 textContent 写入，也顺带免疫 HTML 注入。
_JS_FACE = """<script>window.haiFaceMiss=function(img){
  if(!img||!img.parentNode){return;}
  if(!img.getAttribute('data-r')){
    img.setAttribute('data-r','1');
    var s=img.getAttribute('src')||'';
    img.setAttribute('src',s+(s.indexOf('?')<0?'?':'&')+'r='+((new Date()).getTime()));
    return;
  }
  var p=img.parentNode;
  p.className='cface noimg';
  p.textContent=img.getAttribute('data-ch')||'?';
};</script>"""

# 演职员补全：豆瓣详情只给名字，这里向 /hai/poster/cast 要头像 + 影人 id。
# 补到之后：圆头像换成真人照片，href 换成我们自己的影人页（不跳原生搜索）。
_JS_CAST = """
(function(){
  var els = [].slice.call(document.querySelectorAll('#castSec .citem[data-nm]'));
  if (!els.length) return;
  var x = new XMLHttpRequest();
  x.open('GET', '/hai/poster/cast?vid=%VID%', true);
  x.onreadystatechange = function(){
    if (x.readyState !== 4) return;
    var r; try { r = JSON.parse(x.responseText); } catch(e) { return; }
    if (!r || !r.people) return;
    var map = {}, i;
    for (i = 0; i < r.people.length; i++) { map[r.people[i].name] = r.people[i]; }
    for (i = 0; i < els.length; i++) {
      var el = els[i], p = map[el.getAttribute('data-nm')];
      if (!p) continue;
      if (p.cid) { el.setAttribute('href', '/hai/poster/p/' + encodeURIComponent(p.cid)); }
      if (p.avatar) {
        var f = el.querySelector('.cface');
        if (f) {
          // 首字母去掉引号/尖括号再进属性，别把 data-ch 截断（见 _JS_FACE 注释）
          var ch = String((p.name || '?').charAt(0)).replace(/["'<>&]/g, '');
          f.className = 'cface';
          f.innerHTML = '<img src="' + p.avatar + '" alt="" data-ch="' + ch
            + '" onerror="haiFaceMiss(this)">';
        }
      }
    }
  };
  x.send();
})();
"""

_JS_SELFHEAL = """(function(){
  // 自愈：页面几乎是空的（WebView 被系统回收后重载失败 / 网关抽风）就自己刷一次。
  // 只在真的空页时触发，最多两次，正常页面永远不刷新。
  setTimeout(function(){
    try{
      var txt = document.body ? (document.body.innerText || '') : '';
      var h = document.body ? document.body.scrollHeight : 0;
      if (txt.length < 12 && h < 320) {
        var n = +(sessionStorage.getItem('hai_nr') || 0);
        if (n < 2) { sessionStorage.setItem('hai_nr', n + 1); location.reload(); }
      } else {
        sessionStorage.removeItem('hai_nr');
      }
    }catch(e){}
  }, 3500);
})();
"""


_JS_SRCMENU = """
(function(){
  // 遥控「设置」键呼出数据源菜单（2026-10-31 海哥需求）。
  // 电视遥控器按键码不统一：菜单键 82、设置键 176 都有，个别机型给 93，
  // 所以三个都收；另外页面右上角有一个可聚焦的「⚙ 数据源」小药丸，
  // 万一手柄/遥控发不出这些键也点得到。
  var menu = document.getElementById('srcMenu');
  if (!menu) { return; }
  var opts = [].slice.call(menu.querySelectorAll('.srcopt'));
  var sel = 0;
  for (var i = 0; i < opts.length; i++) {
    if (opts[i].className.indexOf('cur') >= 0) { sel = i; }
  }
  function paint() {
    for (var i = 0; i < opts.length; i++) {
      if (/(^|\\s)sel(\\s|$)/.test(opts[i].className)) {
        opts[i].className = opts[i].className
          .replace(/(^|\\s)sel(\\s|$)/g, ' ').replace(/\\s+/g, ' ');
      }
    }
    if (opts[sel]) { opts[sel].className += ' sel'; }
  }
  // 以 DOM 上的高亮为准（WebView 偶尔吞 keyCode，变量可能和画面不一致）
  function curIdx() {
    for (var i = 0; i < opts.length; i++) {
      if (/(^|\\s)sel(\\s|$)/.test(opts[i].className)) { return i; }
    }
    return sel;
  }
  function open() { menu.className = 'on'; sel = Math.max(0, sel); paint(); }
  function close() { menu.className = ''; }
  function isOpen() { return menu.className.indexOf('on') >= 0; }
  // 海报墙的「↑ 上键」要能呼出这个浮层（海哥 11-01：详情页切换太难点，
  // 改成在海报墙类目条上按上键）。_JS_WALL 通过这个全局函数触发。
  window.__haiSrcOpen = function () { open(); };
  window.__haiSrcIsOpen = isOpen;

  document.addEventListener('keydown', function (e) {
    var k = e.keyCode || e.which;
    // 设置键（176）/ 个别机型的 93 仍然能直接开。
    // **菜单键 82 故意不收** —— 海报墙把 82 让给了「筛选面板」，
    // 收两个键会在同一个按键上打架（打开浮层的同时弹出筛选）。
    // 海报墙上的数据源入口是「类目条按 ↑」，详情页不再提供切换（海哥 11-01）。
    if (k === 176 || k === 93) {
      e.preventDefault();
      if (isOpen()) { close(); } else { open(); }
      return;
    }
    if (!isOpen()) { return; }
    // 菜单打开时把方向/OK 全吃掉，别让底下的 _JS_NAV 同时挪焦点
    e.stopPropagation();
    e.preventDefault();
    // 电视遥控按键码两套都要认：WebView 一般把方向键翻成 DOM 的 37/38/39/40，
    // 但个别盒子（本机实测）直接送 Android 的 19/20/21/22 —— 只写一套就哑火。
    // 2026-11-01 海哥实拍：浮层里按左右不动，就是漏了 37/39。
    if (k === 21 || k === 19 || k === 37 || k === 38) {   // ← / ↑
      sel = (sel + opts.length - 1) % opts.length; paint();
    } else if (k === 22 || k === 20 || k === 39 || k === 40) {  // → / ↓
      sel = (sel + 1) % opts.length; paint();
    } else if (k === 23 || k === 66 || k === 13) {
      var a = opts[curIdx()];
      if (a && a.getAttribute('href')) { location.href = a.getAttribute('href'); }
    } else if (k === 4 || k === 27) {           // 返回 / ESC
      close();
    }
  }, true);                                     // ← capture：先于页面自身导航

  if (isOpen()) { paint(); }              // 服务端渲染直接打开时补上高亮

  var pill = document.getElementById('srcPill');
  if (pill) {
    pill.addEventListener('click', function (e) { e.preventDefault(); open(); });
    pill.addEventListener('focus', function () { });
    pill.addEventListener('keydown', function (e) {
      var k = e.keyCode || e.which;
      if (k === 23 || k === 66 || k === 13) { e.preventDefault(); open(); }
    });
  }
})();
"""


def _srcdlg(cur: str, back: str, opened: bool = False) -> str:
    """数据源浮层 + 右上角小药丸。`back` 是切换后要回到的页面路径。

    `opened=True` 时浮层直接展开 —— 电视遥控的「设置」键会被机顶盒系统
    截走（实测 M401A：按 176 弹的是系统设置），WebView 也不把 MENU 键转给
    JS，所以真正的入口是页面上那个可聚焦的「⚙ 数据源」按钮：点它 → 带
    `sm=1` 重新渲染 → 浮层就是打开状态。这样遥控一定点得到。
    """
    q = urllib.parse.quote(back, safe="")
    def opt(key, title, sub):
        cls = "srcopt" + (" cur" if key == cur else "")
        return (f'<a class="{cls}" data-src="{key}" '
                f'href="/hai/poster/src?to={key}&amp;back={q}">'
                f'{title}<small>{sub}</small></a>')
    return (
        '<div id="srcMenu"' + (' class="on"' if opened else '') + '><div class="srcbox">'
        '<div class="srctitle">数据源</div>'
        '<div class="srcopts">'
        + opt("tmdb", "TMDB", "国际榜单 · 剧照丰富")
        + opt("douban", "豆瓣", "国产剧更新更快")
        + f'<a class="srcopt close" href="{_e(back)}">✕<small>关闭</small></a>'
        + '</div><div class="srchint">← → 选择　OK 确认</div>'
        '</div></div>'
    )


def _srcpill(cur: str) -> str:
    """右上角的数据源标识（纯展示，不可点）。

    真正的入口是类目条/按钮行里那个「⚙ 数据源」—— 遥控的焦点是页面自己
    用 ←→ 驱动的，绝对定位在角落的元素反而选不到，所以入口必须放在
    能被网格收进去的位置。
    """
    return f'<span class="srcpill">⚙ {_src_name(cur)}</span>'


def _srcbtn(cur: str, here: str, cls: str = "fbtn") -> str:
    """数据源的两个入口，并排放，遥控都选得到：

      1) `⚙ 数据源`  —— 带 sm=1 重新渲染，浮层直接是打开态（看两个源的说明）
      2) `⇄ TMDB`   —— **直接切**到另一个源，一次点按完成，不等浮层

    为什么要两个：电视 WebView 对 DPAD 的 keyCode 转发不稳定（实测这台
    M401A 上浮层里的方向键偶尔不生效），只靠浮层有风险；直切按钮走的是
    普通 <a href>，和「筛选」「类目」同一条已验证的链路，一定点得动。
    """
    sep = "&" if "?" in here else "?"
    other = "tmdb" if cur == "douban" else "douban"
    return (f'<a class="{cls}" id="srcPill" href="{_e(here + sep + "sm=1")}">'
            f'⚙ 数据源</a>'
            f'<a class="{cls}" href="/hai/poster/src?to={other}'
            f'&amp;back={urllib.parse.quote(here, safe="")}">'
            f'⇄ {_src_name(other)}</a>')


def _page(title: str, body: str, script: str = "", src: str = "tmdb",
          back: str = "", sm: bool = False) -> str:
    src = src if src in _SRC_ALL else "tmdb"
    foot = f"{_src_name(src)} 海报墙 · hai 本地包中枢自托管"
    return (
        "<!DOCTYPE html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
        f"<title>{_e(title)}</title><style>{_CSS}</style>"
        f"<script src='/hai/lib/haiplay.js?v={_jsver()}'></script></head><body>"
        f"{_srcpill(src)}"
        f"<div class=\"wrap\">{body}"
        f"<div class=\"foot\">{foot}</div></div>"
        + _srcdlg(src, back or "/hai/poster/", sm)
        + (f"<script>{script}</script>" if script else "")
        + f"<script>{_JS_SRCMENU}</script>"
        + f"<script>{_JS_SELFHEAL}</script>"
        + "</body></html>"
    )


def _card(it: dict) -> str:
    return (
        f"<a class=\"card\" href=\"/hai/poster/d/{_e(it.get('vod_id'))}\">"
        f"<div class=\"pic\"><img src=\"{_pic(it.get('vod_pic'))}\" loading=\"lazy\" alt=\"\">"
        f"<span class=\"badge\">{_e(it.get('vod_remarks'))}</span></div>"
        f"<h3>{_e(it.get('vod_name'))}</h3>"
        f"<div class=\"sub\">{_e(it.get('vod_year'))}</div></a>"
    )


def _q(tid, page: int = 1, country: str = "", sort: str = "",
       genre: str = "", src: str = "") -> str:
    """海报墙 URL（筛选状态全在 query 里，服务端渲染 —— 电视 WebView 最稳）"""
    parts = [f"t={urllib.parse.quote(str(tid), safe='')}"]
    if country:
        parts.append("c=" + urllib.parse.quote(str(country), safe=""))
    if sort:
        parts.append("s=" + urllib.parse.quote(str(sort), safe=""))
    if genre:
        parts.append("g=" + urllib.parse.quote(str(genre), safe=""))
    if src:
        parts.append("src=" + src)
    if page and page > 1:
        parts.append(f"pg={page}")
    return "/hai/poster/?" + "&".join(parts)


_JS_WALL = """
(function () {
  var tabs = [].slice.call(document.querySelectorAll('.tabs a'));
  var cards = [].slice.call(document.querySelectorAll('.wall a.card'));
  var panel = document.getElementById('fpanel');
  var wall = document.getElementById('wallbox');
  if (!cards.length && !tabs.length) { return; }

  function makeGrid(els) {
    var rows = [];
    for (var i = 0; i < els.length; i++) {
      var el = els[i], placed = false;
      for (var r = 0; r < rows.length; r++) {
        if (Math.abs(rows[r][0].offsetTop - el.offsetTop) < 24) {
          rows[r].push(el); placed = true; break;
        }
      }
      if (!placed) { rows.push([el]); }
    }
    rows.sort(function (a, b) { return a[0].offsetTop - b[0].offsetTop; });
    for (var r2 = 0; r2 < rows.length; r2++) {
      rows[r2].sort(function (a, b) { return a.offsetLeft - b.offsetLeft; });
    }
    return rows;
  }

  // ---- 主网格：类目条 + 海报 ----
  var grid = [], row = 0, col = 0;
  function build() {
    var els = tabs.concat(cards);
    grid = els.length ? makeGrid(els) : [];
  }
  function hi() {
    for (var i = 0; i < tabs.length; i++) { tabs[i].classList.remove('sel'); }
    for (var j = 0; j < cards.length; j++) { cards[j].classList.remove('sel'); }
    var el = grid[row] && grid[row][col];
    if (el) { el.classList.add('sel'); }
  }
  function ensure(el) {
    if (!el) { return; }
    var r = el.getBoundingClientRect(), h = window.innerHeight;
    if (r.top < 8) { window.scrollBy(0, r.top - 40); }
    else if (r.bottom > h - 8) { window.scrollBy(0, r.bottom - h + 40); }
  }
  // ---- 焦点预热（2026-11-03）------------------------------------------
  // 光标停在哪张卡上超过 0.7 秒，就把它的选集聚合排进后台队列（串行、最多
  // 排 6 个）。这样点进去选集区往往已经有数据，不用等 20~40 秒。
  // 只排「用户下一眼可能点开的那一部」，绝不批量预扫（见 core/warm.py）。
  var warmTimer = null, warmDone = {};
  function warmFocus(el) {
    clearTimeout(warmTimer);
    if (!el) { return; }
    var h = el.getAttribute('href') || '';
    var m = h.indexOf('/hai/poster/d/') === 0 ? [h, h.slice(14)] : null;
    if (!m || warmDone[m[1]]) { return; }
    warmTimer = setTimeout(function () {
      warmDone[m[1]] = 1;
      var nm = '';
      try {
        var h3 = el.querySelector('h3');
        nm = h3 ? h3.textContent : '';
      } catch (e) {}
      var x = new XMLHttpRequest();
      x.open('GET', '/hai/poster/warm?vid=' + encodeURIComponent(m[1])
        + '&name=' + encodeURIComponent(nm || ''), true);
      x.send();
    }, 700);
  }

  function go(dr, dc) {
    if (!grid.length) { return; }
    // 2026-11-03：翻页改成**后台预取**，不阻塞按键。
    // 之前那版把方向键卡住了：XHR 没回来就一直 return，方向键像死了一样。
    // 现在的语义：焦点进到最后 3 行就去后台拉下一批；拉回来 grid 自然变长，
    // 用户继续按下就能进新的一行；拉不到也不会影响任何按键。
    var nr = Math.min(Math.max(0, row + dr), grid.length - 1);
    var c = (dr === 0) ? col + dc : Math.min(col, grid[nr].length - 1);
    c = Math.min(Math.max(0, c), grid[nr].length - 1);
    row = nr; col = c;
    hi(); ensure(grid[row][col]);
    warmFocus(grid[row][col]);
    // 焦点落到类目上就把这一类的内容换进来（2026-11-02 海哥第二次纠正）：
    // **不要「自动点确定」** —— 整页重载会让焦点回到「我的追剧」、还往历史里
    // 堆一条记录（返回键于是变成网页式无限回退）。改成原地换内容：请求一次
    // 同一套服务端渲染的 HTML，只换 hero / 类目条 / 海报区，URL 用
    // replaceState 替换，焦点留在类目条上 —— 手感就是「翻页」。
    if (isTab(grid[row][col])) { queueSwitch(grid[row][col]); } else { clearTimeout(tabTimer); }
    // 快到底了 → 后台补下一页（不阻塞按键；提前 3 行，贴底时已经有货）
    if (row >= grid.length - 3 && !wallMore.done) { loadMore(null); }
  }
  // ---- 无限翻页：滚到最后一行再取下一批（2026-11-03）-------------------
  var wallMore = { busy: false, done: false, page: 1 };
  (function () {
    var m = location.search.match(/[?&]pg=([0-9]+)/);
    wallMore.page = m ? +m[1] : 1;
  })();

  function moreUrl(n) {
    var q = location.search.replace(/^[?]/, "");
    var parts = q ? q.split("&") : [], out = [], has = false;
    for (var i = 0; i < parts.length; i++) {
      if (!parts[i]) { continue; }
      if (/^pg=/.test(parts[i])) { out.push("pg=" + n); has = true; }
      else { out.push(parts[i]); }
    }
    if (!has) { out.push("pg=" + n); }
    return location.pathname + (out.length ? "?" + out.join("&") : "");
  }

  function loadMore(cb) {
    if (wallMore.busy || wallMore.done) { if (cb) { cb(false); } return; }
    wallMore.busy = true;
    var x = new XMLHttpRequest();
    x.open("GET", moreUrl(wallMore.page + 1), true);
    x.onload = function () {
      wallMore.busy = false;
      if (x.status !== 200 || !x.responseText) { wallMore.done = true; if (cb) { cb(false); } return; }
      var doc = null;
      try { doc = new DOMParser().parseFromString(x.responseText, "text/html"); } catch (e) {}
      var nb = doc && doc.getElementById("wallbox");
      var box = document.getElementById("wallbox");
      var added = 0;
      if (nb && box) {
        var kids = nb.querySelectorAll(".card");
        for (var i = 0; i < kids.length; i++) {
          box.appendChild(document.importNode(kids[i], true));
          added++;
        }
      }
      if (!added) { wallMore.done = true; }
      else {
        wallMore.page++;
        // 关键：grid 由 tabs+cards 构建，而 cards 是页面加载时抓的静态数组。
        // 只 append DOM 不刷新 cards ⇒ grid 不变长 ⇒ 焦点永远停在最后一张老卡片
        // 上（海哥 2026-11-03 实测：只能移到第 19 个）。这里重新抓一次。
        cards = [].slice.call(document.querySelectorAll('.wall a.card'));
        build(); hi();
      }
      if (cb) { cb(added > 0); }
    };
    x.onerror = function () { wallMore.busy = false; wallMore.done = true; if (cb) { cb(false); } };
    x.ontimeout = x.onerror;
    try { x.timeout = 9000; } catch (e) {}
    x.send();
  }

  function openItem() {
    var el = grid[row] && grid[row][col];
    if (!el) { return; }
    var href = el.getAttribute('href');
    if (el.getAttribute('data-act') === 'home') { goHome(); return; }
    if (isTab(el)) {
      // 当前类目上按 OK = 呼筛选（老行为）；其它类目 = 立刻切过去，不等防抖
      if (el.getAttribute('data-filter') === '1') { openPanel(); return; }
      if (href) { switchTab(href, el); }
      return;
    }
    if (href) { savePos(); location.href = href; }
  }

  // ---- 筛选面板 ----
  var frows = [], fsel = [], fr = 0, fc = 0;
  function initPanel() {
    if (!panel) { return; }
    var rs = [].slice.call(panel.querySelectorAll('.frow'));
    for (var i = 0; i < rs.length; i++) {
      var opts = [].slice.call(rs[i].querySelectorAll('.fopt'));
      if (!opts.length) { continue; }
      var sel = -1;
      for (var j = 0; j < opts.length; j++) {
        if (opts[j].className.indexOf('sel') >= 0) { sel = j; }
      }
      frows.push(opts); fsel.push(sel < 0 ? 0 : sel);
    }
  }
  function fHi() {
    for (var i = 0; i < frows.length; i++) {
      for (var j = 0; j < frows[i].length; j++) {
        frows[i][j].classList.remove('focus');
      }
    }
    var el = frows[fr] && frows[fr][fc];
    if (el) { el.classList.add('focus'); }
  }
  function fGo(dr, dc) {
    if (!frows.length) { return; }
    if (dr) {
      fr = Math.min(Math.max(0, fr + dr), frows.length - 1);
      fc = Math.min(fsel[fr], frows[fr].length - 1);
    } else {
      fc = Math.min(Math.max(0, fc + dc), frows[fr].length - 1);
    }
    fHi();
  }
  function fOpen() {
    var el = frows[fr] && frows[fr][fc];
    if (!el) { return; }
    var href = el.getAttribute('href');
    if (!href || href === '#') { closePanel(); return; }
    // replace 而不是 href：筛条件不往历史里堆（返回键要像 app，不是浏览器）
    savePos();
    closePanel(true);
    location.replace(href);
  }
  // 筛选面板也垫一条历史：按返回先关面板，而不是直接退出 app。
  // （Activity 的 onKeyDown 会先吃掉返回键 → canGoBack 就 goBack，所以只有
  //   垫历史才能让「返回」落在浮层上。）
  var fpPushed = false;
  function openPanel() {
    if (!panel) { return; }
    panel.classList.add('show');
    if (!frows.length) { initPanel(); }
    fr = 0; fc = fsel[0] || 0;
    fHi();
    if (!fpPushed) {
      fpPushed = true;
      try { history.pushState({ fp: 1 }, '', location.href); } catch (e) { fpPushed = false; }
    }
  }
  function closePanel(noback) {
    if (!panel) { return; }
    panel.classList.remove('show');
    hi();
    if (fpPushed && !noback) {
      fpPushed = false;
      try { history.back(); } catch (e) {}
    } else {
      fpPushed = false;
    }
  }
  window.addEventListener('popstate', function () {
    if (fpPushed) { fpPushed = false; closePanel(true); }
  }, false);

  // ---- 焦点/滚动记忆（从详情页返回要回到原位）----
  // 海哥 2026-11-02：从详情页返回海报墙，焦点总是跳回第一格，得重新找。
  var MKEY = 'hai.wall.pos';
  function urlKey() { return location.pathname + location.search; }
  function savePos() {
    try {
      var el = grid[row] && grid[row][col];
      localStorage.setItem(MKEY, JSON.stringify({
        u: urlKey(), y: window.pageYOffset,
        m: el ? (el.getAttribute('href') || '') : ''
      }));
    } catch (e) {}
  }
  function restorePos() {
    try {
      var d = JSON.parse(localStorage.getItem(MKEY) || '{}');
      if (!d || d.u !== urlKey()) { return; }
      if (d.y) { window.scrollTo(0, d.y); }
      if (d.m) {
        for (var i = 0; i < grid.length; i++) {
          for (var j = 0; j < grid[i].length; j++) {
            if (grid[i][j].getAttribute('href') === d.m) {
              row = i; col = j; hi(); ensure(grid[i][j]); return true;
            }
          }
        }
      }
    } catch (e) {}
    return false;
  }

  // ---- 类目切换：原地换内容，像翻页（2026-11-02 海哥纠正版）----
  //
  // 老的写法是「焦点落到类目上 → 260ms 后整页 location.href」。三个毛病：
  //   1. 整页重载 ⇒ 焦点回到「我的追剧」，得重新找位置；
  //   2. 每切一次就往 WebView 历史里堆一条 ⇒ 返回键变成网页式无限回退；
  //   3. 页面白闪一下，像刷新，不像翻页。
  // 现在：XHR 取同一套服务端渲染的 HTML，只替换 hero / 类目条 / 海报 / 筛选
  // 面板，history.replaceState 改地址（不新增历史），焦点留在类目条原位。
  var tabTimer = null, tabSeq = 0, swCache = {}, upCount = 0, upTimer = null;
  function isTab(el) {
    // 只认真正的「类目」——影人页 / 搜索页的「← 返回」也在 .tabs 里，
    // 但它们不是类目，不能让焦点挪过去就触发换内容。
    return !!(el && el.getAttribute && el.getAttribute('data-tab') === '1');
  }

  function queueSwitch(el) {
    clearTimeout(tabTimer);
    if (!el) { return; }
    tabTimer = setTimeout(function () { switchTab(el.getAttribute('href'), el); }, 200);
  }

  function switchTab(href, el) {
    if (!href || !el) { return; }
    // 已经是这一类（服务端给了 on），不用重复拉
    if (/(^|\\s)on(\\s|$)/.test(el.className)) { return; }
    if (href === urlKey()) { return; }
    var seq = ++tabSeq;
    var cached = swCache[href];
    if (cached) { applyTab(href, el, cached, seq); return; }
    var x = new XMLHttpRequest();
    x.open('GET', href, true);
    x.onload = function () {
      if (seq !== tabSeq) { return; }
      if (x.status !== 200 || !x.responseText) { location.replace(href); return; }
      swCache[href] = x.responseText;
      applyTab(href, el, x.responseText, seq);
    };
    x.onerror = function () { if (seq === tabSeq) { location.replace(href); } };
    x.send();
  }

  function applyTab(href, el, html, seq) {
    if (seq !== tabSeq) { return; }
    var doc = null;
    try { doc = new DOMParser().parseFromString(html, 'text/html'); } catch (e) {}
    if (!doc || !doc.getElementById('wallbox')) { location.replace(href); return; }
    var onTabRow = isTab(grid[row] && grid[row][col]) || (row === 0);
    applyDoc(doc);
    wallMore.page = 1; wallMore.done = false; wallMore.busy = false;
    try { history.replaceState(null, '', href); } catch (e) {}
    if (onTabRow) {
      // 焦点跟到新类目条上（不再回「我的追剧」）。
      // 注意类目条会折成两行（正在上映/即将上映/⌂ 主页 在第二行），所以
      // 必须按元素在 grid 里的真实 (row,col) 定位，不能拿数组下标硬顶。
      var target = null;
      for (var i = 0; i < tabs.length; i++) {
        if (tabs[i].getAttribute('href') === href) { target = tabs[i]; }
      }
      if (target) { focusEl(target); } else { row = 0; col = 0; hi(); }
    } else {
      row = Math.min(row, grid.length - 1);
      col = Math.min(col, grid[row].length - 1);
      hi(); ensure(grid[row][col]);
    }
  }

  // 把焦点挪到某个元素（按 grid 的真实行列）
  function focusEl(el) {
    if (!el) { return false; }
    for (var i = 0; i < grid.length; i++) {
      for (var j = 0; j < grid[i].length; j++) {
        if (grid[i][j] === el) { row = i; col = j; hi(); ensure(el); return true; }
      }
    }
    return false;
  }

  // 把新页面的动态块搬进当前页（顺序：hero → 品牌行 → 类目条 → 海报 → 筛选）
  function applyDoc(doc) {
    var tb = document.querySelector('.tabs');
    var nh = doc.querySelector('.whero'), oh = document.querySelector('.whero');
    if (nh && oh) { oh.parentNode.replaceChild(nh, oh); }
    else if (nh && tb) { tb.parentNode.insertBefore(nh, tb); }
    else if (!nh && oh) { oh.parentNode.removeChild(oh); }

    var nb = doc.querySelector('.brand'), ob = document.querySelector('.brand');
    if (nb && ob) { ob.innerHTML = nb.innerHTML; }

    var nt = doc.querySelector('.tabs');
    if (tb && nt) { tb.innerHTML = nt.innerHTML; }

    var nw = doc.getElementById('wallbox'), ow = document.getElementById('wallbox');
    if (nw && ow) { ow.innerHTML = nw.innerHTML; }

    var nfp = doc.getElementById('fpanel'), ofp = document.getElementById('fpanel');
    if (nfp && ofp) { ofp.parentNode.replaceChild(nfp, ofp); }
    else if (nfp && ow) { ow.parentNode.insertBefore(nfp, ow.nextSibling); }
    else if (!nfp && ofp) { ofp.parentNode.removeChild(ofp); }

    tabs = [].slice.call(document.querySelectorAll('.tabs a'));
    cards = [].slice.call(document.querySelectorAll('.wall a.card'));
    panel = document.getElementById('fpanel');
    frows = []; fsel = []; fr = 0; fc = 0;
    fpPushed = false;
    build();
  }

  // ---- 「⌂ 主页」：进壳的原生主页（点播 → 网盘与弹幕配置 → 站点扫 CK）----
  // 之前走 hai.nav('setting') 直接落到壳的「设置」页，海哥要的是**主页**：
  // 网盘/弹幕的 CK 是在「点播」里的站点管理扫的，设置页里没有那一项。
  // 走 `haiplay://native` 而不是 hai.nav('home')：这条路由 WebDetailActivity
  // 拦截后会给 HomeActivity 打 hai.wall 标记 —— 它按返回就直接回海报墙。
  // （原生 onBackPressed 是「再按一次退出」，不打标记返回键会被吞掉，回不来。）
  function goHome() {
    try { location.href = 'haiplay://native'; } catch (e) {}
    return true;
  }

  build(); hi();
  // ---- 类目页后台预取（2026-11-03）--------------------------------------
  // 切类目要服务端现渲染一页（TMDB 冷缓存 0.5~1.5 秒），来回切就一顿一顿。
  // 页面停住 0.9 秒后开始**串行**把其它类目的 HTML 拉一遍存进 swCache，
  // 之后任何一次切换都是内存里取 —— 真正像「翻页」。
  // 串行 + 150ms 间隔：别把电视那点带宽一次性占满，也别把 TMDB 打到限流。
  (function prefetchTabs() {
    var i = 0;
    function step() {
      if (i >= tabs.length) { return; }
      var a = tabs[i++];
      if (!a || !isTab(a)) { step(); return; }
      var href = a.getAttribute('href');
      if (!href || href === urlKey() || swCache[href]
          || /(^|\\s)on(\\s|$)/.test(a.className)) { step(); return; }
      var x = new XMLHttpRequest();
      x.open('GET', href, true);
      x.onload = function () {
        if (x.status === 200 && x.responseText
            && x.responseText.indexOf('id="wallbox"') > 0) {
          swCache[href] = x.responseText;
        }
        setTimeout(step, 150);
      };
      x.onerror = function () { setTimeout(step, 300); };
      x.send();
    }
    setTimeout(step, 900);
  })();
  // 焦点默认落在**当前类目**上（服务端给了 on 的那个），不要固定在第一格 ——
  // 否则一进来光标在「我的追剧」，看着就像被踢回去了（海哥 2026-11-02）。
  if (!restorePos()) {
    for (var ti = 0; ti < tabs.length; ti++) {
      if (/(^|\\s)on(\\s|$)/.test(tabs[ti].className)) {
        if (focusEl(tabs[ti])) { break; }
      }
    }
  }

  // ---- 「⚙ 主页」（旧名「设置」）：进壳的原生主页 ----
  var bs = document.getElementById('btnSet');
  if (bs) {
    bs.addEventListener('click', function (ev) {
      ev.preventDefault();
      goHome();
      return false;
    });
  }

  document.addEventListener('keydown', function (e) {
    var k = e.keyCode;
    // 数据源浮层打开时全部交给它（它自己用 capture 处理），这里不要再挪焦点
    if (window.__haiSrcIsOpen && window.__haiSrcIsOpen()) { return; }
    var inPanel = panel && panel.className.indexOf('show') >= 0;
    if (inPanel) {
      if (k === 38) { fGo(-1, 0); }
      else if (k === 40) { fGo(1, 0); }
      else if (k === 37) { fGo(0, -1); }
      else if (k === 39) { fGo(0, 1); }
      else if (k === 13 || k === 23) { fOpen(); }
      else if (k === 4 || k === 27 || k === 8 || k === 461) { closePanel(); }
      else { return; }
      e.preventDefault();
      return false;
    }
    if (k === 38) {
      // 2026-11-02 海哥：类目条上**连按 3 次 ↑** 呼出「豆瓣 / TMDB」切换卡片。
      // 一次 ↑ 只是在类目条内上移；连按三次才弹（1.2 秒内算连击）。
      if (row <= 0) {
        upCount++;
        clearTimeout(upTimer);
        upTimer = setTimeout(function () { upCount = 0; }, 1200);
        if (upCount >= 3) {
          upCount = 0;
          if (window.__haiSrcOpen) { window.__haiSrcOpen(); }
        }
      } else {
        upCount = 0;
        go(-1, 0);
      }
    }
    else if (k === 40) { go(1, 0); }
    else if (k === 37) { go(0, -1); }
    else if (k === 39) { go(0, 1); }
    else if (k === 13 || k === 23) { openItem(); }
    else if (k === 82) { openPanel(); }        // MENU 键也能呼出
    else { return; }
    e.preventDefault();
    return false;
  }, false);

  // 触屏/鼠标也能用：点类目条 / 筛选条件 —— 和遥控同一套（原地切、不堆历史）
  document.addEventListener('click', function (ev) {
    var a = ev.target;
    while (a && a.nodeType === 1 && a.tagName !== 'A') { a = a.parentNode; }
    if (!a || a.tagName !== 'A') { return; }
    var p = a, inPanel = false;
    while (p) { if (p.id === 'fpanel') { inPanel = true; break; } p = p.parentNode; }
    if (inPanel) {
      var h = a.getAttribute('href');
      ev.preventDefault();
      if (!h || h === '#') { closePanel(); return false; }
      savePos(); closePanel(true); location.replace(h);
      return false;
    }
    if (a.getAttribute('data-act') === 'home') {
      ev.preventDefault(); goHome(); return false;
    }
    if (a.getAttribute('data-tab') === '1') {
      ev.preventDefault();
      if (a.getAttribute('data-filter') === '1') { openPanel(); return false; }
      switchTab(a.getAttribute('href'), a);
      return false;
    }
  }, false);
  var fb = document.getElementById('fbtn');
  if (fb) {
    fb.addEventListener('click', function (e) {
      e.preventDefault(); openPanel(); return false;
    });
  }
})();
"""


def _filter_panel(type_id, page: int, country: str, sort: str,
                  genre: str, src: str = "tmdb") -> str:
    """筛选面板 HTML：三组条件 + 关闭项，每项直接带完整 URL（点了就重渲染）"""
    ctry, sorts, gens = _filters(src)

    def row(label: str, items: list[tuple[str, str]], cur: str, key: str) -> str:
        opts = []
        for val, name in items:
            url = _q(type_id, 1,
                     val if key == "c" else country,
                     val if key == "s" else sort,
                     val if key == "g" else genre,
                     src)
            on = " sel" if val == cur else ""
            opts.append(f"<a class=\"fopt{on}\" href=\"{_e(url)}\">{_e(name)}</a>")
        return (f"<div class=\"frow\"><div class=\"fl\">{label}</div>"
                f"<div class=\"fopts\">{''.join(opts)}</div></div>")

    close = ("<div class=\"frow\"><div class=\"fl\">操作</div><div class=\"fopts\">"
             f"<a class=\"fopt\" href=\"#\">✕ 关闭</a></div></div>")
    return (
        "<div id=\"fpanel\"><div class=\"fbox\">"
        "<div class=\"fh\">筛选</div>"
        + row("地区 / 国家", ctry, country, "c")
        + row("排序", sorts, sort, "s")
        + row("影片类型", list(gens), genre, "g")
        + close
        + "</div></div>"
    )


def _fsum(triple, country: str, sort: str, genre: str) -> str:
    """把当前筛选条件拼成一句人话（两套数据共用）。"""
    ctry, sorts, gens = triple
    names = []
    for pool, cur in ((ctry, country), (sorts, sort), (gens, genre)):
        if not cur:
            continue
        nm = next((n for v, n in pool if v == cur), cur)
        names.append(nm)
    return " · ".join(names)


def _base_tid(cid) -> int:
    """把三种写法（`2` / `db2` / `"2"`）统一成数字 tid。

    两个数据源的 cid 数字部分是对齐的（2=热门电影 … 11=即将上映），
    所以切数据源后同一个位置仍是同一类，筛选条件也能原样带过去。
    """
    s = str(cid or "").strip()
    m = re.match(r"^db(\d+)$", s)
    if m:
        return int(m.group(1))
    try:
        return int(s)
    except (TypeError, ValueError):
        return 2


def _cid_of(src: str, tid: int) -> str:
    """数字 tid → 该数据源用的 cid 写法。"""
    return ("db%d" % tid) if src == "douban" else str(tid)


def _src_of(vid) -> str:
    return "douban" if str(vid or "").startswith("db") else "tmdb"


def _mod_for_vid(vid):
    return _mod(_src_of(vid))


def _hero(items, src: str) -> str:
    """首图 hero：取第一条的详情做背景 + 标题 + 角标。两源同一套排版。"""
    if not items:
        return ""
    top = items[0]
    d = {}
    try:
        d = _mod_for_vid(top.get("vod_id")).detail(top.get("vod_id"),
                                                   with_plays=False) or {}
    except Exception:
        d = {}
    # 横幅要横图：豆瓣的剧照 / TMDB 的 backdrop 都是 16:9，
    # 直接把竖版海报铺成横幅会只剩中间一条，很难看。
    wide = (d.get("vod_backdrops") or [])[:1]
    bg = _pic((wide[0] if wide else "")
              or d.get("vod_pic_slide") or d.get("vod_pic")
              or top.get("vod_pic") or "")
    desc = str(d.get("vod_content") or "").replace("\n\n", " ")[:170]
    chips = []
    if top.get("vod_remarks"):
        chips.append(f"<span class=\"chip score\">{_e(top['vod_remarks'])}</span>")
    for v in (d.get("vod_year") or top.get("vod_year"), d.get("vod_area")):
        if v:
            chips.append(f"<span class=\"chip\">{_e(v)}</span>")
    if d.get("type_name"):
        chips.append(f"<span class=\"chip p\">{_e(d['type_name'])}</span>")
    return (
        "<div class=\"whero\">"
        f"<img class=\"bg\" src=\"{bg}\" alt=\"\">"
        "<div class=\"shade\"></div>"
        "<div class=\"info\">"
        f"<h1>{_e(top.get('vod_name'))}</h1>"
        f"<div class=\"chips\">{''.join(chips)}</div>"
        f"<p class=\"desc\">{_e(desc)}</p>"
        "</div></div>"
    )


def _tabs(src: str, tid: int, page: int, country: str, sort: str,
          genre: str) -> str:
    """类目条：我的追剧 + 该数据源的 10 个类目。

    默认**不放**「⚙ 筛选」这个标识（海哥 11-01：类目条要干净）——
    筛选改成隐藏入口：
      * 在**当前类目**上按 OK  → 呼出筛选面板；
      * 在类目条任意位置按 ↑  → 呼出豆瓣/TMDB 切换开关。
    """
    out = []
    # 2026-11-02 海哥：类目条上**不要**「豆瓣 / TMDB」按钮了（占位置、还容易误触），
    # 改成「在类目上连按 3 次上键」呼出切换卡片（见 _JS_WALL）。
    wcid = _cid_of(src, T.WATCH_TYPE)
    on = " on" if tid == T.WATCH_TYPE else ""
    attr = " data-filter=\"1\"" if tid == T.WATCH_TYPE else ""
    out.append(f"<a class=\"{on.strip()}\" data-tab=\"1\"{attr} href=\""
               f"{_e(_q(wcid, 1, country, sort, genre, src))}\">我的追剧</a>")
    for cid, name in _classes(src):
        c_tid = _base_tid(cid)
        on = " on" if c_tid == tid else ""
        attr = " data-filter=\"1\"" if c_tid == tid else ""
        out.append(f"<a class=\"{on.strip()}\" data-tab=\"1\"{attr} href=\""
                   f"{_e(_q(cid, 1, country, sort, genre, src))}\">{_e(name)}</a>")
    # 末尾留一个「⚙ 设置」—— 回原生首页的唯一入口（2026-11-01）。
    # 启动器换成海报墙之后，app 自己的「设置 / 网盘扫码登录」就没地方进了；
    # 网盘 CK 不登录，Push 解析夸克/百度分享恒返回空，播放必然失败。
    # 用 <a> 放在 .tabs 里，天生进焦点池，遥控按到底就能选到。
    out.append('<i class="tsep"></i>')
    # 「⌂ 主页」= 进壳的原生**主页**（不是设置页）。
    # 2026-11-02 海哥纠正：壳的设置页里没有网盘扫码入口 —— CK 是「点播」里的
    # 「网盘与弹幕配置 → 站点管理」扫的，所以按钮必须落在主页上。走 JS 桥
    # hai.nav('home') 直接 startActivity（启动器换成海报墙后，这是回原生的
    # 唯一入口）；遥控 OK 走 _JS_WALL 的 openItem → data-act="home"。
    out.append('<a class="srcbtn" id="btnSet" data-act="home" '
               'href="javascript:void(0)">\u2302 \u4e3b\u9875</a>')
    return "".join(out)


def page_home(type_id=1, page: int = 1, country: str = "",
              sort: str = "", genre: str = "", src: str = "tmdb",
              sm: bool = False) -> str:
    """海报墙首页。**两个数据源共用这一套渲染** —— 这是「UI 与操作完全对齐」
    的唯一可靠做法：只要分成两份代码，迟早会长歪。数据源差异全部藏在
    `_mod(src)` 后面（列表 / 详情 / 类目三条接口）。"""
    src = src if src in _SRC_ALL else load_src()
    M = _mod(src)
    tid = _base_tid(type_id)
    if tid == T.WATCH_TYPE:
        data = T._watch_page(page)
    else:
        try:
            data = M.list_page(_cid_of(src, tid), page, country, sort, genre)
        except Exception:
            data = {"page": page, "pagecount": 1, "list": []}
    items = data.get("list") or []

    fsum = _fsum(_filters(src), country, sort, genre)
    here = _q(_cid_of(src, tid), page, country, sort, genre, src)
    cards = "".join(_card(x) for x in items[1:])
    if not items:
        if tid == T.WATCH_TYPE:
            cards = ("<div class=\"empty\">还没有追剧记录。"
                     "去别的类目点开一部片，详情页点「＋ 一键追剧」就会出现在这里。</div>")
        else:
            cards = ("<div class=\"empty\">这一类暂时没返回数据（数据站偶发限流）。"
                     "等几秒按返回再进一次就好。</div>")
    summary = f"<span class=\"fsum\">筛选：<b>{_e(fsum)}</b></span>" if fsum else ""
    sub = "豆瓣 · 国产剧更新更快" if src == "douban" else "TMDB · 深色影院"
    show_filter = tid != T.WATCH_TYPE and bool(items)
    body = (
        f"<div class=\"brand\"><b>海报墙</b><span>{sub}</span>{summary}</div>"
        + _hero(items, src)
        + f"<div class=\"tabs\">{_tabs(src, tid, page, country, sort, genre)}</div>"
        + f"<div class=\"wall\" id=\"wallbox\">{cards}</div>"
        + ("" if not show_filter
           else _filter_panel(_cid_of(src, tid), page, country, sort, genre, src))
    )
    return _page("海报墙 · " + _src_name(src), body, _JS_WALL, src, here, sm)


def page_detail(vid: str, retry: int = 0, sm: bool = False) -> str:
    # 数据源看 vod_id 前缀：豆瓣统一 `db<id>`，TMDB 是 `m/t<id>`。
    # 这样链接本身就能表达数据源，不用额外参数，分享/收藏也不会串。
    src = "douban" if str(vid or "").startswith("db") else "tmdb"
    M = _mod(src)
    # 只拿元数据：两套数据都有长缓存，基本上就是本地字典查找。
    # 这里**绝不能**用 with_plays=True —— 那样会同步跑一遍网盘搜索（花卷最慢要
    # 5 秒），详情页首屏从 0.2 秒直接变成 3 秒以上，实拍反馈过"打开要 3 秒"。
    d = M.detail(vid, with_plays=False)
    if not d:
        # 2026-10-31 真机反馈「播完返回偶尔整页空白」：多半是 TMDB 侧抖动/
        # 限流导致元数据拿空，页面就只剩一行字。这里改成**自动重试页**：
        # 每 2.5 秒自己刷一次，最多 3 次，刷不到才认输。
        if retry < 3:
            body = (
                "<div class=\"brand\"><b>海报墙</b></div>"
                f"<div style=\"margin-top:14vh;text-align:center\">"
                f"<div style=\"font-size:17px;color:#9ff5cd;letter-spacing:2px\">"
                f"正在重新载入影片信息…（{retry + 1}/3）</div>"
                "<div style=\"margin-top:10px;font-size:13px;color:#6f7c93\">"
                "网络抖动时数据站会短暂拿不到数据，页面会自动重试</div>"
                f"<div style=\"margin-top:26px\">"
                f"<a class=\"btn\" href=\"/hai/poster/d/{_e(vid)}\">立即重试</a>"
                f"<a class=\"btn ghost\" href=\"/hai/poster/\">返回海报墙</a></div></div>"
            )
            html = _page("载入中", body, "", src, f"/hai/poster/d/{_e(vid)}")
            # meta refresh 比 JS 稳（WebView 被冻结时 JS 定时器不一定跑）
            html = html.replace("</head>",
                                f"<meta http-equiv=\"refresh\" content=\"2.5;url="
                                f"/hai/poster/d/{_e(vid)}?r={retry + 1}\"></head>")
            return html
        return _page("未找到", "<div class=\"brand\"><b>海报墙</b></div>"
                              f"<p class=\"desc\">{_src_name(src)} 暂时没有返回这部片的"
                              "数据，回上一层再进一次通常就好了。</p>"
                              "<p><a class=\"btn\" href=\"/hai/poster/\">← 返回海报墙</a></p>",
                     "", src, "/hai/poster/")
    bg = _pic(d.get("vod_pic_slide") or "")
    chips = []
    if d.get("vod_remarks"):
        chips.append(f"<span class=\"chip score\">{_e(d['vod_remarks'])}</span>")
    for k in ("vod_year", "vod_area", "vod_lang"):
        if d.get(k):
            chips.append(f"<span class=\"chip\">{_e(d[k])}</span>")
    if d.get("type_name"):
        chips.append(f"<span class=\"chip p\">{_e(d['type_name'])}</span>")

    # 导演/主演做成和 crew chip 一致的外观（2026-11-01 海哥：导演主演介绍
    # 要和演员头像放一起）。这里只负责兜底 —— 有 crew 明细时用 crew_html。
    kv = []
    if d.get("vod_director"):
        kv.append(f'<span class="ci"><b>导演</b>{_e(d["vod_director"])}</span>')
    if d.get("vod_actor"):
        kv.append(f'<span class="ci wide"><b>主演</b>{_e(d["vod_actor"])}</span>')
    # 只取简介正文：导演/主演/TMDB 评分在下面单独排版，避免重复出现两遍
    desc = str(d.get("vod_content") or "").split("\n\n")[0]

    # ---- 操作区：播放（跳转） / 一键追剧 ----------------------------------
    #
    # 播放**不在网页里放**：网盘分享要转存、要 Cookie，网页播不了。
    # 这里只负责把请求交回 app —— 用自定义 scheme `haiplay://`，由改造后的
    # WebDetailActivity 在 shouldOverrideUrlLoading 里拦截，然后启动原生播放器
    # （VideoActivity，播放源由它自己去 detail() 拿，夸克优先）。
    # 线路名只从网盘缓存里读（不触发搜索）。第一次打开这部片时缓存是空的，
    # 页面照常秒开，只显示一句"点播放时自动匹配"，不让用户对着转圈等。
    src_names = []
    try:
        from . import panwall
        src_names = [n.split("·")[1] if "·" in n else n
                     for n, _ in panwall.peek(d.get("vod_name") or "",
                                              d.get("vod_year") or "")]
    except Exception:
        src_names = []
    src_names = list(dict.fromkeys(src_names))
    lines_txt = " · ".join(src_names[:4])

    watched = False
    try:
        from . import watchlist
        watched = watchlist.has(vid)
    except Exception:
        pass

    # 播放 / 搜索分流（2026-10-30 海哥定稿；11-01 改「搜索」走我们自己的页面）：
    #   ▶ 播放 —— 打开页面后 JS 后台调 /hai/poster/play 解析站源 vod id，
    #     成功就把 href 换成 haiplay://play?site=Wogg&id=/voddetail/n.html…
    #     （app 拦截后直达该站选集页，OK 即播）；解析失败保持搜索兜底。
    #   🔍 搜索 —— **我们自己画的结果页**（/hai/poster/q）。原来直接跳
    #     haiplay://search 会进 app 原生搜索页（白底列表，很丑）。海哥要求
    #     「所有流程不跳原生页面」，所以这里改成自绘。
    # 播放**必须**交给原生播放器：网盘要转存、要 Cookie，网页播不了。那是
    # 唯一 unavoidable 的一次跳转，其余全在我们的页面里。
    q = urllib.parse.quote(d.get("vod_name") or "")
    play_btn = (f"<a class=\"btn\" id=\"btnPlay\" "
                f"href=\"haiplay://search?q={q}\">▶ 播放</a>")
    search_btn = (f"<a class=\"btn ghost\" href=\"/hai/poster/q?kw={q}\">"
                  "🔍 搜索</a>")
    if watched:
        watch_btn = (f"<a class=\"btn on\" href=\"/hai/poster/watch?act=del&vid={_e(vid)}"
                     f"&back=/hai/poster/d/{_e(vid)}\">✓ 已追剧</a>")
    else:
        watch_btn = (f"<a class=\"btn ghost\" href=\"/hai/poster/watch?act=add&vid={_e(vid)}"
                     f"&back=/hai/poster/d/{_e(vid)}\">＋ 一键追剧</a>")

    # 「▦ 海报墙」按钮已删（2026-11-01 海哥）：详情页是我们自己的客厅，不该
    # 再放一个「回我的海报墙」的入口 —— 那等于承认自己是个外挂页面。返回键
    # 本来就是回海报墙，多余的按钮只会挤掉「播放 / 搜索 / 追剧」的焦点位。

    # ---- 演职员（点头像 → 我们自己的影人页） ------------------------------
    #
    # 豆瓣详情只给演员名字，没有 id / 头像 —— 由 /hai/poster/cast 后台补，
    # 页面首屏不等它（14 个人串行要 10 秒以上）。补到之前先用「名字首字母」
    # 圆头像占位，并把 href 指向我们自己的搜索页（不跳原生搜索）。
    cast_full = d.get("vod_cast") or []
    crew_full = d.get("vod_crew") or []
    cast_html = ""
    need_cast_api = False
    if cast_full:
        items = []
        for c in cast_full:
            name = c.get("name") or ""
            cid = str(c.get("cid") or "")
            pic = _pic(c.get("profile"))
            # 首字母占位。豆瓣头像走 /hai/dbimg/ 代理，代理偶尔 502 / 电视
            # wifi 抽风时整块圆会是一个纯黑空洞（2026-11-01 真机截图实锤），
            # 所以给 img 挂 onerror：先带 cache-buster 重试一次，仍失败就换
            # 成首字母 —— 任何情况下都不留黑圈。
            letter = _e((name or "?")[:1])
            if pic:
                face = ('<div class="cface"><img src="%s" alt="" data-ch="%s" '
                        'onerror="haiFaceMiss(this)"></div>' % (pic, letter))
            else:
                face = f'<div class="cface noimg">{letter}</div>'
                need_cast_api = True
            href = (f"/hai/poster/p/{_e(cid)}" if cid
                    else "/hai/poster/q?kw=" + urllib.parse.quote(name))
            items.append(
                f'<a class="citem" data-nm="{_e(name)}" href="{href}">{face}'
                f'<div class="cname">{_e(name)}</div>'
                f'<div class="crole">{_e(c.get("character"))}</div></a>'
            )
        cast_html = (_JS_FACE + '<div class="cast">' + "".join(items) + "</div>")
    crew_html = ""
    if crew_full:
        cis = []
        for c in crew_full:
            job = {"Director": "导演", "Writer": "编剧", "Screenplay": "编剧",
                   "Original Music Composer": "配乐"}.get(c.get("job"), c.get("job"))
            nm = _e(c.get("name"))
            cid = str(c.get("cid") or "")
            body_txt = (f'<a class="cip" href="/hai/poster/p/{_e(cid)}">{nm}</a>'
                        if cid else nm)
            cis.append(f'<span class="ci"><b>{_e(job)}</b>{body_txt}</span>')
        crew_html = '<div class="crew">' + "".join(cis) + "</div>"

    # ---- 相关推荐（真链接，跳海报墙详情页） -------------------------------
    recs = d.get("vod_recs") or []
    rec_html = ""
    if recs:
        cards = []
        for r in recs:
            pic = _pic(r.get("pic"))
            cards.append(
                f'<a class="card" href="/hai/poster/d/{_e(r.get("vid"))}">'
                f'<div class="pic"><img src="{pic}" loading="lazy" alt="">'
                f'<span class="badge">{_e(r.get("remark"))}</span></div>'
                f'<h3>{_e(r.get("name"))}</h3>'
                f'<div class="sub">{_e(r.get("year"))}</div></a>'
            )
        rec_html = '<div class="sect">相关推荐</div><div class="wall">' + "".join(cards) + "</div>"

    # 顶部「大海报」轮播图组：海报 + 背景大图。数量/间隔/转场由维护页配置下发。
    # 遥控器优化：播放/追剧按钮直接放进 hero（页面顶部、一睁眼就看到），
    # 且默认焦点就在「▶ 播放」上 —— 详情页打开后按一次 OK 即播放，零多余点按。
    cfg = load_cfg()
    hero_imgs = [_pic_raw(d.get("vod_pic"))]
    for b in (d.get("vod_backdrops") or [])[:cfg["imgs"] - 1]:
        if b:
            hero_imgs.append(_pic_raw(b))

    # 2026-11-01 海哥定稿的详情页纵向结构（遥控从上往下滚）：
    #
    #   一屏：大海报剧照 + 片名 + 标签 + 三个按钮（▶ 播放 / 搜索 / 追剧）
    #   二屏：聚合选集（季 → 集）+ 演职员（导演主演 chip 行 + 头像网格）
    #   三屏：相关推荐
    #
    # 原来左上角有块「浮动剧名简介卡」，删了 —— 首屏本来就有大标题和简介，
    # 那块卡片只是遮住剧照，还跟选集区抢视觉重心。
    # 原来「导演/主演」在独立的 .dmeta 里、离演职员头像隔着一整个选集区，
    # 现在并进 castSec 的第一行。
    crew_line = crew_html
    if not crew_line and kv:
        crew_line = '<div class="crew">' + "".join(kv) + "</div>"
    cast_sec = ""
    if cast_html or crew_line:
        cast_sec = ('<section id="castSec"><div class="sect">演职员</div>'
                    + crew_line + cast_html + "</section>")

    body = (
        # fxA 必须走 _pic() 代理 —— 电视直连 images.tmdb.org 时通时断，
        # 直连失败整个背景黑屏（2026-10-30 真机实锤：诛仙首开全黑）。
        f"<div id=\"heroFx\" data-cbg=\"{_pic(d.get('vod_pic_slide') or d.get('vod_pic'))}\">"
        f"<img id=\"fxA\" src=\"{_pic(d.get('vod_pic_slide') or d.get('vod_pic'))}\" "
        "style=\"opacity:1\" alt=\"\"><img id=\"fxB\" alt=\"\"></div>"
        "<div id=\"herobgshade\"></div>"
        "<div class=\"hero\">"
        "<div class=\"info\">"
        f"<h1>{_e(d.get('vod_name'))}</h1>"
        f"<div class=\"chips\">{''.join(chips)}</div>"
        f"<div class=\"acts\">{play_btn}{search_btn}{watch_btn}</div>"
        "</div></div>"
        # 二屏：选集（站源行 → 季行 → 集按钮，全部异步填充）
        # 首屏不等它 —— 网盘列目录要好几秒，先让用户看到海报和「▶ 播放」。
        "<section id=\"epSec\"><div class=\"sect\">选集</div>"
        "<div id=\"epBox\"><span class=\"epwait\">正在解析各站源集数…（首次约十秒）</span>"
        "</div></section>"
        + cast_sec
        + (f"<section id=\"recSec\">{rec_html}</section>" if rec_html else "")
    )
    hero_js = (_JS_HERO
               .replace("%IMGS%", json.dumps(hero_imgs, ensure_ascii=False))
               .replace("%INTERVAL%", str(cfg["interval"]))
               .replace("%EFFECT%", cfg["effect"]))
    # 「▶ 播放」—— 起播前后都由我们自己的页面兜住。
    #
    # 目前只能交给 app 的原生聚合搜索（`haiplay://search?q=`）：WebDetailActivity
    # 里那条 9 参入口的语义是「按片名在该站里搜索」，不是「拿 id 直达选集页」
    # （2026-11-01 真机实锤）。要单集直达必须改 smali 重打包 APK。
    #
    # 但网页这边不用干等：点下去先把全屏横版剧照铺上（_JS_CURTAIN），300ms 后
    # 再切播放器 —— 用户看到的是这张剧照，不是黑屏也不是紫色渐变加载页。
    # 同一份遮罩也在「点某集 → 线路浮层 → 播放」那条链上复用。
    _tname = json.dumps(d.get("vod_name") or "", ensure_ascii=False).replace("</", "<\\/")
    _vid_js = json.dumps(str(vid), ensure_ascii=False)
    # 点下去先铺全屏剧照遮罩（_JS_CURTAIN），300ms 后再切播放器 —— 用户
    # 看到的是这张剧照，不是黑屏也不是紫色渐变加载页。同一份遮罩也在
    # 「点某集 → 线路浮层 → 播放」那条链上复用。
    # 点击同时把**当前集号**写进 localStorage：下次进来「▶ 播放」就是续播。
    # （之前 `hai.ep.<vid>` 只读不写，续播永远走第 1 集。）
    _click = (
        "b.addEventListener('click',function(ev){"
        "ev.preventDefault();"
        # 直链要现算（分享→扫目录→转存→取链，约 3~4 秒）。这段时间按钮
        # href 还是兜底值，点了必然失败。所以先「记下这一下」再等结果，
        # 就绪后自动替他按下 —— 用户感觉是「按了就播」，不是按了没反应。
        "if(!b.getAttribute('data-ready')){"
        "window.__haiPending=1;b.innerHTML='\\u51c6\\u5907\\u4e2d\\u2026';"
        "return false;}"
        "var p=window.__haiPick&&window.__haiPick(0);"
        "if(p&&p.ep){try{localStorage.setItem('hai.ep.'+VID,p.ep);}catch(x){}}"
        "if(window.__haiCurtain)window.__haiCurtain(TITLE,p?p.label:'');"
        "var u=b.getAttribute('href');"
        "setTimeout(function(){if(u)window.location.href=u;},300);"
        "return false;},false);"
    )
    # TMDBWall 站点的 detail() 两个源都认：`m/t<tmdb id>` 走 TMDB，
    # `db<subject id>` 走豆瓣扩展。这是**兜底**入口（一定能开，但会多一层
    # 站源选集页）；真正下发给按钮的是下面 push 直入那条。
    _h = ("haiplay://play?site=TMDBWall&id="
          + urllib.parse.quote(str(vid), safe="")
          + "&name=" + q)
    # 起播遮罩用的海报（中枢代理后的相对路径）。播放器窗口里 HaiCover 要的是
    # 绝对地址，由 haiplay.js 的 href() 拼 location.origin 后带过去。
    _picrel = _pic_raw(d.get("vod_pic_slide") or d.get("vod_pic") or "")
    play_js = (
        "window.__haiPic=" + json.dumps(_picrel, ensure_ascii=False) + ";"
        "(function(){var b=document.getElementById('btnPlay');if(!b)return;"
        "var TITLE=" + _tname + ";var VID=" + _vid_js + ";"
        "var CKEY='hai.pl.'+VID,EKEY='hai.eps.'+VID;"
        "var _eps=null,_epsQ=[],_epsBooting=false,_pre={ep:0,h:''};"
        # ---------------- 直链缓存 ----------------
        "function readPl(ep){try{var d=JSON.parse(localStorage.getItem(CKEY)||'{}');"
        # 2026-11-05：拒收含 127.0.0.1 的历史缓存 —— 那是旧版写死的设备回环地址，
        # HaiDp 未生效时是死链。命中就当作没缓存，强制重算（新算出来的是页面 origin）。
        "var v=d[ep];if(v&&v.t>Date.now()&&!/127[.]0[.]0[.]1/.test(String(v.h||'')))return v.h;}catch(e){}return '';}"
        "function writePl(ep,h){try{var d=JSON.parse(localStorage.getItem(CKEY)||'{}');"
        "d[ep]={h:h,t:Date.now()+600000};localStorage.setItem(CKEY,JSON.stringify(d));}catch(e){}}"
        # ---------------- 选集数据（带缓存） ----------------
        "function flushEps(){if(!_epsQ.length)return;var q=_epsQ;_epsQ=[];"
        "for(var i=0;i<q.length;i++){try{q[i](_eps);}catch(e){}}}"
        "function loadEps(cb){"
        "if(_eps){return cb(_eps);}"
        "_epsQ.push(cb);if(_epsBooting)return;_epsBooting=true;"
        "try{var c=localStorage.getItem(EKEY);if(c){_eps=JSON.parse(c);}}catch(e){}"
        "if(_eps)flushEps();"
        "var x=new XMLHttpRequest();"
        "x.open('GET','/hai/poster/eps?vid='+encodeURIComponent(VID),true);"
        "x.onload=function(){var d=null;try{d=JSON.parse(x.responseText);}catch(e){}"
        "if(d&&(d.merged||d.sources)){_eps=d;"
        "try{localStorage.setItem(EKEY,JSON.stringify(d));}catch(e){}}"
        "flushEps();};"
        "x.onerror=function(){flushEps();};x.send();}"
        # ---------------- 候选（跨线路，夸克优先） ----------------
        "function candsOf(d,ep){var out=[],i,j,k,ms=(d&&d.merged)||[];"
        "for(i=0;i<ms.length;i++){var es=ms[i].eps||[];"
        "for(j=0;j<es.length;j++){if(ep&&(es[j].ep||0)!==ep)continue;"
        "var rs=es[j].routes||[];"
        "for(k=0;k<rs.length;k++){if(rs[k].push){"
        "out.push({push:rs[k].push,pan:rs[k].pan||'',ep:es[j].ep||0});}}}}"
        "out.sort(function(a,b){"
        "return (/夸克/.test(a.pan)?0:1)-(/夸克/.test(b.pan)?0:1);});"
        "return out;}"
        # ---------------- 本地算直链：缓存 → 预取 → 并行 2 条 ----------------
        "function localHref(ep,cb){"
        "if(!window.HaiPlay||!window.HaiPlay.ep){return cb(null);}"
        "var h=readPl(ep);if(h){return cb(h);}"
        "if(_pre.ep===ep&&_pre.h){return cb(_pre.h);}"
        "loadEps(function(d){"
        "var cs=candsOf(d,ep);"
        "if(!cs.length){return cb(null);}"
        "var done=false,i=0,inflight=0,left=cs.length;"
        "function good(x){if(done)return;done=true;writePl(ep,x);_pre={ep:ep,h:x};cb(x);}"
        # 2026-11-04 修（真 bug）：原来只在「最后一个候选失败」时才调 bad()，
        # 而 bad() 里 `--left<=0` —— left 初值 = 候选数，**只减一次**永远减不到 0
        # ⇒ cb(null) 永不触发 ⇒ 不回落 askServer ⇒ 页面卡在遮罩不动。
        # 单线路的集（left=1）刚好能减到 0，所以只有**多线路**的集点不动。
        # 现在每失败一个候选就 left--，失败光了才回落。
        "function bad(){if(done)return;if(--left<=0){done=true;cb(null);}}"
        "function launch(){while(!done&&inflight<2&&i<cs.length){"
        "var c=cs[i++];inflight++;"
        "(function(c){window.HaiPlay.ep({share:c.push,ep:c.ep||0,title:TITLE})"
        ".then(function(r){inflight--;"
        "if(r&&r.ok){good(window.HaiPlay.href(r,c.ep||ep||1,r.name||TITLE));}"
        "else{left--;if(left<=0){done=true;cb(null);}else{launch();}}})"
        ".catch(function(){inflight--;left--;"
        "if(left<=0){done=true;cb(null);}else{launch();}});})(c);}}"
        "launch();});}"
        # ---------------- 服务端兜底（本地算不出来才走） ----------------
        "function askThen(){if(!window.__haiAskServer){return;}"
        # 本地算不出来才问中枢 —— 这一步是 3~15 秒的等待，遮罩必须显示进度
        "var _w=window.__haiWatch?window.__haiWatch(VID,want()||1):null;"
        "window.__haiAskServer(want(),function(u){if(_w)_w.stop();"
        "if(u)location.href=u;});}"
        "function want(){var p=window.__haiPick&&window.__haiPick(0);"
        "if(p&&p.ep)return p.ep;"
        "try{return parseInt(localStorage.getItem('hai.ep.'+VID),10)||0;}catch(e){}"
        "return 0;}"
        # ---------------- 点播放：缓存命中 → 立刻全屏 ----------------
        "b.addEventListener('click',function(ev){ev.preventDefault();"
        "var ep=want()||1;"
        "try{localStorage.setItem('hai.ep.'+VID,String(ep));}catch(e){}"
        "var p=window.__haiPick&&window.__haiPick(0);"
        "if(window.__haiCurtain)window.__haiCurtain(TITLE,p?p.label:'');"
        "var h=readPl(ep);"
        "if(h){window.location.href=h;return false;}"
        "b.innerHTML='本地取链…';"
        "localHref(ep,function(x){if(x){window.location.href=x;}else{askThen();}});"
        "return false;},false);"
        # ---------------- 进页面就预取续播集 ----------------
        "function prewarm(){var ep=want()||1;"
        "localHref(ep,function(h){if(h){b.setAttribute('href',h);"
        "b.setAttribute('data-ready','1');"
        "b.innerHTML='\\u25b6 \\u64ad\\u653e \\u7b2c'+ep+'\\u96c6';}"
        # 本地算不出来（百度：非会员只能走服务端；或冷门源）→ **现在就**让
        # 服务端算好放进缓存。等用户真按播放时直接命中，省掉 3~15 秒。
        "else{serverPre(ep);}});}"
        "function serverPre(ep){try{var x=new XMLHttpRequest();"
        "x.open('GET','/hai/poster/play?vid='+encodeURIComponent(VID)"
        "+'&direct=1&ep='+encodeURIComponent(ep||1),true);x.send();}catch(e){}}"
        "setTimeout(prewarm,600);"
        "window.__haiLocal2=function(ep,cb){localHref(ep||0,cb||function(){});};"
        # 服务端兜底：本地算不出来（老 APK / 冷门源）才问中枢
        "window.__haiAskServer=function(ep,cb){"
        "var x=new XMLHttpRequest();"
        "x.open('GET','/hai/poster/play?vid='+encodeURIComponent(VID)"
        "+'&direct=1&ep='+encodeURIComponent(ep||0),true);"
        "x.onload=function(){var d=null;try{d=JSON.parse(x.responseText);}catch(e){}"
        "cb((d&&d.ok&&d.href)?d.href:'');};"
        "x.onerror=function(){cb('');};x.send();};"
        # ?go=1：预取完成后自动按一次「播放」（联调用）
        "if(/[?&]go=1/.test(location.search)){setTimeout(function(){b.click();},2200);}"
        "})();"
    )

    # 豆瓣侧演职员只有名字 —— 后台补头像 + 影人 id（补到就换 href 成影人页）。
    # 首屏绝不等它：14 个名字串行要 10 秒以上。
    cast_js = ""
    if need_cast_api:
        cast_js = _JS_CAST.replace("%VID%", _e(vid))
    eps_js = (_JS_EPS.replace("%VID%", _e(vid))
              .replace("%TITLE%", json.dumps(d.get("vod_name") or "",
                                             ensure_ascii=False).replace("</", "<\\/")))
    curtain_js = _JS_CURTAIN
    return _page(f"{d.get('vod_name')} · 海报墙", body,
                 _JS_NAV + hero_js + play_js + cast_js + curtain_js + eps_js, src,
                 f"/hai/poster/d/{_e(vid)}", sm)


# ---------------------------------------------------------------------------
# 影人页 —— 详情页点头像进来（海哥 11-01：不跳 app 原生搜索页）
# ---------------------------------------------------------------------------

def _person_src(pid: str) -> str:
    """pid 前缀决定数据源：`db<cid>` 豆瓣影人，`tp<id>` TMDB 影人。"""
    return "douban" if str(pid or "").startswith("db") else "tmdb"


def page_person(pid: str, sm: bool = False) -> str:
    pid = str(pid or "").strip()
    src = _person_src(pid)
    M = _mod(src)
    p = {}
    try:
        p = M.person(pid) or {}
    except Exception:
        p = {}
    back = "/hai/poster/?src=" + src
    if not p:
        body = (
            "<div class=\"brand\"><b>海报墙</b></div>"
            "<div class=\"empty\">影人资料暂时没拿到（数据站偶发限流），"
            "回上一层再进一次通常就好了。</div>"
            f"<a class=\"btn\" href=\"{back}\">← 返回海报墙</a>"
        )
        return _page("影人 · 海报墙", body, "", src, back, sm)

    name = p.get("name") or ""
    av = _pic(p.get("avatar"))
    face = (f'<img src="{av}" alt="">' if av
            else f'<div class="cface noimg" style="padding-top:133%;font-size:80px;'
                 f'border-radius:16px">{_e((name or "?")[:1])}</div>')
    latin = p.get("latin") or ""
    intro = str(p.get("intro") or "")
    info = p.get("info") or []
    info_html = "".join(f"<div><b>{_e(a)}</b>{_e(b)}</div>" for a, b in info)

    works = p.get("works") or []
    cards = []
    for w in works:
        cards.append(
            f'<a class="card" href="/hai/poster/d/{_e(w.get("vid"))}">'
            f'<div class="pic"><img src="{_pic(w.get("pic"))}" loading="lazy" alt="">'
            f'<span class="badge">{_e(w.get("remark"))}</span></div>'
            f'<h3>{_e(w.get("name"))}</h3>'
            f'<div class="sub">{_e(w.get("year"))}'
            + (f' · {_e(w.get("role"))}' if w.get("role") else "")
            + "</div></a>"
        )
    works_html = ""
    if cards:
        works_html = ('<section id="workSec"><div class="sect">作品 '
                      f'（{len(cards)}）</div><div class="wall" id="wallbox">'
                      + "".join(cards) + "</div></section>")

    body = (
        f"<div class=\"brand\"><b>影人</b><span>{_src_name(src)}</span></div>"
        f"<div class=\"tabs\"><a class=\"fbtn\" href=\"{back}\">← 返回</a></div>"
        "<div class=\"phead\">"
        f"<div class=\"pav\">{face}</div>"
        "<div class=\"pinfo\">"
        f"<h1>{_e(name)}</h1>"
        + (f"<div class=\"platin\">{_e(latin)}</div>" if latin else "")
        + (f"<div class=\"pintro\">{_e(intro)}</div>" if intro else "")
        + (f"<div class=\"pinfo2\">{info_html}</div>" if info_html else "")
        + "</div></div>"
        + works_html
        + ("" if cards else "<div class=\"empty\">这位影人暂时没有作品数据。</div>")
    )
    return _page(f"{name} · 影人", body, _JS_WALL, src, back, sm)


# ---------------------------------------------------------------------------
# 搜索结果页 —— 详情页「🔍 搜索」进来（自绘，不跳 app 原生搜索）
# ---------------------------------------------------------------------------

def page_search(kw: str, sm: bool = False) -> str:
    """两个数据源一起搜，分两段列出。

    为什么自己做：原来「🔍 搜索」直接 `haiplay://search` 调 app 的原生搜索页
    —— 白底列表、字体不能调、还只有站源结果。海哥要求「所有流程不跳原生
    页面 + 全面美化」，所以这里把豆瓣和 TMDB 的结果都画成自家的海报卡。
    """
    kw = str(kw or "").strip()
    src = load_src()
    back = "/hai/poster/?src=" + src
    groups = []
    if kw:
        for s, label in (("douban", "豆瓣"), ("tmdb", "TMDB")):
            try:
                if s == "douban":
                    from . import doubanx as D
                    rows = D.search(kw, 12)
                else:
                    rows = (T.search(kw).get("list") or [])[:12]
            except Exception:
                rows = []
            if rows:
                cards = []
                for r in rows:
                    vid = r.get("vid") or r.get("vod_id")
                    cards.append(
                        f'<a class="card" href="/hai/poster/d/{_e(vid)}">'
                        f'<div class="pic"><img src="{_pic(r.get("pic") or r.get("vod_pic"))}" '
                        f'loading="lazy" alt="">'
                        f'<span class="badge">{_e(r.get("remark") or r.get("vod_remarks"))}</span>'
                        "</div>"
                        f'<h3>{_e(r.get("name") or r.get("vod_name"))}</h3>'
                        f'<div class="sub">{_e(r.get("year") or r.get("vod_year"))}</div></a>'
                    )
                groups.append(f'<div class="sect">{label}</div>'
                              '<div class="wall">' + "".join(cards) + "</div>")

    body = (
        f"<div class=\"brand\"><b>搜索</b><span>{_e(kw) or '没有关键词'}</span></div>"
        f"<div class=\"tabs\"><a class=\"fbtn\" href=\"{back}\">← 返回</a></div>"
        + ("".join(groups) if groups else
           "<div class=\"empty\">两个数据源都没搜到。<br><br>"
           "换个更短的关键词试试（比如只留前两个字），或者回海报墙按类目找。</div>")
    )
    return _page(f"搜索 {kw} · 海报墙", body, _JS_WALL, src, back, sm)
