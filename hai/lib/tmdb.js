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

var CLASSES = [
    [1, '热门电影'], [2, '热门剧集'], [3, '高分电影'], [4, '正在上映'],
    [5, '高分剧集'], [6, '华语剧集'], [7, '日本动画'], [8, '即将上映']
];

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
    var d = _get(API + '?ac=detail&t=1&pg=1');
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

function detail(id) {
    var d = _get(API + '?ac=detail&ids=' + id);
    var raw = d.list || [];
    if (!raw.length) {
        _log('detail empty: ' + id);
        return JSON.stringify({ list: [] });
    }
    var it = raw[0];
    _log('detail ' + it.vod_name + ' | ' + it.vod_play_from);
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
            vod_play_from: it.vod_play_from || '',
            vod_play_url: it.vod_play_url || ''
        }]
    });
}

function play(flag, id, flags) {
    // id 已经是 push://https://pan.xxx/s/yyy：parse=0 让 app 别去做网页解析，
    // 直接把 push:// 交给网盘模块（转存 + 取直链都在 jar 里）。
    _log('play flag=' + flag + ' id=' + id);
    return JSON.stringify({ parse: 0, playUrl: '', url: id, header: '' });
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
