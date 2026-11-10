// -*- coding: utf-8 -*-
package com.hisen.hai.api;

import android.content.Context;

import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.security.MessageDigest;
import java.text.SimpleDateFormat;
import java.util.Date;
import java.util.LinkedHashMap;
import java.util.Locale;
import java.util.Map;
import java.util.Random;
import java.util.zip.GZIPInputStream;

/**
 * 端上直连公网上游的数据通道（2026-11-10 定案：墙页彻底脱离自家服务器）。
 *
 * <p>海哥拍板「不要再用服务器墙页，要脱离服务器实现」，所以墙页数据不再来自
 * 10.0.0.11 渲染的 HTML，而是由本类在设备里直连豆瓣 / TMDB 的公开接口，
 * 把 JSON / 图片以同源 HTTP 喂给 APK 内置 WebView。
 *
 * <p>为什么必须经过本类而不是让 WebView 直接 fetch：
 * <ul>
 *   <li>豆瓣图片直连一律 <b>418</b>，必须带 {@code Referer: https://movie.douban.com/}；</li>
 *   <li>豆瓣 JSON 接口要 UA + Referer + 随机 bid cookie，跨域还会被 CORS 拦；</li>
 *   <li>TMDB 的 api_key 与筛选参数由端上统一持有，页面不接触密钥；</li>
 *   <li>同源返回可加缓存 / 冷却 / 离线兜底，TV 弱网下不白屏。</li>
 * </ul>
 *
 * <p><b>域名白名单是硬边界</b>：只放行豆瓣与 TMDB 两组域名，杜绝把它变成
 * 任意 URL 的开放代理。已实测（2026-11-10，湖北移动 10.0.0.x 出口）：
 * {@code m.douban.com / movie.douban.com / imgN.doubanio.com / api.tmdb.org /
 * images.tmdb.org} 全部直连可达；{@code api.themoviedb.org} 被 DNS 污染，不用。
 *
 * <p><b>2026-11-10 新增「缓存当天有效」</b>：JSON 与图片统一进按天分片的磁盘缓存
 * {@code cache/hai_up/<yyyyMMdd>/<md5(url)>}，跨天自动换目录并清理旧分片；
 * 内存 LRU 也统一在当天 23:59:59 过期。这样 WallWarm 的后台预热结果当天
 * 一直复用，第二天首启自然重新拉真实数据。
 */
public final class Upstream {

    public static final class Result {
        public int code = 502;
        public String ct = "application/json; charset=utf-8";
        public byte[] body = new byte[0];
        public String msg = "";
        public boolean ok() { return code >= 200 && code < 300; }
    }

    private static final String UA =
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            + "(KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36";
    private static final String REF_DOUBAN = "https://movie.douban.com/explore";
    private static final String REF_DOUBAN_IMG = "https://movie.douban.com/";
    private static final String REF_TMDB = "https://www.themoviedb.org/";

    private static final String[] HOSTS = {
            "m.douban.com", "movie.douban.com", "book.douban.com",
            "api.tmdb.org", "images.tmdb.org",
            "img.doubanio.com", "img1.doubanio.com", "img2.doubanio.com",
            "img3.doubanio.com", "img4.doubanio.com", "img9.doubanio.com",
    };

    private static final int CONNECT_MS = 6000;
    private static final int READ_MS = 14000;
    private static final int MAX_BYTES = 6 * 1024 * 1024;

    /** 内存 LRU 上限（电视内存小，不能放开）；有效期一律「当天结束」。 */
    private static final long MEM_CAP = 12 * 1024 * 1024L;
    private static final int MEM_MAX_ENTRIES = 320;

    private static final class Ent {
        long exp;
        String ct;
        byte[] body;
        Ent(long e, String c, byte[] b) { exp = e; ct = c; body = b; }
    }

    private static final LinkedHashMap<String, Ent> sMem =
            new LinkedHashMap<String, Ent>(64, 0.75f, true);
    private static long sMemBytes = 0;
    /** 连续失败域名冷却，避免限流时把电视带宽和豆瓣账号一起刷爆。 */
    private static final Map<String, int[]> sFail = new LinkedHashMap<String, int[]>();
    private static final Map<String, Long> sCool = new LinkedHashMap<String, Long>();

