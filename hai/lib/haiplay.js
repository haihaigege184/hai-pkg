/* 海哥影视 —— 本地取链引擎（2026-11-02）
 *
 * 目标：详情页里算直链这件事**完全在电视本地做**，不碰服务器。
 * 依赖 APK 注入的 `hai` 对象（com.hai.HaiJs）：
 *   hai.ck(pan) -> cookie 串
 *   hai.http(method, url, headersJson, body) -> {code, body, headers}
 *   hai.dp(url, headersJson, size) -> token（HaiDp 拦截器读同一张表）
 *
 * 对外只暴露一个方法：
 *   await HaiPlay.ep({ share: 'https://pan.quark.cn/s/xxx', ep: 1, title: '' })
 *   -> { ok, url, header, size, name, mode }   // mode: origin / preview
 */
(function (global) {
  'use strict';

  var QK = {
    base: 'https://drive-pc.quark.cn/1/clouddrive/',
    q: '?pr=ucpro&fr=pc',
    ua: ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
         + '(KHTML, like Gecko) quark-cloud-drive/2.5.56 Chrome/100.0.4896.160 '
         + 'Electron/18.3.5.12 Safari/537.36 Channel/pckk_other_ch'),
    ref: 'https://pan.quark.cn/'
  };
  var UC = {
    base: 'https://pc-api.uc.cn/1/clouddrive/',
    q: '?pr=UCBrowser&fr=pc',
    // **必须是客户端 UA**（2026-11-04 修）：`file/download` 用浏览器 UA 恒 403，
    // 跟夸克一个道理。对齐服务端 core/panck.py 的 UC_UA。
    ua: ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
         + '(KHTML, like Gecko) Chrome/100.0.4896.160 '
         + 'Electron/18.3.5.12 Safari/537.36 Channel/pckk_other_ch'),
    ref: 'https://drive.uc.cn/',
    // UC 转存的 to_pdir_fid 必须是 "0"（写真实根 fid 报 token 校验异常）
    rootFid: '0'
  };
  var CHROME_UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      + '(KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36');
  var BD = {
    ua: ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
         + '(KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36'),
    ref: 'https://pan.baidu.com/',
    appId: '250528'
  };

  function hasBridge() {
    return typeof global.hai !== 'undefined' && global.hai
        && typeof global.hai.http === 'function';
  }

  function jparse(s) {
    try { return JSON.parse(s); } catch (e) { return null; }
  }

  /** 统一请求：返回 {code, body, json, headers} */
  async function req(method, url, headers, body) {
    var h = headers || {};
    // POST 必须显式带 Content-Type：夸克的 /share/sharepage/token 少了它
    // 会回 code != 0（表现为「分享失败或需要提取码」）。2026-11-02 实测。
    if (body && /post|put/i.test(method || '')) {
      var has = false;
      for (var k in h) {
        if (k.toLowerCase() === 'content-type') { has = true; break; }
      }
      if (!has) { h['Content-Type'] = 'application/x-www-form-urlencoded'; }
    }
    var r = JSON.parse(global.hai.http(method, url, JSON.stringify(h), body || ''));
    var j = jparse(r.body || '');
    return { code: r.code, body: r.body || '', json: j, headers: r.headers || {} };
  }

  function dlHeaders(cfg, ck) {
    return { 'User-Agent': cfg.ua, 'Referer': cfg.ref, 'Cookie': ck || '' };
  }

  function form(obj) {
    return Object.keys(obj).map(function (k) {
      return encodeURIComponent(k) + '=' + encodeURIComponent(obj[k] == null ? '' : obj[k]);
    }).join('&');
  }

  /** 夸克/UC 的开放接口**要 JSON body**，不是表单。
   *  2026-11-02 实测：发 x-www-form-urlencoded 会回 `500 code=15000 inner error`，
   *  和 Python 端 `_req`（json.dumps + application/json）对齐后立刻正常。 */
  function jbody(obj) {
    return JSON.stringify(obj);
  }

  /** 调 API 用的头：Chrome UA + Accept: application/json。
   *  注意**不能用**夸克客户端 UA —— 那个只给 file/download 用。 */
  function apiHeaders(cfg, ck) {
    return {
      'User-Agent': CHROME_UA,
      'Referer': cfg.ref,
      'Accept': 'application/json',
      'Content-Type': 'application/json',
      'Cookie': ck || ''
    };
  }

  // ---------------- 通用：从文件名猜集号 ----------------
  var RE_JUNK = /(\.(srt|ass|ssa|sub|idx|nfo|txt|jpg|png|zip|rar|7z|torrent)$)/i;
  var RE_EP = [
    /[Ss](\d{1,2})[Ee](\d{1,4})/,
    /第\s*(\d{1,4})\s*[集话話期]/,
    /(?:^|[^\d])(\d{1,4})\s*[集话話期]/,
    /\bE[Pp]?(\d{1,4})\b/,
    /[.\-_\s](\d{1,4})\.(?:mp4|mkv|ts|avi|rmvb|flv|m4v|webm)$/i
  ];

  function parseEp(name) {
    for (var i = 0; i < RE_EP.length; i++) {
      var m = name.match(RE_EP[i]);
      if (m) {
        var n = parseInt(m[m.length - 1], 10);
        if (n > 0 && n < 3000) return n;
      }
    }
    return 0;
  }

  function isJunk(name) {
    return !name || RE_JUNK.test(name);
  }

  function pickEp(files, ep, title) {
    var rows = files.filter(function (f) {
      var n = f.name || '';
      if (isJunk(n)) return false;
      f._ep = parseEp(n);
      return true;
    });
    if (!rows.length) return null;
    if (rows.length === 1) return rows[0];         // 单文件分享：就是它
    if (ep) {
      var hit = rows.filter(function (f) { return f._ep === ep; });
      if (hit.length) return hit[0];
      var mx = Math.max.apply(null, rows.map(function (f) { return f._ep || 0; }));
      if (ep > mx && rows.length <= 3) return rows[0];
      if (!mx) {
        var cand = rows;
        if (title) {
          var same = rows.filter(function (f) {
            return f.name.indexOf(title) >= 0 || title.indexOf(f.name) >= 0;
          });
          if (same.length) cand = same;
        }
        return cand[0];
      }
    }
    return rows[0];
  }

  // ================= 夸克 / UC（同一套 API）=================
  async function qkToken(cfg, ck, shareUrl) {
    var m = shareUrl.match(/\/s\/([0-9a-zA-Z]+)/);
    if (!m) return null;
    var pwd = (shareUrl.match(/[?&]pwd=([0-9a-zA-Z]+)/) || [])[1] || '';
    var r = await req('POST', cfg.base + 'share/sharepage/token' + cfg.q,
      apiHeaders(cfg, ck), jbody({ pwd_id: m[1], passcode: pwd }));
    if (!r.json || r.json.code !== 0) return null;
    return { sid: m[1], stoken: (r.json.data || {}).stoken || '', pwd: pwd };
  }

  async function qkList(cfg, ck, sid, stoken, pdir) {
    var q = '&pwd_id=' + sid + '&stoken=' + encodeURIComponent(stoken)
      + '&pdir_fid=' + (pdir || '0')
      + '&force=0&_page=1&_size=200&_fetch_total=1&_fetch_banner=0&_fetch_share=0'
      + '&_sort=file_type:asc,file_name:asc';
    var r = await req('GET', cfg.base + 'share/sharepage/detail' + cfg.q + q,
      dlHeaders(cfg, ck));
    return (r.json && r.json.data && r.json.data.list) || [];
  }

  /** 递归摊平（最多 3 层，网盘常见「剧名/季/文件」套娃） */
  async function qkTree(cfg, ck, sid, stoken, pdir, depth, out) {
    depth = depth || 0;
    out = out || [];
    var list = await qkList(cfg, ck, sid, stoken, pdir);
    for (var i = 0; i < list.length; i++) {
      var it = list[i];
      var name = it.file_name || it.server_filename || '';
      if (it.dir === true) {
        if (depth < 3) await qkTree(cfg, ck, sid, stoken, it.fid, depth + 1, out);
      } else {
        out.push({
          fid: it.fid, name: name, size: it.size || 0,
          token: it.share_fid_token || '', pdir: it.pdir_fid || '0'
        });
      }
    }
    return out;
  }

  /** 自己的根目录真实 fid（转存 to_pdir_fid 必须是它，写 "0" 报 token 校验异常） */
  async function qkRoot(cfg, ck) {
    var r = await req('GET', cfg.base + 'file/sort' + cfg.q
      + '&pdir_fid=0&_page=1&_size=1&_fetch_total=1', dlHeaders(cfg, ck));
    var list = (r.json && r.json.data && r.json.data.list) || [];
    return list.length ? (list[0].pdir_fid || '0') : '0';
  }

  async function qkSave(cfg, ck, sid, stoken, f) {
    // UC 的 to_pdir_fid 固定 "0"；夸克必须用自己根目录的真实 fid（写 "0" 报异常）
    var root = (cfg.rootFid === '0') ? '0' : await qkRoot(cfg, ck);
    var r = await req('POST', cfg.base + 'share/sharepage/save' + cfg.q,
      apiHeaders(cfg, ck),
      jbody({
        fid_list: [String(f.fid)],
        fid_token_list: [String(f.token)],
        to_pdir_fid: root, pwd_id: sid, stoken: stoken,
        pdir_fid: f.pdir || '0', scene: 'link'
      }));
    var tid = (r.json && r.json.data && r.json.data.task_id) || '';
    if (!tid) {
      return { ok: false, error: '转存失败: ' + ((r.json && r.json.message) || r.code) };
    }
    for (var i = 0; i < 24; i++) {
      await new Promise(function (s) { setTimeout(s, 500); });
      var t = await req('GET', cfg.base + 'task' + cfg.q + '&task_id=' + tid,
        dlHeaders(cfg, ck));
      var d = (t.json && t.json.data) || {};
      if (Number(d.status) === 2) {
        var ids = (d.save_as || {}).save_as_top_fids || [];
        return ids.length ? { ok: true, fid: String(ids[0]) } : { ok: false, error: '转存完成但无 fid' };
      }
      if (Number(d.status) === 3 || Number(d.status) === 4) {
        return { ok: false, error: '转存失败 status=' + d.status };
      }
    }
    return { ok: false, error: '转存超时' };
  }

  /** 探文件真实总长（字节）。
   *
   * 2026-11-04：UC 的 `file/download` 返回的 size 是**假的** ——
   * 「177 4K.mp4」报 15,340,287 字节（4K 片子不可能这么小）。
   * APK 的 HaiDp 拿这个 size 去算 Content-Range，多线程拉 15MB 就"读完"
   * ⇒ 播放器卡在启动画面，出不来画面。
   *
   * 办法：`Range: bytes=0-0` 读响应头 Content-Range 的 `/total`。
   * 必须走 `global.hai.http`（APK 的 OkHttp）而不是 XHR —— XHR 会被 CORS 挡
   * （夸克/UC 的 CDN 根本不返回 Access-Control-Allow-Origin）。
   */
  async function probeLen(url, header) {
    try {
      var h = {};
      for (var k in (header || {})) { h[k] = header[k]; }
      h['Range'] = 'bytes=0-0';
      var r = JSON.parse(global.hai.http('GET', url, JSON.stringify(h), ''));
      var hs = r.headers || {};
      var cr = hs['content-range'] || hs['Content-Range'] || '';
      var m = String(cr).match(/\/(\d+)\s*$/);
      if (m) { return parseInt(m[1], 10) || 0; }
      return parseInt(hs['content-length'] || hs['Content-Length'] || '0', 10) || 0;
    } catch (e) {
      return 0;
    }
  }

  async function qkLink(cfg, ck, fid) {
    // file/download **必须**用夸克 PC 客户端 UA（否则恒报 23018），body 也是 JSON
    var r = await req('POST', cfg.base + 'file/download' + cfg.q,
      { 'User-Agent': cfg.ua, 'Referer': cfg.ref,
        'Accept': 'application/json', 'Content-Type': 'application/json',
        'Cookie': ck || '' },
      jbody({ fids: [String(fid)] }));
    var d = r.json && r.json.data;
    var arr = Array.isArray(d) ? d : ((d && d.list) || []);
    if (!arr.length) {
      return { ok: false, error: '取链失败 ' + ((r.json && r.json.code) || r.code) };
    }
    var it = arr[0];
    if (!it.download_url) return { ok: false, error: '无 download_url' };
    var url = it.download_url;
    var hdr = { 'User-Agent': cfg.ua, 'Referer': cfg.ref, 'Cookie': ck || '' };
    // 真实总长必须探 —— 网盘报的 size 不可信（见 probeLen 注释）。
    var real = await probeLen(url, hdr);
    return { ok: true, url: url, size: real || it.size || 0 };
  }

  async function quarkEp(shareUrl, ep, title, cfg, pan) {
    // 2026-11-04：夸克 / UC 共用一套 API，但**配置必须分开** ——
    // 原来这里写死 QK + 'quark'，于是 UC 分享被扔进夸克接口取 token，
    // 必然失败 → 电视本地路径对 UC 永远是死的 → 只能回落服务端，
    // 白白多花 5~10 秒（服务端还要转存+轮询）。
    cfg = cfg || QK; pan = pan || 'quark';
    var ck = global.hai.ck(pan);
    if (!ck) return { ok: false, error: '没有' + pan + '凭据（/sdcard/TV/.' + pan + '_user.pg）' };
    var t = await qkToken(cfg, ck, shareUrl);
    if (!t || !t.stoken) return { ok: false, error: '分享失效或需要提取码' };
    var files = await qkTree(cfg, ck, t.sid, t.stoken, '0');
    if (!files.length) return { ok: false, error: '分享里没扫到视频' };
    var f = pickEp(files, ep, title);
    if (!f) return { ok: false, error: '找不到第 ' + ep + ' 集', total: files.length };

    // ① 免转存先试（UC 可通，夸克一般回 21001）
    //    **但 <8MB 的一律当试看片段**：实测 UC 对未转存的分享只给 15MB 试看
    //    （分享页报 1.35GB，直链 /total 只有 15,340,287）。
    //    UC 转存后也一样（账号无下载权限）⇒ 直接报错，别浪费时间转存。
    var lk = await qkLink(cfg, ck, f.fid);
    if (lk.ok && lk.size && lk.size < 8 * 1024 * 1024) {
      return { ok: false, error: 'UC 只提供 15MB 试看（账号无下载权限），换夸克/百度' };
    }
    if (!lk.ok) {
      var sv = await qkSave(cfg, ck, t.sid, t.stoken, f);
      if (!sv.ok) return { ok: false, error: sv.error };
      lk = await qkLink(cfg, ck, sv.fid);
      if (lk.ok && lk.size && lk.size < 8 * 1024 * 1024) {
        return { ok: false, error: '转存后仍只给试看片段（账号无下载权限）' };
      }
    }
    if (!lk.ok) return { ok: false, error: lk.error };
    return {
      ok: true, url: lk.url, size: lk.size || f.size,
      name: f.name, pan: pan, mode: 'origin',
      header: dlHeaders(cfg, ck)
    };
  }

  // ================= 百度 =================
  function bdLogid() {
    var s = '';
    var hex = '0123456789ABCDEF';
    for (var i = 0; i < 32; i++) s += hex[Math.floor(Math.random() * 16)];
    return btoa(s + ':FG=1');
  }
  function bdDplogid() {
    var n = '';
    for (var i = 0; i < 19; i++) n += Math.floor(Math.random() * 10);
    return n;
  }

  async function bdOpen(ck, shareUrl) {
    var m = shareUrl.match(/pan\.baidu\.com\/s\/1?([\w\-]+)/);
    if (!m) return null;
    var surl = m[1];
    var ref = 'https://pan.baidu.com/s/1' + surl;
    var h = { 'User-Agent': BD.ua, 'Referer': 'https://pan.baidu.com/' };
    var r = await req('GET', ref, h);
    var html = r.body || '';
    function pick(re) {
      var mm = html.match(re);
      return mm ? mm[1] : '';
    }
    return {
      surl: surl, ref: ref,
      shareid: pick(/"shareid"\s*:\s*"?(\d{6,})"?/),
      uk: pick(/"share_uk"\s*:\s*"(\d+)"/),
      bdstoken: pick(/"bdstoken"\s*:\s*"([^"]+)"/)
    };
  }

  async function bdVerify(ck, o, pwd) {
    var q = '?t=' + Date.now() + '&bioc=1&surl=' + o.surl
      + '&channel=chunlei&web=1&app_id=' + BD.appId
      + '&bdstoken=' + o.bdstoken + '&logid=' + encodeURIComponent(bdLogid())
      + '&clienttype=0&dp-logid=' + bdDplogid();
    var h = { 'User-Agent': BD.ua, 'Referer': o.ref,
              'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
              'X-Requested-With': 'XMLHttpRequest' };
    var r = await req('POST', 'https://pan.baidu.com/share/verify' + q, h,
      form({ pwd: pwd, vcode: '', vcode_str: '' }));
    if (!(r.json && Number(r.json.errno) === 0)) return false;
    // verify 会回 randsk，浏览器是把它当 BDCLND cookie 存下来再带回去的。
    // 我们的 http 桥没有 cookie 罐，必须手动带上 —— 少了它 share/list
    // 恒回 errno 2「链接出错了」（2026-11-02 实测：Python 有 jar 所以一直正常，
    // JS 这边就永远「分享里没扫到文件」）。
    o.bdclnd = r.json.randsk || r.json.randskstr || '';
    return true;
  }

  /** 分享列表 —— 参数是 CDP 抓包定死的，少一个就 errno 2 */
  async function bdList(ck, o, dirPath) {
    var isRoot = (!dirPath || dirPath === '/');
    var q = '?web=5&app_id=' + BD.appId + '&desc=1&showempty=0&page=1&num=100'
      + '&order=time&shorturl=' + o.surl + '&view_mode=1'
      + '&channel=chunlei&web=1&bdstoken=' + o.bdstoken
      + '&logid=' + encodeURIComponent(bdLogid())
      + '&clienttype=0&dp-logid=' + bdDplogid();
    // **子目录不能带 root=1**（带了百度永远回分享根，套娃分享就扫不出文件）
    if (isRoot) { q += '&root=1'; }
    else { q += '&dir=' + encodeURIComponent(dirPath); }
    var h = { 'User-Agent': BD.ua, 'Referer': o.ref,
              'X-Requested-With': 'XMLHttpRequest' };
    if (o.bdclnd) { h.Cookie = 'BDCLND=' + o.bdclnd; }
    // 百度这个接口偶尔空回（同一条分享上一秒 5 项、下一秒 0 项），空结果重试两次
    var last = null;
    for (var attempt = 0; attempt < 3; attempt++) {
      var r = await req('GET', 'https://pan.baidu.com/share/list' + q, h);
      last = r;
      var lst = (r.json && r.json.list) || [];
      if (lst.length || (r.json && Number(r.json.errno) !== 0)) { return lst; }
      await new Promise(function (z) { setTimeout(z, 350); });
    }
    return (last && last.json && last.json.list) || [];
  }

  async function bdSignTs(ck, o) {
    var q = '?surl=1' + o.surl + '&fields=sign,timestamp&view_mode=1'
      + '&channel=chunlei&web=1&app_id=' + BD.appId
      + '&bdstoken=' + o.bdstoken + '&logid=' + encodeURIComponent(bdLogid())
      + '&clienttype=0&dp-logid=' + bdDplogid();
    var h = { 'User-Agent': BD.ua, 'Referer': o.ref,
              'X-Requested-With': 'XMLHttpRequest' };
    if (o.bdclnd) { h.Cookie = 'BDCLND=' + o.bdclnd; }
    var r = await req('GET', 'https://pan.baidu.com/share/tplconfig' + q, h);
    var d = (r.json && r.json.data) || {};
    return { sign: d.sign || '', ts: d.timestamp || 0 };
  }

  async function bdTree(ck, o, dirPath, depth) {
    depth = depth || 0;
    var out = [];
    var list = await bdList(ck, o, dirPath);
    for (var i = 0; i < list.length; i++) {
      var it = list[i];
      var name = it.server_filename || '';
      if (String(it.isdir) === '1') {
        if (depth < 5) {
          var sub = await bdTree(ck, o, it.path || dirPath, depth + 1);
          out = out.concat(sub);
        }
      } else {
        out.push({ fid: String(it.fs_id), name: name, size: Number(it.size) || 0,
                   path: it.path || '' });
      }
    }
    return out;
  }

  async function baiduEp(shareUrl, ep, title) {
    // 2026-11-03：百度**全部交给服务端**，这里直接秒退，别再本地扫一遍。
    //
    // 原因（实测）：非会员的下载直链被限到 ~100KB/s、大 Range 直接 403；
    // 真正能播的是服务端的 PCS 在线播放（`method=streaming` → m3u8）。
    // 而本地要判断这些必须先 open_share + 扫目录（110 个文件要好几秒），
    // 扫完还是得回服务端 —— 纯浪费。这里立刻返回 needServer，让上层直接
    // 走 /hai/poster/play，省掉每次点击的 5~10 秒。
    return {
      ok: false, needServer: 1, pan: 'baidu',
      error: '百度走服务端在线播放（HLS）'
    };
  }

  // ================= 对外 =================
  var HaiPlay = {
    ready: hasBridge,
    parseEp: parseEp,
    pickEp: pickEp,

    /** 一句话入口：分享链接 + 集号 -> 可播地址 + 请求头 */
    ep: async function (opt) {
      if (!hasBridge()) {
        return { ok: false, error: '没有 hai bridge（APK 里没注入 HaiJs）' };
      }
      var share = (opt && opt.share) || '';
      var ep = (opt && opt.ep) || 1;
      var title = (opt && opt.title) || '';
      try {
        if (/pan\.quark\.cn/.test(share)) return await quarkEp(share, ep, title, QK, 'quark');
        if (/drive\.uc\.cn|uc\.cn/.test(share)) return await quarkEp(share, ep, title, UC, 'uc');
        if (/pan\.baidu\.com/.test(share)) return await baiduEp(share, ep, title);
        return { ok: false, error: '不支持的网盘: ' + share.slice(0, 40) };
      } catch (e) {
        return { ok: false, error: String(e) };
      }
    },

    /** 把结果变成可以直接跳的 haiplay:// 地址 */
    href: function (r, ep, title) {
      if (!r || !r.ok) return '';
      var tok = global.hai.dp(r.url, JSON.stringify(r.header || {}), r.size || 0);
      // 用 **HaiDirect**（我们自己的站源）：id 就是播放地址本身，
      // detail()/play() 原样交出去，**不启任何原生 Activity**。
      // 之前用 PG 内建的 push_agent，它会渲染一个原生「推送」详情页
      // （紫底那页），全播时看不出来，一按返回就露出来 —— 已废。
      // 2026-11-05 修（海哥「所有资源找不到数据」的真因之一）：
      // 原来写死 127.0.0.1 —— 那条只有 APK 的 HaiDp 拦截器认得。HaiDp 一旦
      // 没挂到播放器实际取流的 OkHttpClient 上，127.0.0.1 就是死地址（电视
      // 本地 80 口没人听）⇒ 播放器打不开 ⇒ 报「找不到数据」。
      // 改用页面 origin（= 中枢 10.0.0.11:8099）：HaiDp 在 → 仍按路径
      // `/hai/dp/` 拦截、电视直连 CDN 满速；HaiDp 不在 → 由中枢 `/hai/dp`
      // 代拉兜底。两条路都活，不再把「能不能播」押在 HaiDp 必须注册上。
      var _org = (typeof location !== 'undefined' && location.origin)
        ? location.origin : 'http://10.0.0.11:8099';
      var id = _org + '/hai/dp/' + tok;
      // 注意：**不要**往这条 URL 上加 `pic=`（2026-11-03 实测）。
      // 我们自己改的 WebDetailActivity.a() 拿到带 pic 的 haiplay:// 之后
      // 就不再启动 VideoActivity（点播放只留网页遮罩，永远进不了播放器）。
      // 海报由网页遮罩（_JS_CURTAIN）负责，播放器窗口那层以后另找通道。
      // ep 必须为 0：HaiDirect 直连蜘蛛 detail() 永远只回「一条已解析好的直链」
      // = 单集（索引 0）。fongmi 用 0 基 ep 取 playUrls[ep]，带剧集号（1/2/7…）
      // 会越界取到 null → requireNonNull → 找不到数据。
      return 'haiplay://play?site=HaiDirect&id=' + encodeURIComponent(id)
        + '&ep=0' + '&name=' + encodeURIComponent(title || '');
    }
  };

  global.HaiPlay = HaiPlay;


  // ---- 网盘 CK 自动上送中枢（2026-11-03）---------------------------------
  //
  // 事故复盘：电视上重新扫码只写 `/sdcard/TV/.<pan>_user.pg`，中枢那份
  // `data/panck.json` 不跟着变。于是直链照样签得出来，但夸克 CDN 判
  // `412 third-party-remote-auth`（__puus / Video-Auth 还是旧会话）⇒ 播放 3003。
  // 这里页面加载时把端上 CK 推一次（每小时最多一次），保持两边同源。
  (function () {
    var pans = ['quark', 'uc', 'baidu'];
    try {
      if (!global.hai || !global.hai.ck) { return; }
      var last = 0;
      try { last = +(global.localStorage.getItem('hai.ckpush') || 0); } catch (e) {}
      if (new Date().getTime() - last < 3600 * 1000) { return; }
      var body = {}, n = 0;
      for (var i = 0; i < pans.length; i++) {
        var v = '';
        try { v = global.hai.ck(pans[i]) || ''; } catch (e) { v = ''; }
        if (v && v.length > 20) { body[pans[i]] = v; n++; }
      }
      if (!n) { return; }
      var x = new XMLHttpRequest();
      x.open('POST', '/hai/panck', true);
      x.setRequestHeader('Content-Type', 'application/json');
      x.onload = function () {
        try { global.localStorage.setItem('hai.ckpush', String(new Date().getTime())); } catch (e) {}
      };
      x.send(JSON.stringify(body));
    } catch (e) {}
  })();

})(window);
