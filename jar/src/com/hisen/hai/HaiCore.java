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

    private HaiCore() {}

    public static void start(Context ctx) {
        if (sStarted) return;
        synchronized (HaiCore.class) {
            if (sStarted) return;
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
