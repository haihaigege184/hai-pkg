(function(){
  var vh = function () { return window.innerHeight; };
  var btns = [], epEls = [], castEls = [], recEls = [];
  // 四态：hero（按钮） → ep（选集） → cast（演职员） → rec（相关推荐）
  // 2026-11-01 插入 ep：海哥要「播放前先选集」，所以 ↓ 的第一站是选集，
  // 没有集数时自动跳过（avail/step 会绕过空态）。
  var ORDER = ['hero', 'ep', 'cast', 'rec'];
  var state = 'hero';
  var grid = [], row = 0, col = 0;
  var epRow = 0, epCol = 0;   // 选集区最后停留位置（从演职员移回来时回位用）
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
    // 2026-11-05：离开选集区前记下当前光标位置。从演职员/推荐移回选集时
    // 回到原来那一集，不再跳到第一集（海哥实测反馈）。
    if (state === 'ep') { epRow = row; epCol = col; }
    state = st;
    if (st === 'hero') {
      grid = [btns]; row = 0; col = 0;
      titleCard('hide');
      document.body ? document.body.classList.remove('below') : 0;
      smoothTo(0);
    } else if (st === 'ep') {
      grid = makeGrid(epEls);
      // 回位：恢复上次选集光标（clamp 到当前 grid 范围，避免越界）
      row = Math.min(Math.max(0, epRow), Math.max(0, grid.length - 1));
      col = Math.min(Math.max(0, epCol), Math.max(0, (grid[row] || []).length - 1));
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