// -*- coding: utf-8 -*-
package com.hisen.hai.proxy;

import java.io.BufferedInputStream;
import java.io.BufferedOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.net.ServerSocket;
import java.net.Socket;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.atomic.AtomicBoolean;

/**
 * 本地 HTTP 服务：一个端口接管 PG 原版 go_proxy_video 的全部代理路由。
 *
 * <p>原版是 APK assets 里的 Go ELF（AArch64, 9.48MB），由 App 按需启动；
 * 这里用纯 Java 复刻，放进 pg.jar 的 classes2.dex，因此可随 jar 热更新、完全可控。
 *
 * <p>路由（与 jar 内 R/a.smali 及真机 /media 抓包逐字对齐）：
 * <pre>
 *   GET /ping                                       健康检查（R/a.d() 探测用）
 *   GET /p/&lt;threads&gt;/&lt;hdr&gt;/&lt;chunk&gt;/&lt;b64url&gt;/&lt;name&gt;   分片加速（32/null/1048576/...）
 *   GET /p/0/null/&lt;urlEncoded&gt;                    R/a.c() 的单路转发
 *   GET /cfgo/null/&lt;urlEncoded&gt;                    R/a.b() 的普通请求转发
 *   GET /cfgo/&lt;hdrEncoded&gt;/&lt;urlEncoded&gt;           同上，带自定义头
 *   GET /hai/*                                      同形 UI 数据接口（APK 内置 HTML 用）
 * </pre>
 */
public final class DownloadProxyServer {

    private static final String CRLF = "\r\n";
    private static volatile DownloadProxyServer sInstance;
    private final AtomicBoolean running = new AtomicBoolean(false);
    private final ExecutorService conns = Executors.newCachedThreadPool();
    private ServerSocket server;
    private int port = ProxyConfig.HAI_PORT;

    /** 外部 API 处理器（HaiApiServer），用接口解耦，避免编译期强依赖。 */
    public interface ApiHandler {
        /** 返回 null 表示不归我管。 */
        Response handle(String path, String query, Map<String, String> headers);
    }

    public static final class Response {
        public int code = 200;
        public String contentType = "application/json; charset=utf-8";
        public byte[] body = new byte[0];
        public Map<String, String> extra = new LinkedHashMap<String, String>();
        public Response(int c, String ct, String text) {
            code = c; contentType = ct;
            try { body = text.getBytes("UTF-8"); } catch (Exception e) { body = text.getBytes(); }
        }
        public Response(int c, String ct, byte[] b) { code = c; contentType = ct; body = b; }
    }

    private volatile ApiHandler api;

    private DownloadProxyServer() {}

    public static DownloadProxyServer get() {
        if (sInstance == null) {
            synchronized (DownloadProxyServer.class) {
                if (sInstance == null) sInstance = new DownloadProxyServer();
            }
        }
        return sInstance;
    }

    public void setApiHandler(ApiHandler h) { this.api = h; }

    /**
     * 启动服务。2026-11-09 架构定案：Java 代理<b>只</b>服务「APK 内置电视墙 UI」
     * 这条同源链路（127.0.0.1:10090），播放取流仍走 PG 原版 Go（10078→10079）。
     * 设备上的 uid-owner REDIRECT 已删除，两条链路彻底解耦。
     */
    public synchronized int start() {
        if (running.get()) return port;
        int p = ProxyConfig.HAI_PORT;
        // 冷启动偶发：上一进程被 force-stop 后 10090 仍处 TIME_WAIT，单次 bind 失败
        // 会让 WebView 加载不到墙页 → 永久黑屏。显式 SO_REUSEADDR + 8 次 × 250ms 重试。
        for (int i = 0; i < 8 && server == null; i++) {
            try {
                ServerSocket s = new ServerSocket();
                s.setReuseAddress(true);
                s.bind(new InetSocketAddress(InetAddress.getByName("127.0.0.1"), p), 64);
                server = s;
            } catch (Throwable t) {
                try { Thread.sleep(250L); } catch (InterruptedException ignored) {}
            }
        }
        if (server == null) return -1;
        port = p;
        running.set(true);
        ProxyConfig.sBase = "http://127.0.0.1:" + port;
        Thread t = new Thread(new Runnable() {
            public void run() { acceptLoop(); }
        }, "hai-download-proxy");
        t.setDaemon(true);
        t.start();
        return port;
    }

