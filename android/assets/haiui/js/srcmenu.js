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
      if (/(^|\s)sel(\s|$)/.test(opts[i].className)) {
        opts[i].className = opts[i].className
          .replace(/(^|\s)sel(\s|$)/g, ' ').replace(/\s+/g, ' ');
      }
    }
    if (opts[sel]) { opts[sel].className += ' sel'; }
  }
  // 以 DOM 上的高亮为准（WebView 偶尔吞 keyCode，变量可能和画面不一致）
  function curIdx() {
    for (var i = 0; i < opts.length; i++) {
      if (/(^|\s)sel(\s|$)/.test(opts[i].className)) { return i; }
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