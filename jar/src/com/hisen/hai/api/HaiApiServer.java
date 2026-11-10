// -*- coding: utf-8 -*-
package com.hisen.hai.api;

import android.content.Context;
import android.database.Cursor;
import android.database.sqlite.SQLiteDatabase;

import com.hisen.hai.HaiBoot;
import com.hisen.hai.HaiCore;
import com.hisen.hai.proxy.DownloadProxyServer;
import com.hisen.hai.proxy.PortRelay;
import com.hisen.hai.proxy.ProxyConfig;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.InputStream;
import java.util.LinkedHashMap;
import java.util.Map;

/**
 * APK 内置 HTML UI 的数据接口层（同形 /hai/* 路由）。
 *
 * <p>目标：服务器瘦身后不再渲染任何页面，UI 从 file:///android_asset/haiui/*.html
 * 加载，bridge.js 把 /hai/* 指到本 jar 的本地服务。这里提供同源路径，
 * 所以阶段 1 抽出的 1396 行 JS 一行都不用改。
 *
 * <p><b>站源照搬</b>：站源数据直接读 App 自己那份 Config 表（原版落库格式），
 * 不另造一份配置存储 —— 设置页里改的、首页用的、我们 UI 显示的必须是同一份。
 * 后续接 SiteBridge 调 classes.dex 里原封不动的 456 个 csp_* 站源类。
 */
public final class HaiApiServer implements DownloadProxyServer.ApiHandler {

    /** App 原生 Room 数据库（与 Config / Site 表在同一进程，直接读写）。 */
    private static final String DB_PATH =
            "/data/user_de/0/com.hisen.android.tv/databases/tv";

    private final Context ctx;

    public HaiApiServer(Context c) { this.ctx = c; }

    /**
     * App 原生 DB 的真实路径。优先走 Context（凭据加密存储 / 多用户都对），
     * 拿不到再回退硬编码 —— 避免站源页因为路径差异整页空白。
     */
    private String dbPath() {
        try {
            java.io.File f = ctx.createDeviceProtectedStorageContext().getDatabasePath("tv");
            if (f != null && f.exists()) return f.getAbsolutePath();
        } catch (Throwable ignored) {}
        try {
            java.io.File f = ctx.getDatabasePath("tv");
            if (f != null && f.exists()) return f.getAbsolutePath();
        } catch (Throwable ignored) {}
        return DB_PATH;
    }