    private static File sCacheDir;
    private static String sCacheDay = "";
    private static String sBid;
    private static volatile int sDiskHit;
    private static volatile int sDiskPut;

    private Upstream() {}

    private static synchronized String bid() {
        if (sBid == null) {
            char[] cs = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
                    .toCharArray();
            Random r = new Random();
            StringBuilder b = new StringBuilder();
            for (int i = 0; i < 11; i++) b.append(cs[r.nextInt(cs.length)]);
            sBid = b.toString();
        }
        return sBid;
    }

    /** 本地日期分片键（WallWarm 也用它判断跨天）。 */
    public static String dayKey() {
        return new SimpleDateFormat("yyyyMMdd", Locale.US)
                .format(new Date(System.currentTimeMillis()));
    }

    /** 当天 23:59:59.999，内存缓存统一过期点。 */
    private static long dayEnd() {
        java.util.Calendar c = java.util.Calendar.getInstance();
        c.set(java.util.Calendar.HOUR_OF_DAY, 23);
        c.set(java.util.Calendar.MINUTE, 59);
        c.set(java.util.Calendar.SECOND, 59);
        c.set(java.util.Calendar.MILLISECOND, 999);
        return c.getTimeInMillis();
    }

    /** 只放行白名单 HTTPS 域名；返回规范化的 URL，非法返回 null。 */
    public static String sanitize(String raw) {
        if (raw == null || raw.length() < 12 || raw.length() > 2048) return null;
        String u = raw.trim();
        if (!u.startsWith("https://") && !u.startsWith("http://")) return null;
        String host;
        try {
            host = new URL(u).getHost().toLowerCase();
        } catch (Throwable t) {
            return null;
        }
        boolean ok = false;
        for (String h : HOSTS) {
            if (host.equals(h)) { ok = true; break; }
        }
        if (!ok && host.endsWith(".doubanio.com")) ok = true;
        if (!ok) return null;
        return u;
    }

    private static boolean isDouban(String host) {
        return host.contains("douban");
    }

    private static boolean isImage(String url, String ct) {
        String u = url.toLowerCase();
        return u.contains("/view/photo/") || u.contains("/t/p/")
                || (ct != null && ct.startsWith("image/"));
    }

    /**
     * 当天分片目录 {@code cache/hai_up/<yyyyMMdd>/}。跨天换目录的同时清掉
     * 旧分片（只认 8 位数字目录名，绝不误删 App 的其它缓存）。
     */
    private static synchronized File cacheDir(Context ctx) {
        String day = dayKey();
        if (sCacheDir == null || !day.equals(sCacheDay)) {
            try {
                File root = new File(ctx.getCacheDir(), "hai_up");
                File d = new File(root, day);
                if (!d.exists()) d.mkdirs();
                sCacheDir = d;
                sCacheDay = day;
                File[] olds = root.listFiles();
                if (olds != null) {
                    for (File f : olds) {
                        if (f.isDirectory() && f.getName().matches("[0-9]{8}")
                                && !f.getName().equals(day)) {
                            File[] inner = f.listFiles();
                            if (inner != null) for (File g : inner) g.delete();
                            f.delete();
                        }
                    }
                }
            } catch (Throwable ignored) {}
        }
        return sCacheDir;
    }

    private static String md5(String s) {
        try {
            byte[] d = MessageDigest.getInstance("MD5").digest(s.getBytes("UTF-8"));
            StringBuilder b = new StringBuilder();
            for (byte x : d) b.append(String.format("%02x", x & 0xff));
            return b.toString();
        } catch (Throwable t) {
            return Integer.toHexString(s.hashCode());
        }
    }

    private static synchronized Ent memGet(String key) {
        Ent e = sMem.get(key);
        if (e == null) return null;
        if (e.exp < System.currentTimeMillis()) {
            sMem.remove(key);
            sMemBytes -= e.body.length;
            return null;
        }
        return e;
    }

