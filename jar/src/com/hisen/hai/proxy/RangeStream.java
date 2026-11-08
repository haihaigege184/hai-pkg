// -*- coding: utf-8 -*-
package com.hisen.hai.proxy;

import java.io.IOException;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.util.Arrays;
import java.util.Map;
import java.util.concurrent.Callable;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Future;
import java.util.concurrent.LinkedBlockingQueue;
import java.util.concurrent.ThreadPoolExecutor;
import java.util.concurrent.TimeUnit;

/**
 * R 加速代理核心：把一条真实直链变成「边下边播」的连续流。
 *
 * <p><b>2026-11-08 逆向修正（重要）</b>：原版 /p/32/null/1048576/ 里的 32
 * <b>不是线程数</b>，而是 {@code NanoHTTPD$Constants.DEFAULT_BUFFER_SEGMENTS = 0x20}
 * —— 32 个 1MB 缓冲段。当前 APK 的 {@code HaiApiService} 注释原文
 * 「多线程下载…实测反而更慢，已禁用」，实际是<b>单连接顺序流式透传 + 大缓冲预读</b>：
 * <ol>
 *   <li><b>零等待起播</b>：请求一进来立刻向 CDN 发起单连接 GET（带 Range），
 *       上游第一个字节到达就往播放器写，不等整片下载。</li>
 *   <li><b>大缓冲预读</b>：独立预读线程把上游流灌进 8MB 管道（原版 32×1MB 段），
 *       吸收网络抖动，让播放器读取端始终有数据 —— 峰值 50MB/s 由此而来。</li>
 *   <li><b>内存恒定</b>：不管 4K 还是 100GB 原盘，缓冲恒定，绝不全量缓存。</li>
 *   <li><b>天然背压</b>：管道满则预读线程阻塞，不会吃满带宽和内存。</li>
 *   <li><b>seek 友好</b>：播放器带 Range: bytes=N- 重连，直接把 N 透传给上游。</li>
 * </ol>
 *
 * <p>默认 {@link ProxyConfig#MODE_STREAM} 复刻原版行为。
 * 另保留 {@link ProxyConfig#MODE_CONCURRENT} 作为<b>可选</b>加速：按 1MB 对齐切片、
 * N 路并发 Range、Future 按序拼流、滑动窗口限在途片数 —— 这是 11-04 自研 HaiDp
 * （8 路 × 4MB）实测 11.5MB/s 的那套，用于单连接被限速的 CDN，不是原版默认。
 */
public final class RangeStream {

    /** 全进程共享一个池。多个播放并发时由窗口限流，不会无限建线程。 */
    private static volatile ExecutorService sPool;

    private static ExecutorService pool(int threads) {
        if (sPool == null) {
            synchronized (RangeStream.class) {
                if (sPool == null) {
                    // 池固定 32 线程：并发度由滑动窗口控制，不能让第一次 /p/0/ 请求
                    // 把池永久缩成 1 线程，否则后续 32 路加速全部失效。
                    int n = ProxyConfig.DEFAULT_THREADS;
                    ThreadPoolExecutor p = new ThreadPoolExecutor(
                            n, n, 60L, TimeUnit.SECONDS,
                            new LinkedBlockingQueue<Runnable>(),
                            new ThreadPoolExecutor.CallerRunsPolicy());
                    p.allowCoreThreadTimeOut(true);
                    sPool = p;
                }
            }
        }
        return sPool;
    }

    public static final class Meta {
        public final long total;       // -1 = 未知
        public final boolean ranged;   // CDN 是否支持 Range
        public Meta(long t, boolean r) { total = t; ranged = r; }
    }

    public static final class Result {
        public final Meta meta;
        public final InputStream in;
        public final long length;      // 本次响应 Content-Length，-1 表示 chunked
        Result(Meta m, InputStream i, long l) { meta = m; in = i; length = l; }
    }

    private RangeStream() {}