    public synchronized void stop() {
        running.set(false);
        try { if (server != null) server.close(); } catch (IOException ignored) {}
        conns.shutdownNow();
        sInstance = null;
    }

    public int port() { return port; }
    public boolean isRunning() { return running.get(); }

    private void acceptLoop() {
        while (running.get()) {
            try {
                final Socket s = server.accept();
                s.setTcpNoDelay(true);
                conns.execute(new Runnable() {
                    public void run() { serve(s); }
                });
            } catch (IOException e) {
                if (running.get()) continue;
                break;
            }
        }
    }

    private void serve(Socket sock) {
        InputStream in = null;
        OutputStream out = null;
        try {
            in = new BufferedInputStream(sock.getInputStream(), 8192);
            out = new BufferedOutputStream(sock.getOutputStream(), 64 * 1024);

            // 请求行
            String line = readLine(in);
            if (line == null || line.length() == 0) return;
            String[] parts = line.split(" ");
            if (parts.length < 2) return;
            String method = parts[0].toUpperCase();
            String target = parts[1];

            // 请求头
            Map<String, String> headers = new LinkedHashMap<String, String>();
            while (true) {
                String h = readLine(in);
                if (h == null || h.length() == 0) break;
                int i = h.indexOf(':');
                if (i > 0) headers.put(h.substring(0, i).trim().toLowerCase(), h.substring(i + 1).trim());
            }

            int q = target.indexOf('?');
            String path = q >= 0 ? target.substring(0, q) : target;
            String query = q >= 0 ? target.substring(q + 1) : "";

            // 反代需要 method 与 POST body。ISO-8859-1 逐字节可逆，
            // 二进制 body 也不会被改坏（HaiApiServer.relay 用同编码还原）。
            headers.put("__method", method);
            String cl = headers.get("content-length");
            int len = parseInt(cl, 0);
            if (len > 0 && len <= 4 * 1024 * 1024 && !"GET".equals(method) && !"HEAD".equals(method)) {
                byte[] body = new byte[len];
                int off = 0;
                while (off < len) {
                    int n = in.read(body, off, len - off);
                    if (n < 0) break;
                    off += n;
                }
                if (off > 0) {
                    headers.put("__body", new String(body, 0, off, "ISO-8859-1"));
                }
            }

            if ("/ping".equals(path)) {
                writeSimple(out, 200, "text/plain", "OK", method);
                return;
            }
            if (path.startsWith("/hai/")) {
                ApiHandler h = api;
                Response r = h == null ? null : h.handle(path, query, headers);
                if (r == null) r = new Response(404, "application/json; charset=utf-8", "{\"ok\":false,\"msg\":\"no handler\"}");
                writeResponse(out, method, r.code, r.contentType, r.body, r.extra, r.body.length, -1, null);
                return;
            }
            if (path.startsWith("/p/")) {
                serveP(out, method, path, headers);
                return;
            }
            if (path.startsWith("/cfgo/")) {
                serveCfgo(out, method, path, headers);
                return;
            }
            // /yt/* 与 /av3am3u/* 先按普通转发处理，后续按需要补专门实现
            if (path.startsWith("/yt/") || path.startsWith("/av3am3u/")) {
                writeSimple(out, 501, "text/plain", "not implemented yet", method);
                return;
            }
            writeSimple(out, 404, "text/plain", "not found", method);
        } catch (Throwable t) {
            try {
                writeSimple(out, 500, "text/plain", "proxy error: " + t, "GET");
            } catch (Throwable ignored) {}
        } finally {
            try { if (out != null) out.flush(); } catch (Throwable ignored) {}
            try { sock.close(); } catch (IOException ignored) {}
        }
    }