    /**
     * 纯本地离线路由（2026-11-09 定案：服务器退出运行时链路）。
     *
     * <p>海报墙 / 详情页 / CSS / JS / 图片全部来自 APK assets/haiui/ 的离线快照，
     * 运行时对 10.0.0.11 零请求。服务器只保留 jsm.json 下发与快照更新包。
     * 播放入口统一 haiplay://search?q=片名 → PG 原版站源 + Go 取流，本服务不碰播放。
     */
    public DownloadProxyServer.Response handle(String path, String query,
                                               Map<String, String> headers) {
        try {
            // ---- 本地配置类 API：直接读 App 自己的 Config 表，不联网 ----
            if ("/hai/ping".equals(path)) {
                // 墙页每次加载都会打 ping：顺手再踢一次 PG 服务保活，
                // 覆盖「App 进程没重建」或首轮反射太早的时序情况（幂等）。
                try { HaiBoot.kick(); } catch (Throwable ignored) {}
                return json("{\"ok\":true,\"core\":\"jar\",\"offline\":true,\"proxy\":"
                        + DownloadProxyServer.get().port() + ",\"pg\":" + HaiBoot.status()
                        + ",\"relay\":" + PortRelay.status()
                        + ",\"proc\":\"" + HaiCore.proc() + "\"}");
            }
            if ("/hai/poster/cfg".equals(path)) return json(cfgJson());
            if ("/hai/site/list".equals(path)) return json(siteListJson());
            if ("/hai/site/active".equals(path)) return json(activeConfigJson());
            if ("/hai/site/configs".equals(path)) return json(configsJson());
            if ("/hai/site/prefs".equals(path)) return json(setSitePref(query));

            // ---- 详情页 legacy 脚本的统一落点：本地空对象，不再请求服务端取链 ----
            if ("/hai/offline/empty.json".equals(path)) return json("{}");

            // ---- 海海哥哥.海报墙站源本体：本地 stub（2026-11-09 定案） ----
            // 站源只是首页入口占位：VideoActivity 按 key 分流到 WebDetailActivity，
            // 海报墙 / 详情页数据 100% 来自 APK assets 离线快照，spider 永不被调用。
            // 这里仍给一个合法的空 spider，保证设置页或聚合搜索万一加载它时，
            // 只走 127.0.0.1:10090，绝不外连服务器，也不会因 404 触发配置解析异常。
            if ("/hai/lib/tmdb.js".equals(path)) return plainJs(LOCAL_SITE_JS);
            if ("/hai/lib/tmdb.json".equals(path)) return json(LOCAL_SITE_EXT);
            if (path.startsWith("/hai/tmdb/")) return json("{}");

            // ---- 墙页首屏（WebView 加载 /hai/poster/d/ 空 id） ----
            if ("/hai/poster/d/".equals(path) || "/hai/poster/d".equals(path)) {
                DownloadProxyServer.Response r =
                        asset(ProxyConfig.WALL_ASSET, "text/html; charset=utf-8");
                return r != null ? r : plain(404, "wall.html missing");
            }

            // ---- 墙页分类 / 翻页：md5(归一化 query) → assets/haiui/pages/*.html ----
            if ("/hai/poster".equals(path) || "/hai/poster/".equals(path)) {
                DownloadProxyServer.Response r = asset(
                        "haiui/pages/" + md5Hex(normKey(query)) + ".html",
                        "text/html; charset=utf-8");
                if (r != null) return r;
                DownloadProxyServer.Response home =
                        asset(ProxyConfig.WALL_ASSET, "text/html; charset=utf-8");
                return home != null ? home : plain(404, "no offline wall page");
            }

            // ---- 详情页：按 vid 取静态快照，未收录则给 PG 搜索兜底页 ----
            if (path.startsWith("/hai/poster/d/")) {
                String vid = path.substring("/hai/poster/d/".length());
                int slash = vid.indexOf('/');
                if (slash >= 0) vid = vid.substring(0, slash);
                DownloadProxyServer.Response r = asset(
                        "haiui/detail/" + safeSeg(vid) + ".html", "text/html; charset=utf-8");
                return r != null ? r : fallbackDetail(vid);
            }

            // ---- 其它 /hai/poster/* 动态接口：全部本地兜底，绝不外连 ----
            if (path.startsWith("/hai/poster/")) {
                String leaf = path.substring("/hai/poster/".length());
                if (leaf.startsWith("eps") || leaf.startsWith("play")
                        || leaf.startsWith("progress") || leaf.startsWith("warm")
                        || leaf.startsWith("cast") || leaf.startsWith("src")) {
                    return json("{}");
                }
                // 演员页 / 站内搜索 / 一键追剧等没有离线快照 → 回墙页
                return redirect("/hai/poster/d/");
            }

            // ---- 静态资源：/hai/<rel> → assets/haiui/<rel> ----
            if (path.startsWith("/hai/")) {
                String rel = path.substring("/hai/".length());
                if (rel.indexOf("..") >= 0) return plain(400, "bad path");
                // 快照图片统一压成 JPEG（文件名可能仍是 .png/.webp），按内容给 MIME
                String ct = rel.startsWith("dbimg/") ? "image/jpeg" : guessCt(rel);
                DownloadProxyServer.Response r = asset("haiui/" + rel, ct);
                if (r != null) return r;
                if (rel.startsWith("dbimg/")) {
                    DownloadProxyServer.Response ph =
                            asset("haiui/dbimg/_placeholder.jpg", "image/jpeg");
                    if (ph != null) return ph;
                }
                return plain(404, "offline asset missing: " + rel);
            }
            return plain(404, "not found");
        } catch (Throwable t) {
            return new DownloadProxyServer.Response(500, "application/json; charset=utf-8",
                    "{\"ok\":false,\"msg\":\"" + escape(String.valueOf(t)) + "\"}");
        }
    }

