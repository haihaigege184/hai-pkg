// -*- coding: utf-8 -*-
package com.hisen.hai;

import android.content.Context;
import android.util.Log;

import com.hisen.hai.api.HaiApiServer;
import com.hisen.hai.proxy.DownloadProxyServer;
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
                if (port > 0) {
                    // 原版 jar 内 R/a.d() 默认就是 127.0.0.1:10078；
                    // 我们占住同一个端口，站源拼出来的 /p/ /cfgo/ URL 自然落到我们服务。
                    ProxyConfig.sBase = "http://127.0.0.1:" + port;
                    Log.i(TAG, "started on " + port + " base=" + ProxyConfig.sBase);
                } else {
                    Log.w(TAG, "no free port in 10078..10085, proxy disabled");
                }
                sStarted = true;
            } catch (Throwable t) {
                // 静默降级：绝不影响原版启动
                try { Log.w(TAG, "start failed: " + t); } catch (Throwable ignored) {}
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
