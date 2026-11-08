/* =====================================================================
 * haiui/inject.js —— 把服务端时代的「Python 注入占位符」搬到端上
 *
 * core/posterweb.py 里有 4 处运行时 replace：
 *   _JS_EPS  : %VID% / %TITLE%
 *   _JS_HERO : %EFFECT% / %IMGS% / %INTERVAL%
 *   _JS_CAST : %VID%
 * 抽成静态 .js 后这些占位符还在，直接 <script src> 会语法错误。
 * 这里在 WebView 里读同目录模板、替换后再注入，等价于原来的服务端注入，
 * 但完全不依赖服务器。
 *
 * APK 侧需给 WebView 打开 setAllowFileAccessFromFileURLs(true)；
 * 若 HTML 改由 jar 本地 HTTP 服务提供（HAI_BASE），则天然无跨域限制。
 * ===================================================================== */
(function (global) {
  'use strict';

  function param(k, dft) {
    var m = new RegExp('[?&]' + k + '=([^&]*)').exec(global.location.search);
    return m ? decodeURIComponent(m[1]) : dft;
  }

  var vid = param('vid', 'm535167');
  var title = param('name', '影片');
  var cfg = {
    '%VID%': vid,
    '%TITLE%': JSON.stringify(title),
    '%EFFECT%': 'random',
    '%IMGS%': '[]',
    '%INTERVAL%': '3'
  };

  function fill(src) {
    Object.keys(cfg).forEach(function (k) {
      src = src.split(k).join(cfg[k]);
    });
    return src;
  }

  function run(src) {
    var el = document.createElement('script');
    el.textContent = src;
    document.head.appendChild(el);
  }

  /* 顺序执行：hero → eps → cast → curtain → face，与原 _page() 的拼接顺序一致 */
  var FILES = ['js/hero.js', 'js/eps.js', 'js/cast.js',
               'js/curtain.js', 'js/face.js'];

  function load(i) {
    if (i >= FILES.length) { return; }
    var x = new XMLHttpRequest();
    x.open('GET', FILES[i], true);
    x.onload = function () {
      if (x.status === 200 || x.status === 0) {
        try { run(fill(x.responseText)); } catch (e) {
          console.warn('[haiui] inject fail', FILES[i], e);
        }
      }
      load(i + 1);
    };
    x.onerror = function () { load(i + 1); };
    x.send();
  }

  var started = false;
  function start() {
    if (started) { return; }
    started = true;
    load(0);
  }

  global.HAI_VID = vid;
  global.HAI_TITLE = title;
  global.HaiInject = { start: start, fill: fill };
  document.addEventListener('DOMContentLoaded', start);
  if (document.readyState !== 'loading') { start(); }
})(window);