    /** 读 APK assets；不存在返回 null 交给调用方降级。 */
    private DownloadProxyServer.Response asset(String rel, String ct) {
        java.io.InputStream in = null;
        try {
            in = ctx.getAssets().open(rel);
            byte[] b = readAll(in);
            DownloadProxyServer.Response r =
                    new DownloadProxyServer.Response(200, ct, b);
            if (rel.endsWith(".html")) r.extra.put("Cache-Control", "no-cache");
            else r.extra.put("Cache-Control", "public, max-age=86400");
            return r;
        } catch (Throwable ignored) {
            return null;
        } finally {
            if (in != null) try { in.close(); } catch (Throwable ignored) {}
        }
    }

    /** 归一化 query，与 make_offline_snapshot.py 的 norm_key() 逐字一致。 */
    private static String normKey(String query) {
        java.util.TreeMap<String, String> m = new java.util.TreeMap<String, String>();
        if (query != null) {
            for (String pair : query.split("&")) {
                if (pair.length() == 0) continue;
                int i = pair.indexOf('=');
                if (i < 0) m.put(pair, "");
                else m.put(pair.substring(0, i), pair.substring(i + 1));
            }
        }
        if (!m.containsKey("pg")) m.put("pg", "1");
        StringBuilder sb = new StringBuilder();
        for (Map.Entry<String, String> e : m.entrySet()) {
            if (sb.length() > 0) sb.append('&');
            sb.append(e.getKey()).append('=').append(e.getValue());
        }
        return sb.toString();
    }

    private static String md5Hex(String s) {
        try {
            byte[] d = java.security.MessageDigest.getInstance("MD5")
                    .digest(s.getBytes("UTF-8"));
            StringBuilder sb = new StringBuilder();
            for (byte b : d) sb.append(String.format("%02x", b & 0xff));
            return sb.toString();
        } catch (Throwable t) {
            return "";
        }
    }

    private static String safeSeg(String s) {
        return s == null ? "" : s.replaceAll("[^A-Za-z0-9_\\-]", "");
    }

    private static DownloadProxyServer.Response redirect(String loc) {
        DownloadProxyServer.Response r =
                new DownloadProxyServer.Response(302, "text/plain; charset=utf-8", "");
        r.extra.put("Location", loc);
        return r;
    }

    private static DownloadProxyServer.Response plain(int code, String msg) {
        return new DownloadProxyServer.Response(code, "text/plain; charset=utf-8", msg);
    }

    /** 静态快照未收录的 vid：给一个只走 PG 搜索深链的兜底详情页。 */
    private volatile JSONObject sCards;
    private DownloadProxyServer.Response fallbackDetail(String vid) {
        String name = "";
        try {
            if (sCards == null) {
                java.io.InputStream in = ctx.getAssets().open("haiui/cards.json");
                sCards = new JSONObject(new String(readAll(in), "UTF-8"));
                in.close();
            }
            name = sCards.optString(vid, "");
        } catch (Throwable ignored) {}
        if (name.length() == 0) name = vid;
        String q;
        try { q = java.net.URLEncoder.encode(name, "UTF-8"); }
        catch (Throwable t) { q = name; }
        String t = htmlEsc(name);
        String page = "<!DOCTYPE html><html lang='zh-CN'><head><meta charset='utf-8'>"
                + "<meta name='viewport' content='width=device-width,initial-scale=1'>"
                + "<link rel='stylesheet' href='/hai/ui/detail.css'><title>" + t + "</title>"
                + "</head><body><div class='wrap' style='padding:56px'>"
                + "<h1>" + t + "</h1><p>本地离线快照未收录该条目详情。</p>"
                + "<p><a class='btn' href='haiplay://search?q=" + q + "'>▶ 播放（PG 站源）</a> "
                + "<a class='btn ghost' href='/hai/poster/d/'>← 返回海报墙</a></p>"
                + "</div></body></html>";
        return new DownloadProxyServer.Response(200, "text/html; charset=utf-8", page);
    }