    /** /p/ 分片加速 或 /p/0/ 单路转发。 */
    private void serveP(OutputStream out, String method, String path,
                        Map<String, String> headers) throws Exception {
        String[] seg = path.split("/");   // ["", "p", ...]
        // seg[0] 空, seg[1]="p"
        if (seg.length < 4) { writeSimple(out, 400, "text/plain", "bad /p/ path", method); return; }

        int threads = parseInt(seg[2], 0);
        String hdrEnc = seg[3];
        Map<String, String> extra = ProxyConfig.parseHeaders(hdrEnc);

        String realUrl;
        String name = "video";
        int chunk = ProxyConfig.DEFAULT_CHUNK;
        boolean split = false;

        if (seg.length >= 6 && isDigits(seg[4]) && parseInt(seg[4], 0) >= 64 * 1024) {
            // /p/32/null/1048576/<b64url>/<filename>
            chunk = parseInt(seg[4], ProxyConfig.DEFAULT_CHUNK);
            realUrl = ProxyConfig.b64Decode(seg[5]);
            StringBuilder nm = new StringBuilder();
            for (int i = 6; i < seg.length; i++) {
                if (nm.length() > 0) nm.append('/');
                nm.append(ProxyConfig.urlDecode(seg[i]));
            }
            name = nm.length() > 0 ? nm.toString() : "video";
            split = threads > 0;
        } else {
            // /p/0/null/<urlEncoded>  （jar 内 R/a.c() 造的）
            StringBuilder u = new StringBuilder();
            for (int i = 4; i < seg.length; i++) {
                if (u.length() > 0) u.append('/');
                u.append(seg[i]);
            }
            realUrl = ProxyConfig.urlDecode(u.toString());
        }
        if (realUrl == null || realUrl.length() == 0) {
            writeSimple(out, 400, "text/plain", "empty target", method);
            return;
        }

        long start = rangeStart(headers.get("range"));
        int window = Math.max(threads * 2, ProxyConfig.DEFAULT_WINDOW);
        RangeStream.Result r = split
                ? RangeStream.open(realUrl, extra, start, threads, chunk, window)
                : RangeStream.open(realUrl, extra, start, 1, chunk, 2);   // 单路，不并发

        Map<String, String> ex = new LinkedHashMap<String, String>();
        ex.put("Accept-Ranges", "bytes");
        long total = r.meta.total;
        if (total > 0 && start < total) {
            ex.put("Content-Range", "bytes " + start + "-" + (total - 1) + "/" + total);
        }
        int code = (start > 0 || total > 0) ? 206 : 200;
        writeResponse(out, method, code, ProxyConfig.mimeOf(name), null, ex, r.length, 0, r.in);
        try { r.in.close(); } catch (Throwable ignored) {}
    }

    /** /cfgo/ 普通请求转发：爬虫/配置请求走这里，原样透传 header 与响应体。 */
    private void serveCfgo(OutputStream out, String method, String path,
                           Map<String, String> headers) throws Exception {
        String[] seg = path.split("/");
        if (seg.length < 4) { writeSimple(out, 400, "text/plain", "bad /cfgo/ path", method); return; }
        String hdrEnc = "null".equals(seg[2]) ? "" : seg[2];
        Map<String, String> extra = ProxyConfig.parseHeaders(hdrEnc);
        StringBuilder u = new StringBuilder();
        for (int i = 3; i < seg.length; i++) {
            if (u.length() > 0) u.append('/');
            u.append(seg[i]);
        }
        String realUrl = ProxyConfig.urlDecode(u.toString());
        if (realUrl == null || realUrl.length() == 0) {
            writeSimple(out, 400, "text/plain", "empty target", method);
            return;
        }
        // 复用单路流，保证 Range / Cookie / UA 全部透传
        RangeStream.Result r = RangeStream.open(realUrl, extra, 0, 1, ProxyConfig.DEFAULT_CHUNK, 2);
        Map<String, String> ex = new LinkedHashMap<String, String>();
        writeResponse(out, method, 200, "application/octet-stream", null, ex, r.length, 0, r.in);
        try { r.in.close(); } catch (Throwable ignored) {}
    }

