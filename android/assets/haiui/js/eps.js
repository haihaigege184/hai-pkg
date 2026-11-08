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
    var s = String(fn || '').replace(/\.(mp4|mkv|avi|ts|mov|flv|m3u8|wmv|rmvb)$/i, '');
    var tags = [], m;
    var re = /(2160p|1080p|720p|480p|4k|hdr|h265|h264|hevc|x265|x264|web-?dl|bluray)/ig;
    while ((m = re.exec(s))) {
      var t = m[1].toUpperCase();
      if (tags.indexOf(t) < 0) { tags.push(t); }
    }
    return tags.length ? tags.join(' · ') : s;
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
      return (/夸克/.test(a.pan) ? 0 : 1) - (/夸克/.test(b.pan) ? 0 : 1);
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
      try { window.__haiDbg('[hai] cands=' + cs.length + ' ep=' + w + '\n  '
        + cs.map(function (c) { return c.pan + ':' + String(c.push).slice(-14); }).join('\n  ')); } catch (e) {}
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
        var pn = String(srclist[k]).split('·').pop();
        if (pns.indexOf(pn) < 0) { pns.push(pn); }
      }
      // 有备份线路时才说线路数，否则「12 集 · 12 条线路」纯属废话
      if (stats.routes > stats.eps) {
        html += '　·　<b>' + stats.routes + '</b> 条线路';
      }
      if (pns.length) {
        html += ' <span class="esrc">' + esc(pns.join(' · ')) + '</span>';
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
    window.__haiRoute = r || null;   // P0 native：把用户选中的线路带下去
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