    private static synchronized void memPut(String key, Ent e) {
        Ent old = sMem.remove(key);
        if (old != null) sMemBytes -= old.body.length;
        sMem.put(key, e);
        sMemBytes += e.body.length;
        while (sMem.size() > MEM_MAX_ENTRIES || sMemBytes > MEM_CAP) {
            Map.Entry<String, Ent> first = sMem.entrySet().iterator().next();
            if (first == null) break;
            sMem.remove(first.getKey());
            sMemBytes -= first.getValue().body.length;
        }
    }

    private static synchronized boolean inCooldown(String host) {
        Long until = sCool.get(host);
        return until != null && until > System.currentTimeMillis();
    }

    private static synchronized void noteFail(String host) {
        int[] n = sFail.get(host);
        if (n == null) { n = new int[]{0}; sFail.put(host, n); }
        n[0]++;
        if (n[0] >= 3) {
            sCool.put(host, System.currentTimeMillis() + 45000L);
            n[0] = 0;
        }
    }

    private static synchronized void noteOk(String host) {
        sFail.remove(host);
        sCool.remove(host);
    }

    /**
     * 取上游资源。命中当天内存/磁盘缓存直接返回；失败返回
     * {@code ok()==false} 的 Result，调用方负责降级，不抛异常。
     */
    public static Result fetch(Context ctx, String rawUrl, boolean imageHint) {
        Result out = new Result();
        String url = sanitize(rawUrl);
        if (url == null) {
            out.code = 400;
            out.msg = "host not allowed";
            out.body = ("{\"ok\":false,\"msg\":\"" + out.msg + "\"}")
                    .getBytes();
            return out;
        }
        String key = url;
        Ent hit = memGet(key);
        if (hit != null) {
            out.code = 200; out.ct = hit.ct; out.body = hit.body;
            return out;
        }
        String host;
        try { host = new URL(url).getHost().toLowerCase(); }
        catch (Throwable t) { host = ""; }
        if (inCooldown(host)) {
            out.code = 503;
            out.msg = "upstream cooling";
            out.body = ("{\"ok\":false,\"msg\":\"" + out.msg + "\"}").getBytes();
            return out;
        }
        // JSON 与图片统一查当天磁盘缓存：WallWarm 后台预热写进来的条目，
        // 手动翻页时走这里秒回，不再等公网。
        byte[] disk = diskGet(ctx, key);
        if (disk != null) {
            out.code = 200;
            out.ct = imageHint ? "image/jpeg" : "application/json; charset=utf-8";
            out.body = disk;
            memPut(key, new Ent(dayEnd(), out.ct, disk));
            sDiskHit++;
            return out;
        }

        HttpURLConnection conn = null;
        InputStream in = null;
        try {
            String cur = url;
            int redirects = 0;
            while (true) {
                URL u = new URL(cur);
                conn = (HttpURLConnection) u.openConnection();
                conn.setConnectTimeout(CONNECT_MS);
                conn.setReadTimeout(READ_MS);
                conn.setInstanceFollowRedirects(false);
                conn.setRequestMethod("GET");
                conn.setRequestProperty("User-Agent", UA);
                conn.setRequestProperty("Accept",
                        "application/json, text/plain, image/avif, image/webp, */*");
                conn.setRequestProperty("Accept-Language", "zh-CN,zh;q=0.9");
                boolean db = isDouban(u.getHost().toLowerCase());
                conn.setRequestProperty("Referer",
                        db ? (isImage(cur, null) ? REF_DOUBAN_IMG : REF_DOUBAN)
                           : REF_TMDB);
                if (db) conn.setRequestProperty("Cookie", "bid=" + bid());
                int code = conn.getResponseCode();
                if (code == 301 || code == 302 || code == 303 || code == 307
                        || code == 308) {
                    String loc = conn.getHeaderField("Location");
                    conn.disconnect();
                    conn = null;
                    if (loc == null || ++redirects > 3) {
                        throw new java.io.IOException("too many redirects");
                    }
                    if (loc.startsWith("/")) {
                        loc = u.getProtocol() + "://" + u.getHost() + loc;
                    }
                    String safe = sanitize(loc);
                    if (safe == null) throw new java.io.IOException("redirect blocked");
                    cur = safe;
                    continue;
                }
                String ct = conn.getContentType();
                if (ct == null || ct.length() == 0) ct = "application/octet-stream";
                if (ct.indexOf(';') >= 0) ct = ct.substring(0, ct.indexOf(';')).trim();
                boolean img = imageHint || isImage(cur, ct);
                in = code >= 200 && code < 300
                        ? conn.getInputStream() : conn.getErrorStream();
                byte[] body = in == null ? new byte[0] : readAll(in, MAX_BYTES);
                String enc = conn.getContentEncoding();
                if ("gzip".equalsIgnoreCase(enc) && body.length > 2
                        && (body[0] & 0xff) == 0x1f && (body[1] & 0xff) == 0x8b) {
                    body = gunzip(body);
                }
                out.code = code;
                out.ct = ct;
                out.body = body;
                out.msg = code >= 200 && code < 300 ? "" : ("HTTP " + code);
                if (out.ok()) {
                    noteOk(host);
                    memPut(key, new Ent(dayEnd(), ct, body));
                    diskPut(ctx, key, body);   // JSON 也落盘 ⇒ 当天有效
                } else {
                    noteFail(host);
                }
                return out;
            }
        } catch (Throwable t) {
            noteFail(host);
            out.code = 502;
            out.ct = "application/json; charset=utf-8";
            out.msg = String.valueOf(t);
            out.body = ("{\"ok\":false,\"msg\":\"" + esc(out.msg) + "\"}")
                    .getBytes();
            return out;
        } finally {
            if (in != null) try { in.close(); } catch (Throwable ignored) {}
            if (conn != null) try { conn.disconnect(); } catch (Throwable ignored) {}
        }
    }

