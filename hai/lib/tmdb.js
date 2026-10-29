/**
 * TMDB 海报墙 —— hai 本地包 JS 源（type=3 / spider 通道）
 *
 * 接口形状照 app 内置的 ./lib/alist.min.js 来（它末尾是
 *   export default{init,home,homeVod,category,detail,play,search}
 * 每个方法都返回 JSON 字符串），不是 drpy 的 var rule 那一套。
 *
 * 为什么必须写成 JS 源、不能再走 type=1 苹果CMS 接口：
 *   app 的 dex 里 **一处都没有** pan.quark.cn / quark 字样，网盘转存与取直链
 *   全部在 pg.jar 的 spider 内部。type=1 站点把夸克分享当普通视频地址丢给
 *   播放器，必然「播放失败」。走 spider 通道，play() 返回 push:// 链接才会
 *   被 app 拦截后交给网盘模块。
 *
 * 数据来源：中枢自建接口 /hai/tmdb/api.php/provide/vod/（苹果 CMS V10）
 */

var HOST = 'http://10.0.0.11:8099';
var API = HOST + '/hai/tmdb/api.php/provide/vod/';

// 类目顺序 = 海报墙上的顺序，必须和 core/tmdbwall.py 的 CLASSES 一字不差。
// 1 是「我的追剧」（中枢 data/watchlist.json，详情页点一键追剧加进来）。
// 热门/高分剧集已经排掉动画（without_genres=16），国漫日漫各自单开，互不串味。
var CLASSES = [
    [1, '我的追剧'],
    [2, '热门电影'], [3, '热门剧集'], [4, '热门国漫'], [5, '热门日漫'],
    [6, '高分电影'], [7, '高分剧集'], [8, '高分国漫'], [9, '高分日漫'],
    [10, '正在上映'], [11, '即将上映']
];

// 首屏推荐用「热门电影」而不是「我的追剧」——追剧刚装好是空的，首屏空白很怪
var HOME_TID = 2;

// QuickJS 环境里没有 print（alist.min.js 那份是自己定义的），只有 console.log
function _log(msg) {
    try { console.log('[tmdbwall] ' + msg); } catch (e) { }
}

function init(ext) {
    _log('init, ext=' + (typeof ext === 'object' ? JSON.stringify(ext) : String(ext)));
}

function _get(url) {
    try {
        var res = req(url, {
            headers: { 'User-Agent': 'okhttp/5.0.0-alpha.14' },
            timeout: 30000
        });
        if (!res) { return {}; }
        if (typeof res.json === 'function') { return res.json() || {}; }
        if (res.content) { return JSON.parse(res.content); }
    } catch (e) {
        _log('req fail: ' + e + ' url=' + url);
    }
    return {};
}

function _item(it) {
    return {
        vod_id: it.vod_id,
        vod_name: it.vod_name,
        vod_pic: it.vod_pic,
        vod_remarks: it.vod_remarks,
        vod_year: it.vod_year,
        type_name: it.type_name
    };
}

function home(filter) {
    var cls = [];
    for (var i = 0; i < CLASSES.length; i++) {
        cls.push({ type_id: CLASSES[i][0], type_pid: 0, type_name: CLASSES[i][1] });
    }
    return JSON.stringify({ class: cls, filters: {} });
}

function homeVod(params) {
    var d = _get(API + '?ac=detail&t=' + HOME_TID + '&pg=1');
    var list = [];
    var raw = d.list || [];
    for (var i = 0; i < raw.length; i++) { list.push(_item(raw[i])); }
    return JSON.stringify({ list: list });
}

function category(tid, pg, filter, extend) {
    var d = _get(API + '?ac=detail&t=' + tid + '&pg=' + pg);
    var list = [];
    var raw = d.list || [];
    for (var i = 0; i < raw.length; i++) { list.push(_item(raw[i])); }
    return JSON.stringify({
        page: parseInt(pg, 10) || 1,
        pagecount: 500,
        limit: 20,
        total: 9999,
        list: list
    });
}