    /** 探测总长与 Range 支持。三种兜底，任何一种成立都能起播。 */
    public static Meta probe(String url, Map<String, String> headers) {
        HttpURLConnection c = null;
        try {
            c = connect(url, headers);
            c.setRequestProperty("Range", "bytes=0-0");
            int code = c.getResponseCode();
            String cr = c.getHeaderField("Content-Range");
            if (code == 206 && cr != null) {
                int i = cr.lastIndexOf('/');
                if (i > 0) {
                    try {
                        long t = Long.parseLong(cr.substring(i + 1).trim());
                        return new Meta(t, true);
                    } catch (NumberFormatException ignored) {}
                }
            }
        } catch (Throwable ignored) {
        } finally {
            if (c != null) c.disconnect();
        }
        // 兜底 1：HEAD
        try {
            HttpURLConnection h = (HttpURLConnection) new URL(url).openConnection();
            apply(h, headers);
            h.setRequestMethod("HEAD");
            h.setConnectTimeout(ProxyConfig.CONNECT_TIMEOUT_MS);
            h.setReadTimeout(ProxyConfig.READ_TIMEOUT_MS);
            long len = h.getHeaderFieldLong("Content-Length", -1);
            String ar = h.getHeaderField("Accept-Ranges");
            h.disconnect();
            if (len > 0) return new Meta(len, "bytes".equalsIgnoreCase(ar));
        } catch (Throwable ignored) {}
        // 兜底 2：未知长度，走单路直连（不并发），保证仍能播
        return new Meta(-1, false);
    }

    private static HttpURLConnection connect(String url, Map<String, String> headers) throws IOException {
        HttpURLConnection c = (HttpURLConnection) new URL(url).openConnection();
        apply(c, headers);
        c.setConnectTimeout(ProxyConfig.CONNECT_TIMEOUT_MS);
        c.setReadTimeout(ProxyConfig.READ_TIMEOUT_MS);
        c.setInstanceFollowRedirects(true);
        return c;
    }

    private static void apply(HttpURLConnection c, Map<String, String> headers) {
        // UA 必须透传：百度 dlink 要求长串 netdisk UA，短串直接 403（见 11-04 实证）
        c.setRequestProperty("User-Agent", "netdisk;P2SP;3.0.0.8;netdisk;11.12.3;android-android;10.0");
        if (headers != null) {
            for (Map.Entry<String, String> e : headers.entrySet()) {
                c.setRequestProperty(e.getKey(), e.getValue());
            }
        }
    }

    /**
     * 打开一条从 start 开始的流。
     * @param ranged false 或 total<0 时退化为单路直连（正确性优先，不假装加速）
     */
    /** 统一入口：默认复刻原版流式；sMode=CONCURRENT 时走可选并发加速。 */
    public static Result open(final String url, final Map<String, String> headers,
                              long start, int threads, int chunk, int window) throws IOException {
        if (ProxyConfig.sMode != ProxyConfig.MODE_CONCURRENT) {
            return openStream(url, headers, start);
        }
        return openConcurrent(url, headers, start, threads, chunk, window);
    }

    /**
     * 原版等价模式：单连接流式透传 + 独立预读线程灌大缓冲管道。
     * 不额外发 probe（原版播放请求只有一条），从本条响应的 Content-Range 取总长。
     */
    public static Result openStream(final String url, final Map<String, String> headers,
                                    long start) throws IOException {
        if (start < 0) start = 0;
        final HttpURLConnection c = connect(url, headers);
        if (start > 0) c.setRequestProperty("Range", "bytes=" + start + "-");
        int code = c.getResponseCode();
        if (code >= 400) {
            c.disconnect();
            throw new IOException("upstream " + code);
        }
        long len = c.getHeaderFieldLong("Content-Length", -1);
        String cr = c.getHeaderField("Content-Range");
        long total = -1;
        boolean ranged = false;
        if (code == 206 && cr != null) {
            int i = cr.lastIndexOf('/');
            if (i > 0) {
                try { total = Long.parseLong(cr.substring(i + 1).trim()); ranged = true; }
                catch (NumberFormatException ignored) {}
            }
        } else if (len > 0) {
            total = start + len;
        }
        Meta meta = new Meta(total, ranged);

        final java.io.PipedInputStream pin = new java.io.PipedInputStream(ProxyConfig.PIPE_BUFFER);
        final java.io.PipedOutputStream pout = new java.io.PipedOutputStream(pin);
        final InputStream raw = c.getInputStream();
        Thread prefetch = new Thread(new Runnable() {
            public void run() {
                byte[] buf = new byte[64 * 1024];
                try {
                    int n;
                    while ((n = raw.read(buf)) >= 0) pout.write(buf, 0, n);
                } catch (Throwable ignored) {
                } finally {
                    try { pout.close(); } catch (Throwable ignored) {}
                    try { raw.close(); } catch (Throwable ignored) {}
                    c.disconnect();
                }
            }
        }, "hai-prefetch");
        prefetch.setDaemon(true);
        prefetch.start();
        return new Result(meta, pin, len);
    }

