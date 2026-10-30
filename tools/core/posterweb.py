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


def _e(s) -> str:
    return html.escape(str(s or ""))


def _pic(u) -> str:
    """图片地址改成站内相对路径，走中枢 `/hai/img` 代理。

    两个原因：电视/手机直连 images.tmdb.org 慢且容易裂图；网页可能从局域网
    或公网任意入口打开，绝对地址在另一端就挂。相对路径交给浏览器按当前入口补全。
    """
    s = str(u or "")
    s = re.sub(r"^https?://images\.tmdb\.org/t/p/", "/hai/img/t/p/", s)
    s = re.sub(r"^https?://[^/]+(/hai/img/.*)$", r"\1", s)
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
    s = re.sub(r"^https?://[^/]+(/hai/img/.*)$", r"\1", s)
    return s

def _page(title: str, body: str, script: str = "") -> str:
    return (
        "<!DOCTYPE html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
        f"<title>{_e(title)}</title><style>{_CSS}</style></head><body>"
        f"<div class=\"wrap\">{body}"
        "<div class=\"foot\">TMDB 海报墙 · hai 本地包中枢自托管</div></div>"
        + (f"<script>{script}</script>" if script else "")
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


def page_home(type_id: int = 1, page: int = 1) -> str:
    data = T.list_page(type_id, page)
    items = data.get("list") or []

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

    tabs = "".join(
        f"<a class=\"{'on' if tid == type_id else ''}\" "
        f"href=\"/hai/poster/?t={tid}\">{_e(name)}</a>"
        for tid, name, _, _ in T.CLASSES
    )
    cards = "".join(_card(x) for x in items[1:])
    if not items:
        # 「我的追剧」刚装好是空的，给一句人话，别让人以为页面坏了
        cards = ("<div class=\"empty\">这一类还没有内容。"
                 + ("去别的类目点开一部片，详情页点「一键追剧」就会出现在这里。</div>"
                    if type_id == T.WATCH_TYPE else "换个类目看看。</div>"))
    body = (
        "<div class=\"brand\"><b>海报墙</b><span>TMDB · 深色影院</span></div>"
        f"{hero}<div class=\"tabs\">{tabs}</div><div class=\"wall\">{cards}</div>"
    )
    return _page("海报墙 · TMDB", body)


def page_detail(vid: str) -> str:
    # 只拿元数据：TMDB 侧有长缓存，基本上就是本地字典查找。
    # 这里**绝不能**用 with_plays=True —— 那样会同步跑一遍网盘搜索（花卷最慢要
    # 5 秒），详情页首屏从 0.2 秒直接变成 3 秒以上，实拍反馈过"打开要 3 秒"。
    d = T.detail(vid, with_plays=False)
    if not d:
        return _page("未找到", "<div class=\"brand\"><b>海报墙</b></div>"
                              "<p class=\"desc\">TMDB 没有这条数据。</p>")
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
        f"<div class=\"acts\">{play_btn}{search_btn}{watch_btn}</div>"
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
    # 「▶ 播放」直达玩偶：页面打开后后台解析 vod id，解析成功就换 href。
    # 故意不阻塞首屏 —— 解析要 1~2 秒（wogg 搜索页），失败就保持搜索兜底。
    play_js = (
        "(function(){var b=document.getElementById('btnPlay');if(!b)return;"
        "var x=new XMLHttpRequest();x.open('GET','/hai/poster/play?vid="
        + _e(vid) + "',true);x.onreadystatechange=function(){"
        "if(x.readyState!==4)return;try{var r=JSON.parse(x.responseText);"
        "if(r&&r.ok&&r.id){b.href='haiplay://play?site='+encodeURIComponent(r.site_key)"
        "+'&id='+encodeURIComponent(r.id)+'&name='+encodeURIComponent(r.name||'')"
        "+'&pic='+encodeURIComponent(r.pic||'');}}catch(e){}};x.send();})();"
    )
    return _page(f"{d.get('vod_name')} · 海报墙", body, _JS_NAV + hero_js + play_js)