    private static long rangeStart(String v) {
        if (v == null) return 0;
        try {
            String s = v.trim();
            int eq = s.indexOf('=');
            int dash = s.indexOf('-');
            if (eq > 0 && dash > eq) return Long.parseLong(s.substring(eq + 1, dash).trim());
        } catch (Throwable ignored) {}
        return 0;
    }

    private static boolean isDigits(String s) {
        if (s == null || s.length() == 0) return false;
        for (int i = 0; i < s.length(); i++) if (!Character.isDigit(s.charAt(i))) return false;
        return true;
    }

    private static int parseInt(String s, int dflt) {
        try { return Integer.parseInt(s); } catch (Throwable t) { return dflt; }
    }

    private static String readLine(InputStream in) throws IOException {
        StringBuilder sb = new StringBuilder();
        int c;
        while ((c = in.read()) >= 0) {
            if (c == '\n') {
                int n = sb.length();
                if (n > 0 && sb.charAt(n - 1) == '\r') sb.setLength(n - 1);
                return sb.toString();
            }
            sb.append((char) c);
            if (sb.length() > 8192) break;
        }
        return sb.length() == 0 ? null : sb.toString();
    }

    private static void writeSimple(OutputStream out, int code, String ct, String text, String method)
            throws IOException {
        byte[] b;
        try { b = text.getBytes("UTF-8"); } catch (Exception e) { b = text.getBytes(); }
        writeResponse(out, method, code, ct, b, null, b.length, 0, null);
    }

    /**
     * 统一响应写入。body 与 stream 二选一；stream 非空时按 64KB 缓冲持续输出。
     */
    private static void writeResponse(OutputStream out, String method, int code, String ct,
                                      byte[] body, Map<String, String> extra,
                                      long contentLength, long unused, InputStream stream)
            throws IOException {
        StringBuilder h = new StringBuilder();
        h.append("HTTP/1.1 ").append(code).append(' ').append(reason(code)).append(CRLF);
        h.append("Content-Type: ").append(ct).append(CRLF);
        if (contentLength >= 0) h.append("Content-Length: ").append(contentLength).append(CRLF);
        h.append("Accept-Ranges: bytes").append(CRLF);
        h.append("Connection: close").append(CRLF);
        h.append("Access-Control-Allow-Origin: *").append(CRLF);
        if (extra != null) {
            for (Map.Entry<String, String> e : extra.entrySet()) {
                if ("Accept-Ranges".equalsIgnoreCase(e.getKey())) continue;
                h.append(e.getKey()).append(": ").append(e.getValue()).append(CRLF);
            }
        }
        h.append(CRLF);
        out.write(h.toString().getBytes("UTF-8"));
        if ("HEAD".equals(method)) { out.flush(); return; }
        if (stream != null) {
            byte[] buf = new byte[64 * 1024];
            int n;
            while ((n = stream.read(buf)) >= 0) {
                if (n == 0) continue;
                out.write(buf, 0, n);
            }
        } else if (body != null && body.length > 0) {
            out.write(body);
        }
        out.flush();
    }

    private static String reason(int c) {
        if (c == 200) return "OK";
        if (c == 206) return "Partial Content";
        if (c == 301) return "Moved Permanently";
        if (c == 302) return "Found";
        if (c == 304) return "Not Modified";
        if (c == 400) return "Bad Request";
        if (c == 404) return "Not Found";
        if (c == 500) return "Internal Server Error";
        if (c == 501) return "Not Implemented";
        if (c == 502) return "Bad Gateway";
        return "OK";
    }
}