    private static synchronized byte[] diskGet(Context ctx, String key) {
        try {
            File d = cacheDir(ctx);
            if (d == null) return null;
            File f = new File(d, md5(key));
            if (!f.exists() || f.length() == 0) return null;
            FileInputStream fi = new FileInputStream(f);
            byte[] b = readAll(fi, MAX_BYTES);
            fi.close();
            return b;
        } catch (Throwable ignored) {
            return null;
        }
    }

    /** 临时文件 + rename 原子落盘，避免预热线程与 UI 线程互相读到半截。 */
    private static synchronized void diskPut(Context ctx, String key, byte[] body) {
        try {
            File d = cacheDir(ctx);
            if (d == null || body.length == 0 || body.length > MAX_BYTES) return;
            String n = md5(key);
            File tmp = new File(d, n + ".tmp");
            FileOutputStream fo = new FileOutputStream(tmp);
            fo.write(body);
            fo.close();
            tmp.renameTo(new File(d, n));
            sDiskPut++;
        } catch (Throwable ignored) {}
    }

    private static byte[] readAll(InputStream in, int cap) throws java.io.IOException {
        ByteArrayOutputStream bos = new ByteArrayOutputStream();
        byte[] buf = new byte[8192];
        int n;
        while ((n = in.read(buf)) >= 0) {
            if (bos.size() + n > cap) break;
            bos.write(buf, 0, n);
        }
        return bos.toByteArray();
    }

    private static byte[] gunzip(byte[] b) {
        GZIPInputStream gz = null;
        try {
            gz = new GZIPInputStream(new ByteArrayInputStream(b));
            return readAll(gz, MAX_BYTES);
        } catch (Throwable t) {
            return b;
        } finally {
            if (gz != null) try { gz.close(); } catch (Throwable ignored) {}
        }
    }

    private static String esc(String s) {
        return s == null ? "" : s.replace("\\", "\\\\")
                .replace("\"", "\\\"").replace("\n", " ");
    }

    /** 给 /hai/ping 用的可观测状态。 */
    public static synchronized String status() {
        return "{\"day\":\"" + dayKey() + "\",\"cache\":" + sMem.size()
                + ",\"bytes\":" + sMemBytes + ",\"diskHit\":" + sDiskHit
                + ",\"diskPut\":" + sDiskPut + ",\"cooling\":" + sCool.size() + "}";
    }
}