    private static String htmlEsc(String s) {
        return s.replace("&", "&amp;").replace("<", "&lt;")
                .replace(">", "&gt;").replace("\"", "&quot;").replace("'", "&#39;");
    }

    private static byte[] readAll(java.io.InputStream in) throws java.io.IOException {
        java.io.ByteArrayOutputStream bos = new java.io.ByteArrayOutputStream();
        byte[] buf = new byte[8192];
        int n;
        while ((n = in.read(buf)) >= 0) bos.write(buf, 0, n);
        return bos.toByteArray();
    }

    private static String guessCt(String path) {
        String p = path.toLowerCase();
        if (p.endsWith(".js")) return "application/javascript; charset=utf-8";
        if (p.endsWith(".css")) return "text/css; charset=utf-8";
        if (p.endsWith(".html") || p.endsWith(".htm")) return "text/html; charset=utf-8";
        if (p.endsWith(".json")) return "application/json; charset=utf-8";
        if (p.endsWith(".jpg") || p.endsWith(".jpeg")) return "image/jpeg";
        if (p.endsWith(".png")) return "image/png";
        if (p.endsWith(".webp")) return "image/webp";
        if (p.endsWith(".gif")) return "image/gif";
        return "text/html; charset=utf-8";   // 服务器海报墙路由多不带后缀
    }

    private static DownloadProxyServer.Response json(String s) {
        return new DownloadProxyServer.Response(200, "application/json; charset=utf-8", s);
    }

    /**
     * 本地站源 stub：标准 js spider 接口（与服务端 tmdb.js 一致，非 drpy 的 var rule）。
     * 每个方法都返回合法 JSON 字符串，零网络请求；保证 PG 的 JS 引擎加载后
     * 不会因缺方法或解析失败报错。真实数据由 WebView 读 APK assets 离线快照。
     */
    private static final String LOCAL_SITE_JS =
            "/* TMDBWall offline stub - all wall data lives in APK assets */\n"
            + "function init(ext){return '';}\n"
            + "function home(filter){return JSON.stringify({class:[],filters:{},list:[]});}\n"
            + "function homeVod(){return JSON.stringify({list:[]});}\n"
            + "function category(tid,pg,filter,extend){"
            + "return JSON.stringify({list:[],page:1,pagecount:1,limit:20,total:0});}\n"
            + "function detail(id){return JSON.stringify({list:[]});}\n"
            + "function play(flag,id,flags){return JSON.stringify({});}\n"
            + "function search(wd,quick){return JSON.stringify({list:[]});}\n";

    /** 站源 ext 也指向本地，不持有任何服务器地址。 */
    private static final String LOCAL_SITE_EXT =
            "{\"name\":\"HaiWall(offline)\","
            + "\"api\":\"http://127.0.0.1:10090/hai/offline/empty.json\"}";

    private static DownloadProxyServer.Response plainJs(String s) {
        return new DownloadProxyServer.Response(200,
                "application/javascript; charset=utf-8", s);
    }

    private static String cfgJson() {
        // UI 参数全部端上可配（对应服务端 data/poster.cfg.json），这里给安全默认值。
        return "{\"ok\":true,\"cfg\":{\"rotate\":true,\"interval\":3,"
                + "\"effect\":\"random\",\"imgs\":6,\"play_mode\":\"native\"},"
                + "\"base\":\"" + ProxyConfig.sBase + "\"}";
    }

    /**
     * 读取 App 原生 Config 表。严格对齐原版 AppDatabase.getConfig(type)：
     * {@code type=? ORDER BY time DESC LIMIT 1}（原版不要求 json 非空）。
     * type 语义：0=点播 1=直播 2=壁纸；订阅 urls 是展开成多条 type=0 行，
     * **不是** type=1（旧记忆里写错了，已修正）。
     */
    private JSONObject readActiveConfig() {
        return readConfigByType(0);
    }

