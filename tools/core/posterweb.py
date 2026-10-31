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
    """统一成 [(cid(str), 名称)]，两套数据的类目表形状不同，这里抹平。"""
    if src == "douban":
        from . import doubanx as D
        return [(str(a), b) for a, b in D.classes()]
    return [(str(c[0]), c[1]) for c in T.CLASSES]


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
.brand b{font-size:26px;letter-spacing:3px;color:#00e08a;
 text-shadow:0 0 12px rgba(0,224,138,.75),0 0 34px rgba(0,224,138,.35)}
.brand span{margin-left:14px;font-size:13px;color:#7d8aa3;letter-spacing:1px}

/* 满屏背景大图（固定层，双层轮播见 #heroFx；内容在其上滚动） */
#herobgshade{position:fixed;top:0;left:0;width:100vw;height:100vh;z-index:-1;pointer-events:none;
 background:linear-gradient(180deg,rgba(7,10,16,.50) 0%,rgba(7,10,16,.74) 52%,
            rgba(7,10,16,.95) 100%)}
/* 顶部信息区：透明容器，叠在满屏背景之上，标题/按钮位置不变。
   2026-10-30 海哥：整体向上提（bottom 40→150）—— 二屏演职员上移后
   左上不再露出播放按钮下沿；进演职员/推荐态再把整块淡出（双保险）。 */