    /** 可选并发加速：按 1MB 全局对齐切片、N 路并发、按序拼流、滑动窗口限在途片数。 */
    private static Result openConcurrent(final String url, final Map<String, String> headers,
                                         long start, int threads, int chunk, int window) throws IOException {
        Meta meta = probe(url, headers);
        if (start < 0) start = 0;
        if (threads < 1) threads = ProxyConfig.DEFAULT_THREADS;
        if (chunk < 64 * 1024) chunk = ProxyConfig.DEFAULT_CHUNK;
        if (window < threads) window = threads * 2;

        if (meta.ranged && meta.total > 0 && start < meta.total) {
            ChunkedInputStream in = new ChunkedInputStream(
                    url, headers, start, meta.total, chunk, window, pool(threads));
            return new Result(meta, in, meta.total - start);
        }

        // 单路直连：仍带上客户端的 Range，尽量让 CDN 从 start 开始给
        HttpURLConnection c = connect(url, headers);
        if (start > 0) c.setRequestProperty("Range", "bytes=" + start + "-");
        int code = c.getResponseCode();
        if (code >= 400) {
            c.disconnect();
            throw new IOException("upstream " + code);
        }
        long len = c.getHeaderFieldLong("Content-Length", -1);
        InputStream raw = c.getInputStream();
        InputStream wrapped = new FilterCloseInputStream(raw, c);
        return new Result(meta, wrapped, len);
    }

    /** 保证 HttpURLConnection 随流一起关掉，避免连接池泄漏。 */
    private static final class FilterCloseInputStream extends InputStream {
        private final InputStream in;
        private final HttpURLConnection c;
        FilterCloseInputStream(InputStream in, HttpURLConnection c) { this.in = in; this.c = c; }
        public int read() throws IOException { return in.read(); }
        public int read(byte[] b, int o, int l) throws IOException { return in.read(b, o, l); }
        public long skip(long n) throws IOException { return in.skip(n); }
        public int available() throws IOException { return in.available(); }
        public void close() throws IOException {
            try { in.close(); } finally { c.disconnect(); }
        }
    }

    /** 单块拉取任务。块边界按 chunk 全局对齐，失败自动重试。 */
    private static final class Fetch implements Callable<byte[]> {
        private final String url;
        private final Map<String, String> headers;
        private final long from;
        private final int len;

        Fetch(String u, Map<String, String> h, long f, int l) {
            url = u; headers = h; from = f; len = l;
        }

        public byte[] call() throws Exception {
            IOException last = null;
            for (int attempt = 0; attempt <= ProxyConfig.MAX_RETRY; attempt++) {
                HttpURLConnection c = null;
                try {
                    c = connect(url, headers);
                    c.setRequestProperty("Range", "bytes=" + from + "-" + (from + len - 1));
                    int code = c.getResponseCode();
                    if (code != 206 && code != 200) {
                        throw new IOException("chunk " + from + " http " + code);
                    }
                    InputStream in = c.getInputStream();
                    byte[] buf = new byte[len];
                    int off = 0;
                    while (off < len) {
                        int n = in.read(buf, off, len - off);
                        if (n < 0) break;
                        off += n;
                    }
                    in.close();
                    if (off == 0) throw new IOException("chunk " + from + " empty");
                    return off == len ? buf : Arrays.copyOf(buf, off);
                } catch (IOException e) {
                    last = e;
                } finally {
                    if (c != null) c.disconnect();
                }
            }
            throw last != null ? last : new IOException("chunk failed " + from);
        }
    }