    private JSONObject readConfigByType(int type) {
        SQLiteDatabase db = null;
        Cursor c = null;
        try {
            db = SQLiteDatabase.openDatabase(dbPath(), null, SQLiteDatabase.OPEN_READONLY);
            c = db.rawQuery("select id,type,url,time,json from Config "
                    + "where type=? order by time desc limit 1",
                    new String[]{String.valueOf(type)});
            if (c != null && c.moveToFirst()) {
                JSONObject o = new JSONObject();
                o.put("id", c.getLong(0));
                o.put("type", c.getInt(1));
                o.put("url", c.getString(2));
                o.put("time", c.getLong(3));
                o.put("json", c.getString(4));
                return o;
            }
        } catch (Throwable ignored) {
        } finally {
            if (c != null) try { c.close(); } catch (Throwable ignored) {}
            if (db != null) try { db.close(); } catch (Throwable ignored) {}
        }
        return new JSONObject();
    }

    private String activeConfigJson() {
        JSONObject o = readActiveConfig();
        JSONObject out = new JSONObject();
        try {
            out.put("ok", true);
            out.put("id", o.optLong("id", -1));
            out.put("url", o.optString("url", ""));
            out.put("time", o.optLong("time", 0));
            if (o.has("json")) out.put("config", new JSONObject(o.getString("json")));
        } catch (Throwable ignored) {}
        return out.toString();
    }

    /**
     * 站源页复刻的数据源：直接解析激活配置的 sites 数组。
     * 字段与原版 Site 模型对齐：key / name / type / api / ext / searchable / changeable。
     */
    private String siteListJson() {
        JSONObject out = new JSONObject();
        JSONArray arr = new JSONArray();
        try {
            // 勾选状态以 App 原生 Site 表为准（Site.save() 事务写入），
            // 不是 localStorage —— 保证设置页改的、首页用的、我们 UI 显示的是同一份。
            Map<String, int[]> prefs = readSitePrefs();
            JSONObject cfg = readActiveConfig();
            if (cfg.has("json")) {
                JSONObject j = new JSONObject(cfg.getString("json"));
                JSONArray sites = j.optJSONArray("sites");
                if (sites != null) {
                    for (int i = 0; i < sites.length(); i++) {
                        JSONObject s = sites.optJSONObject(i);
                        if (s == null) continue;
                        JSONObject o = new JSONObject();
                        o.put("key", s.optString("key", ""));
                        o.put("name", s.optString("name", ""));
                        o.put("type", s.optInt("type", 0));
                        o.put("api", s.optString("api", ""));
                        o.put("ext", s.optString("ext", ""));
                        int[] pref = prefs.get(s.optString("key", ""));
                        o.put("searchable", pref == null ? s.optInt("searchable", 1) : pref[0]);
                        o.put("changeable", pref == null ? s.optInt("changeable", 1) : pref[1]);
                        o.put("index", i);
                        arr.put(o);
                    }
                }
                out.put("spider", j.optString("spider", ""));
            }
            out.put("ok", true);
            out.put("total", arr.length());
            out.put("sites", arr);
        } catch (Throwable t) {
            try {
                out.put("ok", false);
                out.put("msg", String.valueOf(t));
                out.put("sites", arr);
            } catch (Throwable ignored) {}
        }
        return out.toString();
    }

    /** 读 App 原生 Site 表。真机 schema：key TEXT PRIMARY KEY / searchable INTEGER / changeable INTEGER。 */
    private Map<String, int[]> readSitePrefs() {
        Map<String, int[]> out = new LinkedHashMap<String, int[]>();
        SQLiteDatabase db = null;
        Cursor c = null;
        try {
            db = SQLiteDatabase.openDatabase(dbPath(), null, SQLiteDatabase.OPEN_READONLY);
            c = db.rawQuery("select key,searchable,changeable from Site", null);
            while (c != null && c.moveToNext()) {
                out.put(c.getString(0), new int[]{c.getInt(1), c.getInt(2)});
            }
        } catch (Throwable ignored) {
        } finally {
            if (c != null) try { c.close(); } catch (Throwable ignored) {}
            if (db != null) try { db.close(); } catch (Throwable ignored) {}
        }
        return out;
    }

