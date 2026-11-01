/**
 * 海哥直连 —— 把「一条网盘直链」当成一部片直接播。
 *
 * 用途：海报墙详情页点「第N集」时，我们把 127.0.0.1:9978/proxy?... 那条
 * 已备好的直链塞进 `haiplay://play?site=HaiDirect&id=<直链>`，由本站在
 * detail() 里原样交出去 —— **跳过站源的选集页**，播放器拿到就是画面，
 * 整个起播链条看不到 PG 原生界面。
 *
 * 为什么改成 export default 形式（2026-11-01）：
 * 原来是 drpy 的 `var rule = {...}` 写法，本 app 的 JS 通道（type=3）实行的
 * 是 fongmi 原生 Spider 接口（同 ./lib/tmdb.js、./lib/alist.min.js），
 * rule 块里的「二级」不会被当作 detail 执行 —— 实测表现就是播放器弹
 * 「找不到数据」，logcat 里一条 Objects.requireNonNull 的 NPE
 * （spider 返回 null → VOD.vod_play_url 没设上）。
 */

function init(cfg) { }

function home(filter) {
    return JSON.stringify({ class: [] });
}

function homeVod() {
    return JSON.stringify({ list: [] });
}

function category(tid, pg, filter, ext) {
    return JSON.stringify({ list: [] });
}

// id 可能被 URL 编码过（含 %3A%2F%2F），也可能没有 —— 只在这一种情况解码，
// 免得把文件名里正常的 %20 解坏。
function _clean(id) {
    var u = String(id == null ? '' : id).trim();
    if (u.indexOf('%3A%2F%2F') >= 0) {
        try { u = decodeURIComponent(u); } catch (e) { }
    }
    return u;
}

function detail(id) {
    var u = _clean(id);
    if (!u) { return JSON.stringify({ list: [] }); }
    // id 就是中枢的 `/hai/pp/<token>` 播放地址，**原样交出去**。
    //
    // 2026-11-01 改：原来这里会先 GET `?json=1` 把真链换回来再播（因为那时
    // 真链是本机 9978 的代理，能裸播）。现在真链是夸克 CDN，有防盗链 ——
    // 播放器自己发请求必 412，只能由中枢代拉（中枢那条短链自带 Cookie 头）。
    // 再去做「换回真链」反而会把播放地址换成裸 CDN，必挂；而且带鉴权的短链
    // 分支不会再吐 json，请求会一直等到超时白卡 8 秒。
    return JSON.stringify({
        list: [{
            vod_id: u,
            vod_name: '海哥网盘',
            vod_pic: '',
            vod_pic_slide: '',
            vod_remarks: '',
            vod_play_from: '海哥网盘',
            vod_play_url: '播放$' + u
        }]
    });
}

function play(flag, id, flags) {
    var u = _clean(id);
    if (!u) { return JSON.stringify({ parse: 0, playUrl: '', url: '', header: '' }); }
    // 播放列表里可能是「播放$地址」，拆掉前缀
    var d = u.indexOf('$');
    if (d > -1) { u = u.substring(d + 1).trim(); }
    return JSON.stringify({ parse: 0, playUrl: '', url: u, header: '' });
}

function search(wd, quick, pg) {
    return JSON.stringify({ list: [] });
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
