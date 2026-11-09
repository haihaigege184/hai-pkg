// -*- coding: utf-8 -*-
package com.hisen.hai.api;

import android.content.Context;
import android.database.Cursor;
import android.database.sqlite.SQLiteDatabase;
import android.text.TextUtils;

import com.hisen.hai.proxy.DownloadProxyServer;
import com.hisen.hai.proxy.ProxyConfig;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;
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

    public DownloadProxyServer.Response handle(String path, String query,
                                               Map<String, String> headers) {
        try {
            if ("/hai/ping".equals(path)) {
                return json("{\"ok\":true,\"core\":\"jar\",\"proxy\":" + DownloadProxyServer.get().port() + "}");
            }
            if ("/hai/poster/cfg".equals(path)) {
                return json(cfgJson());
            }
            if ("/hai/site/list".equals(path)) {
                return json(siteListJson());
            }
            if ("/hai/site/active".equals(path)) {
                return json(activeConfigJson());
            }
            if ("/hai/site/configs".equals(path)) {
                return json(configsJson());
            }
            if ("/hai/site/prefs".equals(path)) {
                return json(setSitePref(query));
            }

            // ---- 电视墙首屏：空 id 时 WebDetailActivity 拼出的路径 ----
            // WebView 以 http://127.0.0.1:10090/hai/poster/d/ 加载，路径里没有 vid。
            // 这里直接返回 APK assets 里固化的整页墙（离线可见、UI 归 APK 所有）。
            // 带 vid 的请求（/hai/poster/d/<vid>）落到下面的反代，取服务器详情 HTML。
            if ("/hai/poster/d/".equals(path) || "/hai/poster/d".equals(path)) {
                return wallPage(headers);
            }

            // ---- 同源反代 ----
            // 页面里所有 /hai/* 相对路径都落在 127.0.0.1:10090 同源：
            //   /hai/poster/?t=...&pg=2  整页 HTML（翻页/切类目，wall.js 用 DOMParser 取 #wallbox）
            //   /hai/poster/eps|play|cast|warm|progress   JSON/文本
            //   /hai/lib/haiplay.js                       站源脚本
            //   /hai/dbimg/view/photo/...                 豆瓣图（服务器带 Referer 轮试 4 域名）
            // 服务器只提供数据/渲染产物，APK 里零硬编码地址；后续逐个接口本地实现
            // 即可渐进裁撤服务器，UI 不用重打包。
            return relay(path, query, headers);
        } catch (Throwable t) {
            return new DownloadProxyServer.Response(500, "application/json; charset=utf-8",
                    "{\"ok\":false,\"msg\":\"" + escape(String.valueOf(t)) + "\"}");
        }
    }

    /** 从 APK assets 读内置墙页。缺失时降级反代服务器首页，保证首屏不黑。 */
    private DownloadProxyServer.Response wallPage(Map<String, String> headers) {
        java.io.InputStream in = null;
        try {
            in = ctx.getAssets().open(ProxyConfig.WALL_ASSET);
            byte[] b = readAll(in);
            DownloadProxyServer.Response r = new DownloadProxyServer.Response(
                    200, "text/html; charset=utf-8", b);
            r.extra.put("Cache-Control", "no-cache");
            return r;
        } catch (Throwable ignored) {
            return relay("/hai/poster/", "", headers);
        } finally {
            if (in != null) try { in.close(); } catch (Throwable ignored) {}
        }
    }

    /**
     * 同源反代到站源/配置中枢。method 与 POST body 由 DownloadProxyServer.serve()
     * 以 __method / __body（ISO-8859-1 逐字节可逆）塞进 headers，避免改接口签名。
     */
    private DownloadProxyServer.Response relay(String path, String query,
                                               Map<String, String> headers) {
        HttpURLConnection c = null;
        try {
            String url = ProxyConfig.UPSTREAM + path
                    + (query == null || query.length() == 0 ? "" : "?" + query);
            String method = headers.get("__method");
            if (method == null || method.length() == 0) method = "GET";
            byte[] reqBody = null;
            String enc = headers.get("__body");
            if (enc != null) reqBody = enc.getBytes("ISO-8859-1");

            c = (HttpURLConnection) new URL(url).openConnection();
            c.setInstanceFollowRedirects(false);   // 302 交给 WebView 自己跳（同源）
            c.setRequestMethod(method);
            c.setConnectTimeout(8000);
            c.setReadTimeout(25000);
            copyReq(headers, c, "referer", "cookie", "user-agent", "range",
                    "accept", "content-type", "accept-language", "x-requested-with");
            c.setRequestProperty("Accept-Encoding", "identity");  // 不解压，直接透传
            if (reqBody != null && reqBody.length > 0) {
                c.setDoOutput(true);
                c.setFixedLengthStreamingMode(reqBody.length);
                java.io.OutputStream os = c.getOutputStream();
                os.write(reqBody);
                os.close();
            }

            int code = c.getResponseCode();
            String ct = c.getContentType();
            if (ct == null || ct.length() == 0) ct = guessCt(path);
            java.io.InputStream in = code >= 400 ? c.getErrorStream() : c.getInputStream();
            byte[] out = in == null ? new byte[0] : readAll(in);

            // 服务器页面偶发带绝对自身地址；改成根相对路径，保持同源
            if (ct.startsWith("text/html") && out.length > 0) {
                String s = new String(out, "UTF-8");
                String up = ProxyConfig.UPSTREAM;
                if (s.indexOf(up) >= 0) {
                    s = s.replace(up, "");
                    out = s.getBytes("UTF-8");
                }
            }

            DownloadProxyServer.Response r = new DownloadProxyServer.Response(code, ct, out);
            String loc = c.getHeaderField("Location");
            if (loc != null) {
                if (loc.startsWith(ProxyConfig.UPSTREAM)) {
                    loc = loc.substring(ProxyConfig.UPSTREAM.length());
                }
                if (!loc.startsWith("/")) loc = "/" + loc;
                r.extra.put("Location", loc);        // /hai/poster/src 切源的 302
            }
            putResp(r, c, "Cache-Control", "ETag", "Set-Cookie", "Content-Disposition");
            return r;
        } catch (Throwable t) {
            return new DownloadProxyServer.Response(502, "text/plain; charset=utf-8",
                    "relay failed: " + t);
        } finally {
            if (c != null) c.disconnect();
        }
    }

    private static void copyReq(Map<String, String> h, HttpURLConnection c, String... keys) {
        for (String k : keys) {
            String v = h.get(k);
            if (v != null && v.length() > 0) c.setRequestProperty(k, v);
        }
    }

    private static void putResp(DownloadProxyServer.Response r, HttpURLConnection c,
                                String... keys) {
        for (String k : keys) {
            String v = c.getHeaderField(k);
            if (v != null && v.length() > 0) r.extra.put(k, v);
        }
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

    /** 豆瓣图直连必 418，必须带 Referer —— 服务端 /hai/dbimg/ 的同形替代。 */
    private DownloadProxyServer.Response image(String enc) {
        HttpURLConnection c = null;
        try {
            String url = java.net.URLDecoder.decode(enc, "UTF-8");
            if (TextUtils.isEmpty(url)) {
                return new DownloadProxyServer.Response(400, "text/plain", "empty url");
            }
            c = (HttpURLConnection) new URL(url).openConnection();
            c.setRequestProperty("Referer", "https://movie.douban.com/");
            c.setRequestProperty("User-Agent",
                    "Mozilla/5.0 (Linux; Android 10) AppleWebKit/537.36 Chrome/84 Mobile Safari/537.36");
            c.setConnectTimeout(8000);
            c.setReadTimeout(12000);
            int code = c.getResponseCode();
            if (code >= 400) {
                c.disconnect();
                return new DownloadProxyServer.Response(code, "text/plain", "upstream " + code);
            }
            InputStream in = c.getInputStream();
            java.io.ByteArrayOutputStream bos = new java.io.ByteArrayOutputStream();
            byte[] buf = new byte[8192];
            int n;
            while ((n = in.read(buf)) >= 0) bos.write(buf, 0, n);
            in.close();
            String ct = c.getContentType();
            DownloadProxyServer.Response r = new DownloadProxyServer.Response(
                    200, ct == null ? "image/jpeg" : ct, bos.toByteArray());
            r.extra.put("Cache-Control", "public, max-age=86400");
            return r;
        } catch (Throwable t) {
            return new DownloadProxyServer.Response(502, "text/plain", String.valueOf(t));
        } finally {
            if (c != null) c.disconnect();
        }
    }

    private static String escape(String s) {
        return s.replace("\\", "\\\\").replace("\"", "\\\"").replace("\n", " ");
    }
}