/* ---------------------------------------------------------------------------
 * 线路排序（2026-10-29）
 *
 * 【为什么】夸克那组下发的是 `push://https://pan.quark.cn/s/xxx`，要靠 jar 的
 * QuarkPan 去转存取直链；而它内部走 MyRequest.getGoProxyBase 找 10078 这个
 * **外部 Go 代理**（README:48 的 aliproxy 只管外部加速，不是它）。本机没有这个
 * 服务（实测 ss -ltnp：com.hai.tv 只有 9978 与 10079，aliproxy 进程纯出站不监听），
 * 于是 play 必挂。而「夸克2 / UC / 百度」那几组是 /p/…/<base64> 形式，解开就是
 * app 自带 9978/proxy?do=quark&type=dwnz&file_id=…，实测能直接换到真直链。
 *
 * 所以只**调顺序**不删线路：能自解析的排前面（默认起播先试它），
 * push:// 留作兜底 —— 哪天补上 Go 代理还能用，也不改变线路数量。
 * ------------------------------------------------------------------------- */
function _play_order(from, urls) {
    var fs = String(from || '').split('$$$');
    var us = String(urls || '').split('$$$');
    if (fs.length < 2 || fs.length !== us.length) {
        return { from: from, url: urls };
    }
    var okF = [], okU = [], psF = [], psU = [], i, u;
    for (i = 0; i < fs.length; i++) {
        u = String(us[i] || '');
        if (u.indexOf('push://') === 0) { psF.push(fs[i]); psU.push(us[i]); }
        else { okF.push(fs[i]); okU.push(us[i]); }
    }
    if (!okF.length || !psF.length) {
        return { from: from, url: urls };    // 全是一种，别动
    }
    return { from: okF.concat(psF).join('$$$'), url: okU.concat(psU).join('$$$') };
}

function detail(id) {
    var d = _get(API + '?ac=detail&ids=' + id);
    var raw = d.list || [];
    if (!raw.length) {
        _log('detail empty: ' + id);
        return JSON.stringify({ list: [] });
    }
    var it = raw[0];
    var pf = _play_order(it.vod_play_from || '', it.vod_play_url || '');
    _log('detail ' + it.vod_name + ' | ' + pf.from);
    return JSON.stringify({
        list: [{
            vod_id: it.vod_id,
            vod_name: it.vod_name,
            vod_pic: it.vod_pic,
            vod_pic_slide: it.vod_pic_slide || it.vod_pic,
            type_name: it.type_name,
            vod_year: it.vod_year,
            vod_area: it.vod_area,
            vod_lang: it.vod_lang,
            vod_actor: it.vod_actor,
            vod_director: it.vod_director,
            vod_content: it.vod_content,
            vod_remarks: it.vod_remarks,
            vod_play_from: pf.from,
            vod_play_url: pf.url
        }]
    });
}

/* ---------------------------------------------------------------------------
 * 网盘线路归一化（2026-10-29）
 *
 * 【问题】花瓣/美剧迷这类网盘站点下发的播放地址是
 *     http://127.0.0.1:10078/p/50/null/262144/<base64>[/178.mkv]
 * 10078 是 **PG 服务器侧的中继**（把 /p/<...>/<base64> 解开、回源到客户端的
 * 9978/proxy，再回传直链）。电视机上根本没有 10078 这个监听 —— 实测
 * /proc/net/tcp 里只有 9978（app 内置 server）和 10079（jar 的延时/图片服务），
 * 原版包同样没有 10078。于是播放器连 127.0.0.1:10078 一律
 * ECONNREFUSED → 用户看到的「能进播放器但播放失败」。
 *
 * 【修法】base64 解开就是客户端**自己**的
 *     http://127.0.0.1:9978/proxy?do=quark&type=dwnz&...
 * 这个接口实测 200 并在正文里直接给真直链 + 请求头：
 *     https://dl-pc-zb.drive.quark.cn/...&filename=178.mkv@Headers={"Cookie":"..."}
 * 所以绕过中继：自己请求内层 → 拆出「直链」和「@Headers=」，parse=0 把直链和
 * 头一起交给播放器。不碰 baseline 的 js/*，只改我们这个自有文件。
 * ------------------------------------------------------------------------- */
var _B64T = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/';

