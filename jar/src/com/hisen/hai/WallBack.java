// -*- coding: utf-8 -*-
package com.hisen.hai;

import android.app.Activity;
import android.app.Application;
import android.content.Context;
import android.os.Bundle;
import android.util.Log;

import java.lang.ref.WeakReference;
import java.util.ArrayList;
import java.util.List;

/**
 * 播放返回跳过原生搜索页，直接回海报墙（2026-11-10 海哥要求）。
 *
 * <p>墙页点播放的真实链路（已从 APK smali + 真机栈核对）：
 * <pre>
 *   WebDetailActivity(墙页/SPA)
 *     → SearchActivity(带 keyword 自动搜全站)
 *     → CollectActivity(聚合搜索结果页)
 *     → VideoActivity(选集 + 播放)
 * </pre>
 * 原版把 Search/Collect 一直留在返回栈里，所以播放结束按返回时第一站
 * 是搜索页。这里在 jar 侧注册 {@link Application.ActivityLifecycleCallbacks}，
 * 一旦确认「已进入播放链路」，就把两个中间页一次性 finish 掉，
 * 播放页返回时栈里只剩墙页 —— <b>APK 一个字节不用改</b>，
 * 且逻辑随 classes2.dex 热更新，/live 换 PG 包不会失效。
 *
 * <p>判定不依赖播放器具体类名：中间页存在时又创建了任何新的非墙页
 * Activity，即认为进入播放链路；{@code VideoActivity} 命中只是加速确认。
 * 墙页 resume 时整体复位，保证下一次点片行为正常。
 */
public final class WallBack implements Application.ActivityLifecycleCallbacks {

    private static final String TAG = "WallBack";

    private static volatile boolean sInstalled;
    private static volatile boolean sPlayed;
    private static final List<WeakReference<Activity>> sMids =
            new ArrayList<WeakReference<Activity>>();

    public static void install(Context ctx) {
        if (sInstalled || ctx == null) return;
        Application app;
        try { app = (Application) ctx.getApplicationContext(); }
        catch (Throwable t) { return; }
        try {
            app.registerActivityLifecycleCallbacks(new WallBack());
            sInstalled = true;
            Log.i(TAG, "installed (skip Search/Collect after playback)");
        } catch (Throwable t) {
            try { Log.w(TAG, "install failed: " + t); } catch (Throwable ignored) {}
        }
    }

    private static String name(Activity a) {
        return a == null ? "" : a.getClass().getName();
    }

    private static boolean isWall(String n) {
        return n.endsWith(".WebDetailActivity");
    }

    private static boolean isMid(String n) {
        return n.endsWith(".SearchActivity") || n.endsWith(".CollectActivity");
    }

    private static boolean isPlayer(String n) {
        return n.endsWith(".VideoActivity") || n.contains("PlayerActivity");
    }

    private static boolean hasMids() {
        synchronized (sMids) {
            for (int i = sMids.size() - 1; i >= 0; i--) {
                if (sMids.get(i).get() == null) sMids.remove(i);
            }
            return !sMids.isEmpty();
        }
    }

    private static void remember(Activity a) {
        synchronized (sMids) {
            for (WeakReference<Activity> w : sMids) if (w.get() == a) return;
            sMids.add(new WeakReference<Activity>(a));
        }
    }

    /** 必须在主线程调用（生命周期回调本身就是主线程）。 */
    private static void finishMids() {
        List<Activity> list = new ArrayList<Activity>();
        synchronized (sMids) {
            for (WeakReference<Activity> w : sMids) {
                Activity a = w.get();
                if (a != null) list.add(a);
            }
            sMids.clear();
        }
        for (Activity a : list) {
            try {
                if (!a.isFinishing()) a.finish();
            } catch (Throwable ignored) {}
        }
        if (!list.isEmpty()) {
            try { Log.i(TAG, "finished " + list.size() + " search pages -> wall"); }
            catch (Throwable ignored) {}
        }
    }

    public void onActivityCreated(Activity a, Bundle b) {
        String n = name(a);
        if (isWall(n)) return;
        if (isMid(n)) { remember(a); return; }
        // 中间页之后又起了新 Activity（选集/播放页）⇒ 播放链路已经开始
        if (hasMids()) {
            sPlayed = true;
            finishMids();
        }
    }

    public void onActivityResumed(Activity a) {
        String n = name(a);
        if (isWall(n)) {
            // 回到墙页：整条链路复位，下次点片重新计
            sPlayed = false;
            synchronized (sMids) { sMids.clear(); }
            return;
        }
        if (isMid(n)) {
            if (sPlayed) {
                // 兜底：万一创建时没抓到，返回时再补一刀，绝不让搜索页露脸
                try { if (!a.isFinishing()) a.finish(); } catch (Throwable ignored) {}
            } else {
                remember(a);
            }
            return;
        }
        if (isPlayer(n)) {
            sPlayed = true;
            finishMids();
        }
    }

    public void onActivityDestroyed(Activity a) {
        synchronized (sMids) {
            for (int i = sMids.size() - 1; i >= 0; i--) {
                Activity x = sMids.get(i).get();
                if (x == null || x == a) sMids.remove(i);
            }
        }
    }

    public void onActivityPaused(Activity a) {}
    public void onActivityStarted(Activity a) {}
    public void onActivityStopped(Activity a) {}
    public void onActivitySaveInstanceState(Activity a, Bundle b) {}

    /** 供 /hai/ping 回显。 */
    public static String status() {
        int m;
        synchronized (sMids) { m = sMids.size(); }
        return "{\"installed\":" + sInstalled + ",\"played\":" + sPlayed
                + ",\"mids\":" + m + "}";
    }
}
