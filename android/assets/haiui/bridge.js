/* =====================================================================
 * haiui/bridge.js —— APK 内置 HTML 的「零改动适配层」
 *
 * 目标：core/posterweb.py 生成的 UI 里所有接口都是相对路径 /hai/*，
 *       且用 XMLHttpRequest 同步/异步取数据。搬进 APK assets 后，
 *       file:// 下的 /hai/* 会指向文件系统根，必然全挂。
 *
 * 做法：不改动那 2886 行 UI 代码，只在这里把 XHR 的 /hai/* 前缀
 *       重写到 jar 提供的本地 HTTP 服务（与服务端同形路由）。
 *       APK 侧只需在 WebView 注入 window.HAI_BASE，例如：
 *         window.HAI_BASE = "http://127.0.0.1:10079";
 *
 * 这样「UI 在 APK、数据在 jar、服务器零渲染」三层就解耦了：
 *   - 换 UI 只换 assets（重打包 APK）
 *   - 换取流/数据源逻辑只下发新 jar（热更新，不用重打包）
 *   - 服务器只负责 jsm.json / lib / pg.jar 的下发与更新检查
 *
 * 兼容：若 HAI_BASE 未注入（PC 浏览器预览），自动回落到
 *       window.HAI_MOCK 提供的本地假数据，页面骨架仍可离线渲染。
 * ===================================================================== */
(function (global) {
  'use strict';

  var BASE = String(global.HAI_BASE || '').replace(/\/+$/, '');

  /** 统一取数入口；UI 层后续新增接口也走这里，避免散落 */
  function api(path, cb, opt) {
    opt = opt || {};
    var url = path.charAt(0) === '/' ? (BASE + path) : path;
    var x = new XMLHttpRequest();
    x.open(opt.method || 'GET', url, true);
    if (opt.timeout) { x.timeout = opt.timeout; }
    x.onreadystatechange = function () {
      if (x.readyState !== 4) { return; }
      var data = null;
      try { data = JSON.parse(x.responseText); } catch (e) { data = null; }
      if (x.status >= 200 && x.status < 300 && data) { cb(data, null); }
      else {
        var mock = mockOf(path, data, x.status);
        if (mock) { cb(mock, null); } else { cb(null, x.status); }
      }
    };
    x.send(opt.body || null);
    return x;
  }

  /** PC 预览兜底：没有 jar 本地服务时用内置假数据，保证骨架能画出来 */
  function mockOf(path, data, status) {
    var M = global.HAI_MOCK;
    if (!M) { return null; }
    if (typeof M === 'function') { return M(path, data, status); }
    if (path.indexOf('/hai/poster/eps') === 0) { return M.eps || null; }
    if (path.indexOf('/hai/poster/play') === 0) { return M.play || null; }
    if (path.indexOf('/hai/poster/wall') === 0 ||
        path.indexOf('/hai/poster/') === 0) { return M.wall || M.home || null; }
    return null;
  }

  /* ---- 关键：接管 XMLHttpRequest，把 /hai/* 重写到本地服务 ---- */
  var XHR = global.XMLHttpRequest;
  if (XHR && XHR.prototype && !XHR.prototype.__haiPatched) {
    var origOpen = XHR.prototype.open;
    XHR.prototype.open = function (method, url) {
      if (typeof url === 'string' && url.charAt(0) === '/' && BASE) {
        arguments[1] = BASE + url;
      }
      return origOpen.apply(this, arguments);
    };
    XHR.prototype.__haiPatched = true;
  }

  /* ---- 图片：/hai/dbimg/ 与 /hai/img/t/p/ 同样重写到 jar 代理 ---- */
  function pic(u) {
    if (!u) { return ''; }
    if (u.charAt(0) === '/' && BASE) { return BASE + u; }
    return u;
  }

  /* ---- 起播：交给 APK（haiplay:// 深链），或交给 jar 返回的地址 ---- */
  function play(href) {
    if (!href) { return false; }
    try { global.location.href = href; } catch (e) { return false; }
    return true;
  }

  global.HaiBridge = {
    api: api,
    pic: pic,
    play: play,
    get base() { return BASE; },
    /** APK 可在 WebView 就绪后调用，避免注入时机竞态 */
    setBase: function (b) { BASE = String(b || '').replace(/\/+$/, ''); }
  };
})(window);