    /**
     * 按序拼装的滑动窗口流。
     * ring[i % window] 保存块 i 的 Future；读走一块才补提交一块。
     */
    private static final class ChunkedInputStream extends InputStream {
        private final String url;
        private final Map<String, String> headers;
        private final long total;
        private final int chunk;
        private final int window;
        private final ExecutorService pool;
        private final long firstBlock;
        private final int skipInFirst;
        @SuppressWarnings("unchecked")
        private final Future<byte[]>[] ring;

        private long curBlock;        // 下一个要读的绝对块号
        private long submittedUntil;  // 下一个要提交的绝对块号
        private byte[] cur;
        private int pos;
        private boolean closed;

        @SuppressWarnings("unchecked")
        ChunkedInputStream(String u, Map<String, String> h, long start, long total,
                           int chunk, int window, ExecutorService pool) {
            this.url = u; this.headers = h; this.total = total;
            this.chunk = chunk; this.window = window; this.pool = pool;
            this.ring = new Future[window];
            this.firstBlock = start / chunk;
            this.skipInFirst = (int) (start % chunk);
            this.curBlock = firstBlock;
            this.submittedUntil = firstBlock;
            ensureSubmitted();
        }

        /** 块 i 的字节范围（全局对齐，1MB 边界不受 seek 影响）。 */
        private long blockStart(long i) { return i * (long) chunk; }

        private void submit(long i) {
            long from = blockStart(i);
            int len = (int) Math.min((long) chunk, total - from);
            if (len <= 0) return;
            ring[(int) (i % window)] = pool.submit(new Fetch(url, headers, from, len));
        }

        /** 保持在途块数 <= window：读走一块才补一块，形成天然背压。 */
        private void ensureSubmitted() {
            while (submittedUntil - curBlock < window && blockStart(submittedUntil) < total) {
                submit(submittedUntil);
                submittedUntil++;
            }
        }

        private boolean fill() throws IOException {
            if (closed || blockStart(curBlock) >= total) return false;
            int slot = (int) (curBlock % window);
            Future<byte[]> f = ring[slot];
            if (f == null) return false;
            byte[] data;
            try {
                data = f.get();
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
                throw new IOException("interrupted", e);
            } catch (ExecutionException e) {
                throw new IOException("chunk " + curBlock + " failed", e.getCause());
            }
            ring[slot] = null;
            long justRead = curBlock;
            curBlock++;
            ensureSubmitted();
            if (justRead == firstBlock && skipInFirst > 0) {
                if (skipInFirst >= data.length) {
                    // CDN 返回的片比预期短（尾片），直接丢弃本片继续读下一片
                    return fill();
                }
                data = Arrays.copyOfRange(data, skipInFirst, data.length);
            }
            cur = data;
            pos = 0;
            return true;
        }

        public int read() throws IOException {
            byte[] one = new byte[1];
            int n = read(one, 0, 1);
            return n < 0 ? -1 : (one[0] & 0xFF);
        }

        public int read(byte[] b, int off, int len) throws IOException {
            if (b == null) throw new NullPointerException();
            if (off < 0 || len < 0 || off + len > b.length) throw new IndexOutOfBoundsException();
            if (len == 0) return 0;
            if (cur == null && !fill()) return -1;
            int n = Math.min(len, cur.length - pos);
            System.arraycopy(cur, pos, b, off, n);
            pos += n;
            if (pos >= cur.length) { cur = null; pos = 0; }
            return n;
        }

        public long skip(long n) throws IOException {
            long left = n;
            byte[] tmp = new byte[8192];
            while (left > 0) {
                int got = read(tmp, 0, (int) Math.min(left, tmp.length));
                if (got < 0) break;
                left -= got;
            }
            return n - left;
        }

        public int available() {
            return cur == null ? 0 : cur.length - pos;
        }

        public void close() {
            if (closed) return;
            closed = true;
            cur = null;
            for (int i = 0; i < window; i++) {
                Future<byte[]> f = ring[i];
                if (f != null) f.cancel(true);
                ring[i] = null;
            }
        }
    }
}
