// -*- coding: utf-8 -*-
package com.hisen.hai;

import android.content.Context;
import android.util.Log;

import com.hisen.hai.api.HaiApiServer;
import com.hisen.hai.proxy.DownloadProxyServer;
import com.hisen.hai.proxy.PortRelay;
import com.hisen.hai.proxy.ProxyConfig;

/**
 * jar 侧总入口，由 APK 侧一行反射调用：
 *
 * <pre>
 *   invoke-static {p0}, Lcom/hisen/hai/HaiCore;-&gt;start(Landroid/content/Context;)V
 * </pre>
 *
 * <p>放在 pg.jar 的 <b>classes2.dex</b>：原版 classes.dex（456 个站源类 + Init）
 * 一个字节都不动，DexClassLoader 会把两个 dex 合并进同一 ClassLoader，
 * 所以「站源照搬」与「播放链路完全自研」可以同时成立，且随时可回滚
 * （删掉 classes2.dex 就是纯原版）。
 *
 * <p><b>绝不能让这里抛异常到 App</b>：所有初始化失败都必须静默降级，
 * 保证即使 jar 有问题，原版观影功能仍然完整可用。
 */
public final class HaiCore {

    private static final String TAG = "HaiCore";
    private static volatile boolean sStarted;
    private static volatile Context sApp;
    private static volatile String sProc = "?";

    private HaiCore() {}

    /**
     * /proc/self/cmdline 第一段。主进程严格为包名；崩溃兜底进程形如
     * {@code com.hisen.android.tv:error_activity}。读不到时返回 "?" 并按主进程处理，
     * 避免误伤导致墙页服务整体不启动。
     */
    public static String procName() {
        java.io.BufferedReader r = null;
        try {
            r = new java.io.BufferedReader(new java.io.FileReader("/proc/self/cmdline"), 128);
            String raw = r.readLine();
            if (raw == null) return "?";
            String name = raw.split("\0")[0].trim();
            return name.isEmpty() ? "?" : name;
        } catch (Throwable ignored) {
            return "?";
        } finally {
            if (r != null) try { r.close(); } catch (Throwable ignored) {}
        }
    }

    /** 只拦明确带 ":" 的子进程，其余一律保守视为主进程。 */
    private static boolean isMainProcess(Context ctx, String proc) {
        if ("?".equals(proc) || proc == null) return true;
        String pkg = ctx == null ? null : ctx.getPackageName();
        if (pkg == null || pkg.isEmpty()) return true;
        return !proc.startsWith(pkg + ":");
    }

    /** 供 /hai/ping 回显，便于一眼判断端口是否被非主进程抢跑。 */
    public static String proc() { return sProc; }

    public static void start(Context ctx) {
        if (sStarted) return;
        synchronized (HaiCore.class) {
            if (sStarted) return;
            // 2026-11-10 实踩：PG 带 :error_activity 崩溃兜底进程，Application.onCreate
            // 每个进程都会跑。崩溃一次后该子进程先抢到 10090/9978/10078，主进程的
            // HaiCore 就一直 "bind 10090 failed"，取链服务留在没有完整 spider 初始化
            // 的进程里 ⇒ 9978/proxy 500 ⇒ 原画全挂（错误码 3003），而预览画质不走
            // 这条链所以照常能播。这里只让主进程持有全部本地端口。
            String proc = procName();
            sProc = proc;
            if (!isMainProcess(ctx, proc)) {
                try { Log.i(TAG, "skip non-main process: " + proc
                        + " (all local ports stay in main process)"); } catch (Throwable ignored) {}
                return;
            }
            try {
                sApp = ctx == null ? null : ctx.getApplicationContext();
                DownloadProxyServer server = DownloadProxyServer.get();
                server.setApiHandler(new HaiApiServer(sApp));
                int port = server.start();
                if (port <= 0) {
                    // 2026-11-09：不能在这里静默吞掉并把 sStarted 置 true，
                    // 否则 APK 侧墙页服务永远起不来且 WebView 已加载失败 → 黑屏。
                    // 抛出去让 HaiBridge 的反射调用失败，从而进入 2s 轮询重试。
                    throw new IllegalStateException("bind 10090 failed after retry");
                }
                // Java 代理只服务 APK 内置电视墙 UI 的同源链路；播放取流走 PG 原版 Go。
                ProxyConfig.sBase = "http://127.0.0.1:" + port;
                Log.i(TAG, "started on " + port + " base=" + ProxyConfig.sBase);
                sStarted = true;
                // 2026-11-10：iptables 10078→10079 规则设备重启必丢 ⇒ 原画全挂（错误码 2001）。
                // 用 classes2.dex 里的纯 TCP 透明转发占住 10078 兜底：规则在时走内核
                // REDIRECT，规则丢时由转发接管；取流仍 100% 由原版 Go(10079) 处理。
                try {
                    PortRelay.start();
                } catch (Throwable ignored) {}
                // 2026-11-09：PG 的 9978/10079 原本只在 HomeActivity.onCreate 里拉起，
                // 而改造版开机直接落墙页 ⇒ 服务不在线 ⇒ 站源与聚合搜索全空。
                // 在这里反射提前拉起（幂等、后台线程、全程吞异常）。
                // 换 PG 包不会失效：本类由 core/extinject.py 自动重挂进 classes2.dex，
                // 反射目标类在 APK dex 里，/live 换包不动 APK。
                try {
                    HaiBoot.startAsync(sApp);
                } catch (Throwable ignored) {}
                // 2026-11-10：①每日首启后台预热全类目 3 页（手动翻页插队优先），
                // ②播放返回跳过原生搜索页直接回墙页。两者都是 App 进程内的
                // 守护逻辑，WebView 被回收也不会中断，/live 换包随 dex 自动重挂。
                try {
                    com.hisen.hai.api.WallWarm.start(sApp);
                } catch (Throwable ignored) {}
                try {
                    WallBack.install(sApp);
                } catch (Throwable ignored) {}
            } catch (Throwable t) {
                try { Log.w(TAG, "start failed: " + t); } catch (Throwable ignored) {}
                // 反射调用方（HaiBridge）会捕获 InvocationTargetException 并轮询重试，
                // 异常不会冒泡到 App.onCreate，原版观影不受影响。
                throw new RuntimeException(t);
            }
        }
    }

    public static Context app() { return sApp; }

    public static boolean isStarted() { return sStarted; }

    /** 供 APK/设置页查询当前代理状态。 */
    public static String status() {
        DownloadProxyServer s = DownloadProxyServer.get();
        return "{\"started\":" + sStarted + ",\"running\":" + s.isRunning()
                + ",\"port\":" + s.port() + ",\"base\":\"" + ProxyConfig.sBase + "\"}";
    }
}