    /**
     * 配置历史列表。原版 dialog_buttons 列表的数据源是 SQL：
     * {@code SELECT id,name,url,type,time FROM Config WHERE type=? ORDER BY time DESC}。
     * 注意 prefs 的 {@code config_0/config_1/config_2} **不是历史**，是 Config.update()
     * 按 {@code "config_"+type} 写的「当前 URL 指针」（0 点播/1 直播/2 壁纸）。
     */
    private String configsJson() {
        JSONArray arr = new JSONArray();
        JSONObject out = new JSONObject();
        SQLiteDatabase db = null;
        Cursor c = null;
        try {
            db = SQLiteDatabase.openDatabase(dbPath(), null, SQLiteDatabase.OPEN_READONLY);
            c = db.rawQuery("select id,name,url,type,time from Config "
                    + "where type=? order by time desc", new String[]{"0"});
            while (c != null && c.moveToNext()) {
                JSONObject o = new JSONObject();
                o.put("id", c.getLong(0));
                o.put("name", c.getString(1) == null ? "" : c.getString(1));
                o.put("url", c.getString(2) == null ? "" : c.getString(2));
                o.put("type", c.getInt(3));
                o.put("time", c.getLong(4));
                arr.put(o);
            }
            out.put("ok", true);
            out.put("configs", arr);
            android.content.SharedPreferences sp = ctx.getSharedPreferences(
                    "com.hisen.android.tv_preferences", Context.MODE_PRIVATE);
            out.put("site_mode", sp.getInt("site_mode", 0));
            out.put("current", sp.getString("config_0", ""));
        } catch (Throwable t) {
            try {
                out.put("ok", false);
                out.put("msg", String.valueOf(t));
                out.put("configs", arr);
            } catch (Throwable ignored) {}
        } finally {
            if (c != null) try { c.close(); } catch (Throwable ignored) {}
            if (db != null) try { db.close(); } catch (Throwable ignored) {}
        }
        return out.toString();
    }

    /**
     * 写站源勾选，等价原版 Site.save()（一个事务内 for 循环 SyncAdapter 标记）。
     * 参数：/hai/site/prefs?key=&lt;k&gt;&amp;searchable=0|1&amp;changeable=0|1
     * 只写 Site 表，不动 Config，避免与 App 主线程抢整库锁。
     */
    private String setSitePref(String query) {
        Map<String, String> q = parseQuery(query);
        String key = q.get("key");
        if (key == null || key.length() == 0) return "{\"ok\":false,\"msg\":\"missing key\"}";
        int searchable = "0".equals(q.get("searchable")) ? 0 : 1;
        int changeable = "0".equals(q.get("changeable")) ? 0 : 1;
        SQLiteDatabase db = null;
        try {
            db = SQLiteDatabase.openDatabase(dbPath(), null, SQLiteDatabase.OPEN_READWRITE);
            db.execSQL("INSERT OR REPLACE INTO Site(key,searchable,changeable) VALUES(?,?,?)",
                    new Object[]{key, searchable, changeable});
            return "{\"ok\":true,\"key\":\"" + escape(key) + "\"}";
        } catch (Throwable t) {
            return "{\"ok\":false,\"msg\":\"" + escape(String.valueOf(t)) + "\"}";
        } finally {
            if (db != null) try { db.close(); } catch (Throwable ignored) {}
        }
    }

    private static Map<String, String> parseQuery(String q) {
        Map<String, String> out = new LinkedHashMap<String, String>();
        if (q == null) return out;
        for (String pair : q.split("&")) {
            int i = pair.indexOf('=');
            if (i <= 0) continue;
            out.put(ProxyConfig.urlDecode(pair.substring(0, i)),
                    ProxyConfig.urlDecode(pair.substring(i + 1)));
        }
        return out;
    }

    private static String escape(String s) {
        return s.replace("\\", "\\\\").replace("\"", "\\\"").replace("\n", " ");
    }
}
