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

    private final Context ctx;

    public HaiApiServer(Context c) { this.ctx = c; }

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
            if (path.startsWith("/hai/dbimg/")) {
                return image(path.substring("/hai/dbimg/".length()));
            }
            return new DownloadProxyServer.Response(404, "application/json; charset=utf-8",
                    "{\"ok\":false,\"msg\":\"unknown " + escape(path) + "\"}");
        } catch (Throwable t) {
            return new DownloadProxyServer.Response(500, "application/json; charset=utf-8",
                    "{\"ok\":false,\"msg\":\"" + escape(String.valueOf(t)) + "\"}");
        }
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

    /** 读取 App 原生 Config 表：type=0 + json 非空 + time 最新 = 激活配置。 */
    private JSONObject readActiveConfig() {
        SQLiteDatabase db = null;
        Cursor c = null;
        try {
            String p = "/data/user_de/0/com.hisen.android.tv/databases/tv";
            db = SQLiteDatabase.openDatabase(p, null, SQLiteDatabase.OPEN_READONLY);
            c = db.rawQuery("select id,type,url,time,json from Config "
                    + "where type=0 and json is not null and length(json)>0 "
                    + "order by time desc limit 1", null);
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
                        o.put("searchable", s.optInt("searchable", 1));
                        o.put("changeable", s.optInt("changeable", 1));
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
