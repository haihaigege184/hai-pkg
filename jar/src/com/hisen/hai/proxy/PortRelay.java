// -*- coding: utf-8 -*-
package com.hisen.hai.proxy;

import android.util.Log;

import java.net.InetSocketAddress;
import java.net.ServerSocket;
import java.net.Socket;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.ThreadFactory;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicLong;

/**
 * 10078 → 10079 纯 TCP 透明转发：让原画播放不再依赖 iptables 规则。
 *
 * <p><b>背景（2026-11-10 实踩）</b>：PG 把原画分片 URL 写死成
 * {@code http://127.0.0.1:10078/p/<seg>/<hdr>/<chunk>/<b64>/<name>}，
 * 但设备上没有任何进程监听 10078 —— 真正监听的是 APK assets 里的 Go 二进制
 * {@code go_proxy_video}(10079)。原版靠一条全局 nat 规则把 10078 转给 10079：
 *
 * <pre>
 *   iptables -t nat -A OUTPUT -p tcp --dport 10078 -j REDIRECT --to-ports 10079
 * </pre>
 *
 * <p>iptables 规则<b>不持久化，设备每次重启必丢</b>。规则一丢，原画一律
 * {@code ECONNREFUSED} → 播放器「错误代码 2001」；预览画质不走 10078 所以仍然能播。
 * App 进程实测无法提权到 root（{@code su} 拒绝），设备也没有 Magisk/init.d，
 * APK 内更没有下发规则的代码 ⇒ 只能从「让 10078 真的有人监听」入手。
 *
 * <p><b>做法</b>：本类只 bind 10078，把每个连接原样双向透传给 127.0.0.1:10079，
 * 不解析、不改写、不缓存任何 HTTP 字节。取流 100% 仍由原版 Go 处理，
 * 播放链路逻辑零改动。
 *
 * <p><b>与 iptables 互补、不冲突</b>：REDIRECT 作用在 OUTPUT 链，只改本机
 * <i>发出</i>的包；规则存在时包在内核层直接被改写到 10079，本监听器收不到
 * （性能更好）；规则丢失时包落到 10078，由本类接管。转发时连的是 10079，
 * 不会被 dpt:10078 再次改写，不存在环路。
 *
 * <p><b>可回滚</b>：本类只存在于 {@code classes2.dex}，删掉该 dex 即回纯原版。
 * 换 PG 包时 {@code core/extinject.py} 会自动重挂，用户无需执行任何脚本。
 */
public final class PortRelay {

    private static final String TAG = "HaiPortRelay";

    public static final int LISTEN_PORT = 10078;
    public static final int UPSTREAM_PORT = 10079;

    private static final int BUFFER = 64 * 1024;
    private static final int CONNECT_TIMEOUT_MS = 5000;

    private static final AtomicBoolean sRunning = new AtomicBoolean(false);
    private static final AtomicLong sConns = new AtomicLong();
    private static final AtomicLong sBytes = new AtomicLong();
    private static volatile ServerSocket sServer;

    private static final ThreadFactory DAEMON_TF = new ThreadFactory() {
        private final ThreadFactory base = Executors.defaultThreadFactory();
        public Thread newThread(Runnable r) {
            Thread t = base.newThread(r);
            t.setDaemon(true);
            t.setName("hai-relay-" + t.getId());
            return t;
        }
    };

    private static final ExecutorService POOL = Executors.newCachedThreadPool(DAEMON_TF);

    private PortRelay() {}

    /** 幂等启动；bind 失败只记日志静默降级，绝不影响原版观影。 */
    public static void start() {
        if (!sRunning.compareAndSet(false, true)) return;
        ServerSocket bound = null;
        // 冷启动 / force-stop 后端口可能处 TIME_WAIT，与 10090 一样做重试。
        for (int i = 0; i < 8 && bound == null; i++) {
            try {
                ServerSocket s = new ServerSocket();
                s.setReuseAddress(true);
                // bind 全网卡：PG 历史上出现过 127.0.0.1 与本机 IP 两种 host。
                // Go 代理本身也是监听 :::10079，暴露面一致。
                s.bind(new InetSocketAddress(LISTEN_PORT), 128);
                bound = s;
            } catch (Throwable t) {
                try { Thread.sleep(250L); } catch (InterruptedException ignored) {}
            }
        }
        if (bound == null) {
            sRunning.set(false);
            Log.w(TAG, "bind 10078 failed, skipped (kernel REDIRECT may still work)");
            return;
        }
        sServer = bound;
        final ServerSocket server = bound;
        Thread t = new Thread(new Runnable() {
            public void run() { acceptLoop(server); }
        }, "hai-port-relay");
        t.setDaemon(true);
        t.start();
        Log.i(TAG, "listening " + LISTEN_PORT + " -> " + UPSTREAM_PORT
                + " (transparent TCP relay, no HTTP parsing)");
    }

    private static void acceptLoop(ServerSocket server) {
        while (sRunning.get()) {
            try {
                final Socket client = server.accept();
                sConns.incrementAndGet();
                POOL.execute(new Runnable() {
                    public void run() { relay(client); }
                });
            } catch (Throwable t) {
                if (!sRunning.get()) break;
                try { Thread.sleep(50L); } catch (InterruptedException ignored) {}
            }
        }
    }

    private static void relay(final Socket client) {
        Socket up = null;
        try {
            client.setTcpNoDelay(true);
            up = new Socket();
            up.setTcpNoDelay(true);
            up.connect(new InetSocketAddress("127.0.0.1", UPSTREAM_PORT),
                    CONNECT_TIMEOUT_MS);
            final Socket upstream = up;
            final AtomicInteger done = new AtomicInteger(0);
            // client -> Go（交给线程池）
            POOL.execute(new Runnable() {
                public void run() { pump(client, upstream, done); }
            });
            // Go -> client（当前线程）
            pump(upstream, client, done);
        } catch (Throwable t) {
            closeQuietly(client);
            closeQuietly(up);
        }
    }

    /**
     * 单向字节搬运：从 src 读、往 dst 写，绝不解释内容。
     * 读到 EOF 时对 dst 做 half-close（保留另一方向继续传完响应），
     * 两个方向都结束后才真正关闭这对 socket。
     */
    private static void pump(Socket src, Socket dst, AtomicInteger done) {
        try {
            java.io.InputStream in = src.getInputStream();
            java.io.OutputStream out = dst.getOutputStream();
            byte[] buf = new byte[BUFFER];
            int n;
            while ((n = in.read(buf)) != -1) {
                out.write(buf, 0, n);
                sBytes.addAndGet(n);
            }
            try { out.flush(); } catch (Throwable ignored) {}
        } catch (Throwable ignored) {
        } finally {
            try { dst.shutdownOutput(); } catch (Throwable ignored) {}
            if (done.incrementAndGet() == 2) {
                closeQuietly(src);
                closeQuietly(dst);
            }
        }
    }

    private static void closeQuietly(Socket s) {
        if (s == null) return;
        try { s.close(); } catch (Throwable ignored) {}
    }

    public static boolean isListening() {
        ServerSocket s = sServer;
        return s != null && !s.isClosed() && sRunning.get();
    }

    /** 供 /hai/ping 回显，便于判断「规则丢了但转发已接管」。 */
    public static String status() {
        return "{\"listening\":" + isListening()
                + ",\"port\":" + LISTEN_PORT
                + ",\"upstream\":" + UPSTREAM_PORT
                + ",\"conns\":" + sConns.get()
                + ",\"bytes\":" + sBytes.get() + "}";
    }
}