.hero{position:relative;min-height:100vh;margin-bottom:26px}
.hero .info{position:absolute;left:38px;bottom:150px;right:44%;transition:opacity .45s ease}
body.below .hero .info{opacity:0;pointer-events:none}
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
.tabs{margin:0 0 14px}
.tabs a{display:inline-block;margin:0 10px 10px 0;padding:8px 16px;border-radius:10px;
 font-size:14px;color:#93a1b8;background:rgba(255,255,255,.035);
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
.fsum{font-size:12px;color:#6ea88f;letter-spacing:1px;margin-left:2px}
.fsum b{color:#00e08a;font-weight:600}

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
.back{display:inline-block;margin-bottom:18px;font-size:14px;color:#7d8aa3}
.foot{margin-top:38px;font-size:12px;color:#4d5769;letter-spacing:1px}

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
.empty{padding:40px 0;font-size:15px;color:#6f7c93}
.tip{margin-top:10px;font-size:12px;color:#5d6879;line-height:1.8}

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
#titleCard p{font-size:12.5px;line-height:1.7;color:#aeb9cc;margin:0;
 max-height:5.4em;overflow:hidden;display:-webkit-box;
 -webkit-line-clamp:3;-webkit-box-orient:vertical}
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
  var btns = [], castEls = [], recEls = [];
  var state = 'hero';            // hero | cast | rec
  var grid = [];                 // 当前态的行 × 列元素表
  var row = 0, col = 0;
  var recRowsShown = 1;          // 推荐区当前展开的行数
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
  function hi() {
    var cur = grid[row] && grid[row][col];
    var pool = state === 'hero' ? btns : (state === 'cast' ? castEls : recEls);
    for (var i = 0; i < pool.length; i++) { pool[i].classList.remove('sel'); }
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
  function recRowApply() {
    // recEls 全集按 offsetTop 分行，rowhide 只显示前 recRowsShown 行
    var full = makeGrid(recEls);
    var showTop = full.length && full[0].length ? full[0][0].offsetTop : -1;
    for (var i = 0; i < recEls.length; i++) {
      var el = recEls[i];
      if (showTop < 0) { continue; }
      var rIdx = -1;
      for (var r = 0; r < full.length; r++) {
        if (full[r].indexOf(el) >= 0) { rIdx = r; break; }
      }
      if (rIdx >= recRowsShown) { el.classList.add('rowhide'); }
      else { el.classList.remove('rowhide'); }
    }
    return full;
  }

  function enter(stateName) {
    state = stateName;
    if (state === 'cast' && !castEls.length && recEls.length) { state = 'rec'; }
    if (state === 'hero') {
      grid = [btns]; row = 0; col = 0;
      titleCard('hide');
      document.body ? document.body.classList.remove('below') : 0;
      smoothTo(0);
    } else if (state === 'cast') {
      grid = makeGrid(castEls); row = 0; col = 0;
      titleCard('cast');
      document.body ? document.body.classList.add('below') : 0;
      smoothTo(secTop('castSec'));
    } else {
      recRowsShown = 1;
      makeGrid(recEls);          // 先排版再按行裁
      recRowApply();
      grid = makeGrid(recEls); row = 0; col = 0;
      titleCard('rec');
      document.body ? document.body.classList.add('below') : 0;
      smoothTo(secTop('recSec'));
    }
    hi();
  }

  function move(dr, dc) {
    if (state === 'hero') {
      // ↓ = 进演职员态（海哥交互稿：按一下下键，演职员一步上移到下 2/3）
      if (dr > 0 && (castEls.length || recEls.length)) { enter('cast'); return; }
      if (!btns.length) { return; }
      col = (col + dc + btns.length) % btns.length;
      hi(); return;
    }
    var nr = row + dr, nc = col + dc;
    if (nr >= grid.length) {
      if (state === 'cast' && recEls.length) { enter('rec'); return; }
      if (state === 'rec' && dr > 0 && recRowsShown < makeGrid(recEls).length) {
        recRowsShown += 1;
        grid = recRowApply();
        row = grid.length - 1;
        hi(); ensureVisible(grid[row][col]);
        return;
      }
      nr = grid.length - 1;
    }
    if (nr < 0) {
      if (state === 'rec') { enter('cast'); return; }
      if (state === 'cast') { enter('hero'); return; }
      nr = 0;
    }
    row = nr;
    var r = grid[row];
    col = Math.max(0, Math.min(col + dc, r.length - 1));
    hi(); ensureVisible(grid[row][col]);
  }

  function init() {
    btns = all('.acts a');
    castEls = all('#castSec .cast a');
    recEls = all('#recSec .wall a.card');
    enter('hero');
  }

  document.addEventListener('keydown', function (e) {
    var k = e.keyCode || e.which;
    if (k === 37) { move(0, -1); e.preventDefault(); }
    else if (k === 39) { move(0, 1); e.preventDefault(); }
    else if (k === 38) { move(-1, 0); e.preventDefault(); }
    else if (k === 40) { move(1, 0); e.preventDefault(); }
    else if (k === 13 || k === 23) {
      var pool = state === 'hero' ? btns : (state === 'cast' ? castEls : recEls);
      var b = grid[row] && grid[row][col];
      if (b && b.getAttribute('href')) { window.location.href = b.getAttribute('href'); }
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
    s = re.sub(r"^https?://[a-z0-9]*\.doubanio\.com/(view/photo/.+)$", r"/hai/dbimg/\1", s)
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
    s = re.sub(r"^https?://[a-z0-9]*\.doubanio\.com/(view/photo/.+)$", r"/hai/dbimg/\1", s)
    s = re.sub(r"^https?://[^/]+(/hai/(?:db)?img/.*)$", r"\1", s)
    return s

_JS_SELFHEAL = """
(function(){
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

  document.addEventListener('keydown', function (e) {
    var k = e.keyCode || e.which;
    // 设置 / 菜单键
    if (k === 82 || k === 176 || k === 93) {
      e.preventDefault();
      if (isOpen()) { close(); } else { open(); }
      return;
    }
    if (!isOpen()) { return; }
    // 菜单打开时把方向/OK 全吃掉，别让底下的 _JS_NAV 同时挪焦点
    e.stopPropagation();
    e.preventDefault();
    if (k === 21 || k === 19) {                 // ← / ↑
      sel = (sel + opts.length - 1) % opts.length; paint();
    } else if (k === 22 || k === 20) {          // → / ↓
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
        + '</div><div class="srchint">← → 选择　OK 确认　「✕ 关闭」退出'
          '（返回键会直接退出详情页）</div>'
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
        f"<title>{_e(title)}</title><style>{_CSS}</style></head><body>"
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
  function go(dr, dc) {
    if (!grid.length) { return; }
    var nr = Math.min(Math.max(0, row + dr), grid.length - 1);
    var c = (dr === 0) ? col + dc : Math.min(col, grid[nr].length - 1);
    c = Math.min(Math.max(0, c), grid[nr].length - 1);
    row = nr; col = c;
    hi(); ensure(grid[row][col]);
  }
  function openItem() {
    var el = grid[row] && grid[row][col];
    if (!el) { return; }
    var href = el.getAttribute('href');
    if (el.getAttribute('data-filter') === '1') { openPanel(); return; }
    if (href) { location.href = href; }
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
    if (href && href !== '#') { location.href = href; }
  }
  function openPanel() {
    if (!panel) { return; }
    panel.classList.add('show');
    if (!frows.length) { initPanel(); }
    fr = 0; fc = fsel[0] || 0;
    fHi();
  }
  function closePanel() {
    if (!panel) { return; }
    panel.classList.remove('show');
    hi();
  }

  build(); hi();

  document.addEventListener('keydown', function (e) {
    var k = e.keyCode;
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
    if (k === 38) { go(-1, 0); }
    else if (k === 40) { go(1, 0); }
    else if (k === 37) { go(0, -1); }
    else if (k === 39) { go(0, 1); }
    else if (k === 13 || k === 23) { openItem(); }
    else if (k === 82) { openPanel(); }        // MENU 键也能呼出
    else { return; }
    e.preventDefault();
    return false;
  }, false);

  // 触屏/鼠标也能用：点分类条上的「筛选」或当前类目
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
                     src if src != "tmdb" else "")
            on = " sel" if val == cur else ""
            opts.append(f"<a class=\"fopt{on}\" href=\"{_e(url)}\">{_e(name)}</a>")
        return (f"<div class=\"frow\"><div class=\"fl\">{label}</div>"
                f"<div class=\"fopts\">{''.join(opts)}</div></div>")

    close = ("<div class=\"frow\"><div class=\"fl\">操作</div><div class=\"fopts\">"
             f"<a class=\"fopt\" href=\"#\">✕ 关闭</a></div></div>")
    return (
        "<div id=\"fpanel\"><div class=\"fbox\">"
        "<div class=\"fh\">筛选<i>选完立刻按条件重排，返回键←关闭</i></div>"
        + row("地区 / 国家", ctry, country, "c")
        + row("排序", sorts, sort, "s")
        + row("影片类型", list(gens), genre, "g")
        + close
        + "<div class=\"fk\">← → 选条件 · ↑ ↓ 换一组 · OK 应用 · 返回 关闭</div>"
        "</div></div>"
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


def _home_douban(cid: str, page: int, country: str, sort: str,
                 genre: str, sm: bool = False) -> str:
    """豆瓣版海报墙。结构与 TMDB 版一致，只换数据来源与类目表。"""
    from . import doubanx as D
    data = D.list_page(cid, page, country, sort, genre)
    items = data.get("list") or []
    fsum = _fsum((D.COUNTRIES, D.SORTS, D.GENRES), country, sort, genre)

    hero = ""
    if items:
        top = items[0]
        d = D.detail(top.get("vod_id") or "") or {}
        bg = _pic(d.get("vod_pic_slide") or top.get("vod_pic") or "")
        desc = str(d.get("vod_content") or "")[:170]
        chips = []
        if top.get("vod_remarks"):
            chips.append(f"<span class=\"chip score\">{_e(top['vod_remarks'])}</span>")
        for v in (d.get("vod_year"), d.get("vod_area")):
            if v:
                chips.append(f"<span class=\"chip\">{_e(v)}</span>")
        if d.get("type_name"):
            chips.append(f"<span class=\"chip p\">{_e(d['type_name'])}</span>")
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

    here = _q(cid, page, country, sort, genre, "douban")
    tabs = ("<a class=\"fbtn\" id=\"fbtn\" data-filter=\"1\" href=\"#\">⚙ 筛选</a>"
            + _srcbtn("douban", here))
    for c2, name in D.classes():
        on = " on" if c2 == cid else ""
        attr = " data-filter=\"1\"" if c2 == cid else ""
        tabs += (f"<a class=\"{on.strip()}\"{attr} "
                 f"href=\"{_e(_q(c2, 1, country, sort, genre, 'douban'))}\">{_e(name)}</a>")
    cards = "".join(_card(x) for x in items[1:])
    if not items:
        cards = ("<div class=\"empty\">豆瓣这会儿没返回数据（偶发限流），"
                 "等几秒按返回再进一次就好。</div>")
    summary = f"<span class=\"fsum\">筛选：<b>{_e(fsum)}</b></span>" if fsum else ""
    body = (
        f"<div class=\"brand\"><b>海报墙</b><span>豆瓣 · 国产剧更新更快</span>{summary}</div>"
        f"{hero}<div class=\"tabs\">{tabs}</div>"
        f"<div class=\"wall\" id=\"wallbox\">{cards}</div>"
        + ("" if not items else _filter_panel(cid, page, country, sort, genre, "douban"))
    )
    return _page("海报墙 · 豆瓣", body, _JS_WALL, "douban", here, sm)


def page_home(type_id=1, page: int = 1, country: str = "",
              sort: str = "", genre: str = "", src: str = "tmdb",
              sm: bool = False) -> str:
    src = src if src in _SRC_ALL else load_src()
    if src == "douban":
        cid = str(type_id)
        from . import doubanx as D
        if cid not in (c for c, _ in D.classes()):
            cid = D.classes()[0][0]
        return _home_douban(cid, page, country, sort, genre, sm)

    try:
        tid = int(type_id)
    except (TypeError, ValueError):
        tid = 2
    data = T.list_page(tid, page, country, sort, genre)
    items = data.get("list") or []
    fsum = _fsum((T.COUNTRIES, T.SORTS, T.GENRES), country, sort, genre)

    hero = ""
    if items:
        top = items[0]
        d = T.detail(top.get("vod_id"), with_plays=False) if top.get("vod_id") else {}
        bg = _pic((d or {}).get("vod_pic_slide") or top.get("vod_pic") or "")
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

    # 类目条：第一个是「⚙ 筛选」；当前类目本身也是筛选入口（海哥：在热门剧集上按 OK 呼出）
    here = _q(tid, page, country, sort, genre, "")
    tabs = (f"<a class=\"fbtn\" id=\"fbtn\" data-filter=\"1\" href=\"#\">⚙ 筛选</a>"
            + _srcbtn("tmdb", here))
    for cid, name in _classes("tmdb"):
        on = " on" if cid == str(tid) else ""
        attr = " data-filter=\"1\"" if cid == str(tid) else ""
        tabs += (f"<a class=\"{on.strip()}\"{attr} "
                 f"href=\"{_e(_q(cid, 1, country, sort, genre))}\">{_e(name)}</a>")
    cards = "".join(_card(x) for x in items[1:])
    if not items:
        # 「我的追剧」刚装好是空的，给一句人话，别让人以为页面坏了
        cards = ("<div class=\"empty\">这一类还没有内容。"
                 + ("去别的类目点开一部片，详情页点「一键追剧」就会出现在这里。</div>"
                    if tid == T.WATCH_TYPE else "换个类目或放宽筛选看看。</div>"))
    summary = f"<span class=\"fsum\">筛选：<b>{_e(fsum)}</b></span>" if fsum else ""
    body = (
        f"<div class=\"brand\"><b>海报墙</b><span>TMDB · 深色影院</span>{summary}</div>"
        f"{hero}<div class=\"tabs\">{tabs}</div>"
        f"<div class=\"wall\" id=\"wallbox\">{cards}</div>"
        + ("" if tid == T.WATCH_TYPE or not items else _filter_panel(
            tid, page, country, sort, genre, "tmdb"))
    )
    return _page("海报墙 · TMDB", body, _JS_WALL, "tmdb", here, sm)


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

    kv = []
    if d.get("vod_director"):
        kv.append(f"<div><b>导演</b>{_e(d['vod_director'])}</div>")
    if d.get("vod_actor"):
        kv.append(f"<div><b>主演</b>{_e(d['vod_actor'])}</div>")
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

    # 播放 / 搜索分流（2026-10-30 海哥定稿）：
    #   ▶ 播放 —— 打开页面后 JS 后台调 /hai/poster/play 解析玩偶 vod id，
    #     成功就把 href 换成 haiplay://play?site=Wogg&id=/voddetail/n.html…
    #     （app 拦截后直达玩偶选集页，OK 即播）；解析失败保持搜索兜底。
    #   🔍 搜索 —— 始终 haiplay://search?q= 聚合搜索（端上前 3 站源已
    #     searchable=0，玩偶结果第一顺位）。
    q = urllib.parse.quote(d.get("vod_name") or "")
    play_btn = (f"<a class=\"btn\" id=\"btnPlay\" "
                f"href=\"haiplay://search?q={q}\">▶ 播放</a>")
    search_btn = f"<a class=\"btn ghost\" href=\"haiplay://search?q={q}\">🔍 搜索</a>"
    if watched:
        watch_btn = (f"<a class=\"btn on\" href=\"/hai/poster/watch?act=del&vid={_e(vid)}"
                     f"&back=/hai/poster/d/{_e(vid)}\">✓ 已追剧</a>")
    else:
        watch_btn = (f"<a class=\"btn ghost\" href=\"/hai/poster/watch?act=add&vid={_e(vid)}"
                     f"&back=/hai/poster/d/{_e(vid)}\">＋ 一键追剧</a>")

    # 「⚙ 数据源」放进按钮行：遥控 ←→ 就能选到，OK 呼出浮层（见 _srcdlg 注释）
    src_btn = _srcbtn(src, f"/hai/poster/d/{_e(vid)}", cls="btn ghost")

    play_block = (
        "<div class=\"sect\">播放</div>"
        + "<div class=\"line\" style=\"border-style:dashed\">▶ 播放：直达玩偶哥哥选集页"
          "（自动按片名匹配）　🔍 搜索：聚合搜索（玩偶第一顺位）</div>"
        + "<div class=\"tip\">点「▶ 播放」直接进玩偶选集页，OK 即播，"
          "网盘转存/设置/弹幕全由 PG 包接管。"
          "遥控器：← → 选按钮、↓ 演职员、OK 确认、返回键回上层。</div>"
    )

    # ---- 演职员 ---------------------------------------------------------
    cast_full = d.get("vod_cast") or []
    crew_full = d.get("vod_crew") or []
    cast_html = ""
    if cast_full:
        items = []
        for c in cast_full:
            name = c.get("name") or ""
            # 点击头像 = 在 App 里搜该演员（活链接）
            aq = urllib.parse.quote(name)
            pic = _pic(c.get("profile"))
            if pic:
                face = f'<div class="cface"><img src="{pic}" alt=""></div>'
            else:
                face = f'<div class="cface noimg">{_e((name or "?")[:1])}</div>'
            items.append(
                f'<a class="citem" href="haiplay://search?q={aq}">{face}'
                f'<div class="cname">{_e(name)}</div>'
                f'<div class="crole">{_e(c.get("character"))}</div></a>'
            )
        cast_html = ('<div class="sect">演职员</div><div class="cast">'
                     + "".join(items) + "</div>")
    crew_html = ""
    if crew_full:
        cis = []
        for c in crew_full:
            job = {"Director": "导演", "Writer": "编剧", "Screenplay": "编剧",
                   "Original Music Composer": "配乐"}.get(c.get("job"), c.get("job"))
            cis.append(f'<span class="ci"><b>{_e(job)}</b>{_e(c.get("name"))}</span>')
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

    # 左上角片名动画条：剧名优先 TMDB 官方 logo（美化设计），简介小字垫底。
    # JS 会在进演职员/推荐态时按需填充文案，这里只放数据和 logo。
    logo = _pic(d.get("vod_logo") or "")
    tc_logo = (f'<img class="tlogo" src="{logo}" alt="">' if logo else "")
    tc_desc = str(d.get("vod_content") or "").split("\n\n")[0][:90]

    body = (
        # fxA 必须走 _pic() 代理 —— 电视直连 images.tmdb.org 时通时断，
        # 直连失败整个背景黑屏（2026-10-30 真机实锤：诛仙首开全黑）。
        f"<div id=\"heroFx\"><img id=\"fxA\" src=\"{_pic(d.get('vod_pic'))}\" "
        "style=\"opacity:1\" alt=\"\"><img id=\"fxB\" alt=\"\"></div>"
        "<div id=\"herobgshade\"></div>"
        f"<div id=\"titleCard\" data-name=\"{_e(d.get('vod_name'))}\" "
        f"data-desc=\"{_e(tc_desc)}\" data-recn=\"{len(recs)}\">"
        "<div class=\"tcwrap\">"
        f"{tc_logo}<h2>{_e(d.get('vod_name'))}</h2><p id=\"tcSub\"></p>"
        "</div></div>"
        f"<a class=\"back\" href=\"/hai/poster/\">← 返回海报墙</a>"
        "<div class=\"hero\">"
        "<div class=\"info\">"
        f"<h1>{_e(d.get('vod_name'))}</h1>"
        f"<div class=\"chips\">{''.join(chips)}</div>"
        f"<div class=\"acts\">{play_btn}{search_btn}{watch_btn}{src_btn}</div>"
        f"<div class=\"keys\">← → 选按钮　↓ 演职员　OK 确认</div>"
        "</div></div>"
        # 旧简介块去掉（2026-10-30 海哥）：二屏左上已有特效剧名+简介卡，重复了
        f"<div class=\"dmeta\"><div class=\"kv\">{''.join(kv)}</div></div>"
        + (f"<section id=\"castSec\">{cast_html}{crew_html}</section>"
           if (cast_html or crew_html) else "")
        + play_block
        + (f"<section id=\"recSec\">{rec_html}</section>" if rec_html else "")
    )
    hero_js = (_JS_HERO
               .replace("%IMGS%", json.dumps(hero_imgs, ensure_ascii=False))
               .replace("%INTERVAL%", str(cfg["interval"]))
               .replace("%EFFECT%", cfg["effect"]))
    # 「▶ 播放」直达站源：页面打开后后台并行搜全部站源（玩偶打头），
    # 找到**完全同名**的资源就换 href 并把站名写到按钮上，用户一眼知道
    # 会从哪个站起播；一站都没匹配到才保持「聚合搜索」兜底。
    play_js = (
        "(function(){var b=document.getElementById('btnPlay');if(!b)return;"
        "var x=new XMLHttpRequest();x.open('GET','/hai/poster/play?vid="
        + _e(vid) + "',true);x.onreadystatechange=function(){"
        "if(x.readyState!==4)return;try{var r=JSON.parse(x.responseText);"
        "if(r&&r.ok&&r.id){b.href='haiplay://play?site='+encodeURIComponent(r.site_key)"
        "+'&id='+encodeURIComponent(r.id)+'&name='+encodeURIComponent(r.name||'')"
        "+'&pic='+encodeURIComponent(r.pic||'');"
        "if(r.site){b.innerHTML='▶ 播放 · '+r.site;}}"
        "else if(b.innerText.indexOf('播放')>=0){b.innerHTML='▶ 播放(搜索)';}}"
        "catch(e){}};x.send();})();"
    )
    return _page(f"{d.get('vod_name')} · 海报墙", body,
                 _JS_NAV + hero_js + play_js, src,
                 f"/hai/poster/d/{_e(vid)}", sm)