// QuickJS 没有 atob，手搓一个（只处理标准表，忽略非表字符）
function _b64dec(s) {
    var out = '', buf = 0, bits = 0, i, v;
    s = String(s || '');
    for (i = 0; i < s.length; i++) {
        v = _B64T.indexOf(s.charAt(i));
        if (v < 0) { continue; }
        buf = (buf << 6) | v;
        bits += 6;
        if (bits >= 8) {
            bits -= 8;
            out += String.fromCharCode((buf >> bits) & 0xFF);
        }
    }
    return out;
}

// 认 10078 中继地址：host 不限（127.0.0.1 / 10.0.0.200 都见过）
// 后面可能还缀着 /178.mkv，而 base64 标准表里带 '/' 和 '+'，贪婪匹配会把文件名
// 一起吞进去 → 先取整段尾巴，再按 '/' 逐段拼，直到解出 http 开头为止。
var _RELAY_RE = /^https?:\/\/[^\/]+:10078\/p\/[^\/]+\/[^\/]+\/[^\/]+\/(.+)$/;

function _unwrap_relay(u) {
    var m = _RELAY_RE.exec(u);
    if (!m) { return ''; }
    var tail = m[1].split('#')[0].split('?')[0];
    var segs = tail.split('/'), acc = '', i, inner;
    for (i = 0; i < segs.length && i < 4; i++) {
        acc += (i ? '/' : '') + segs[i];
        inner = _b64dec(acc);
        if (/^https?:\/\//i.test(inner)) { return inner; }
    }
    return '';
}

// 内层是 app 自带 server 的 /proxy?do=quark → 拿真直链 + 请求头
function _resolve_proxy(u) {
    if (u.indexOf('/proxy?') < 0 || u.indexOf('do=quark') < 0) { return null; }
    try {
        var res = req(u, {
            headers: { 'User-Agent': 'okhttp/5.0.0-alpha.14' },
            timeout: 20000
        });
        var body = (res && (res.content || '')) || '';
        var k = body.indexOf('@Headers=');
        if (k < 0) { return null; }
        var link = body.substring(0, k).trim();
        var head = body.substring(k + 9).trim();
        // 正文尾巴还带 "###TS:0" 之类的标记，必须裁到最后一个 '}' 为止，
        // 否则播放器按 JSON 解析请求头会整条丢掉。
        var end = head.lastIndexOf('}');
        if (end < 0) { return null; }
        head = head.substring(0, end + 1);
        if (!/^https?:\/\//i.test(link)) { return null; }
        return { url: link, header: head };
    } catch (e) {
        _log('resolve fail: ' + e);
    }
    return null;
}

function play(flag, id, flags) {
    var u = String(id == null ? '' : id).trim();
    _log('play flag=' + flag + ' id=' + u.substring(0, 120));

    // 播放列表里是「第1集$http://...」，play 收到的是整串时要拆掉集名前缀
    var d = u.indexOf('$');
    if (d > -1) { u = u.substring(d + 1).trim(); }

    if (/^push:\/\//i.test(u)) {
        // 已经是 push://（夸克分享链接形式）：交给 jar 的网盘模块
        return JSON.stringify({ parse: 0, playUrl: '', url: u, header: '' });
    }

    var inner = _unwrap_relay(u);
    if (inner) {
        var r = _resolve_proxy(inner);
        if (r) {
            _log('relay unwrapped -> 直链 ' + r.url.substring(0, 80));
            return JSON.stringify({ parse: 0, playUrl: '', url: r.url, header: r.header });
        }
        // 解不开就退回内层地址，至少不再连 10078 那个死端口
        _log('relay unwrapped but no direct link, fallback inner');
        return JSON.stringify({ parse: 0, playUrl: '', url: inner, header: '' });
    }

    return JSON.stringify({ parse: 0, playUrl: '', url: u, header: '' });
}

function search(wd, quick) {
    var d = _get(API + '?wd=' + encodeURIComponent(wd) + '&pg=1');
    var list = [];
    var raw = d.list || [];
    for (var i = 0; i < raw.length; i++) { list.push(_item(raw[i])); }
    return JSON.stringify({ list: list });
}

export default {
    init: init,
    home: home,
    homeVod: homeVod,
    category: category,
    detail: detail,
    play: play,
    search: search
};
