// -*- coding: utf-8 -*-
package com.hisen.hai;

import android.content.Context;
import android.util.Log;

import java.io.File;
import java.lang.reflect.Method;

import dalvik.system.DexClassLoader;

/**
 * 薄桥（APK 主 dex 里的一行壳）。
 *
 * <p>架构定案（2026-11-08）：
 * <ul>
 *   <li>APK 主 dex 只放这一层极薄壳；重逻辑（R 加速代理 / 本地服务 / 站源配置 API）
 *       全部留在 pg.jar 的 classes2.dex（{@code com.hisen.hai.HaiCore}），随 jar 热更新。</li>
 *   <li>Android 类加载是「子委托父」：App 主 PathClassLoader 看不到 pg.jar 里的类，
 *       直接 invoke HaiCore 会 NoClassDefFoundError 且抓不到 → App 启动崩。
 *       所以这里用 DexClassLoader 单独加载 pg.jar，再反射调 HaiCore.start(ctx)。</li>
 *   <li>pg.jar 路径由我们自己扫出来（App 用自己独立的 DexClassLoader 加载同一文件）。</li>
 *   <li><b>任何异常都静默降级</b>：jar 没下载 / 没 HaiCore / 端口被占，全部不影响原版观影。</li>
 * </ul>
 *
 * <p>时序：App.onCreate 调 start(ctx) 时，本包刚装好、pg.jar 可能还没被原版 spider 下载完。
 * 直接放弃会导致首启无代理（本包又禁了 Go）→ 播放失败直到重启。
 * 故：首启没找到 jar 时，起一个守护线程轮询最多 ~120s，jar 一就位就加载并启动 HaiCore。
 */
public final class HaiBridge {

    private static final String TAG = "HaiBridge";
    private static final String HAI_CORE = "com.hisen.hai.HaiCore";
    private static final long POLL_MS = 2000L;
    private static final int POLL_MAX = 60;          // 2s * 60 = 120s
    private static volatile boolean sTried;          // 同步首发尝试已做
    private static volatile boolean sStarted;        // HaiCore 已启动

    private HaiBridge() {}

    public static void start(Context ctx) {
        if (sStarted || sTried) return;
        sTried = true;
        if (ctx == null) return;
        // 立刻试一次（升级/非首启时 jar 已在 files 目录，直接起）
        if (tryStart(ctx)) return;
        // 首启 jar 可能未就位：后台轮询，不阻塞 App.onCreate
        final Context appCtx = ctx.getApplicationContext();
        Thread t = new Thread(new Runnable() {
            public void run() {
                for (int i = 0; i < POLL_MAX; i++) {
                    try { Thread.sleep(POLL_MS); } catch (InterruptedException ignored) { return; }
                    if (sStarted) return;
                    if (tryStart(appCtx)) return;
                }
                Log.i(TAG, "轮询 " + POLL_MAX + " 次仍未找到 pg.jar，放弃（保留原生兜底）");
            }
        }, "hai-bridge-poll");
        t.setDaemon(true);
        t.start();
    }

    /** 找到 jar 就加载并启动 HaiCore；返回 true 表示已启动或已在运行。 */
    private static boolean tryStart(Context ctx) {
        if (sStarted) return true;
        try {
            File jar = findJar(ctx);
            if (jar == null || !jar.exists()) return false;
            File opt = safeOptDir(ctx);
            DexClassLoader cl = new DexClassLoader(
                    jar.getAbsolutePath(), opt.getAbsolutePath(),
                    null, ctx.getClassLoader());
            Class<?> hc = cl.loadClass(HAI_CORE);
            Method m = hc.getMethod("start", Context.class);
            m.invoke(null, ctx);
            sStarted = true;
            Log.i(TAG, "HaiCore.start 已调用  jar=" + jar.getAbsolutePath());
            return true;
        } catch (Throwable t) {
            try { Log.w(TAG, "HaiBridge 启动失败（已静默降级）: " + t); } catch (Throwable ignored) {}
            return false;
        }
    }

    /** optimized dex 目录：优先 code_cache，回退 cache。 */
    private static File safeOptDir(Context ctx) {
        try { return ctx.getCodeCacheDir(); } catch (Throwable t) {}
        try { return ctx.getCacheDir(); } catch (Throwable t) {}
        return ctx.getFilesDir();
    }

    /** 在 files 目录里找 pg.jar（也兼容 spider.jar / 任意 .jar）。 */
    private static File findJar(Context ctx) {
        File files = ctx.getFilesDir();
        String[] names = {"pg.jar", "spider.jar", "ok.jar"};
        for (String n : names) {
            File f = new File(files, n);
            if (f.exists()) return f;
        }
        File[] list = files.listFiles();
        if (list != null) {
            for (File f : list) {
                if (f.isFile() && f.getName().endsWith(".jar")) return f;
            }
        }
        // 兜底绝对路径
        File f = new File("/data/data/" + ctx.getPackageName() + "/files/pg.jar");
        return f.exists() ? f : null;
    }
}
