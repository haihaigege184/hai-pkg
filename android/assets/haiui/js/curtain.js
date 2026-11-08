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