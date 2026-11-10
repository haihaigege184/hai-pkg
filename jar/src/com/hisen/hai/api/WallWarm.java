// -*- coding: utf-8 -*-
package com.hisen.hai.api;

import android.content.Context;
import android.util.Log;

import java.util.ArrayList;
import java.util.HashSet;
import java.util.LinkedList;
import java.util.List;
import java.util.Set;

/**
 * 墙页「每天第一次打开软件就立即后台缓存每个类目 3 页」的端上预热队列。
 *
 * <p>2026-11-10 海哥要求：①翻页要提前自动预加载下一页，不能等点了才拉；
 * ②每日首启后台把每个类目 3 页缓存好；③<b>手动翻页的类目优先级更高</b>；
 * ④缓存当天有效。
 *
 * <p>为什么放在 jar 而不是 WebView 里：App 冷启动默认就落墙页，但用户点进
 * 详情页 / 播放后 WebView 可能被系统回收，纯 JS 的预热队列会半途中断。
 * 这里用 App 进程内的 daemon 单线程，只要进程在就一直把队列跑完，
 * 结果由 {@link Upstream} 写进按天分片的磁盘缓存，翻页时同源接口秒回。
 *
 * <p>优先级：{@code sUrgent}（手动翻页 / 切类目时提交）永远先于
 * {@code sWarm}（每日全类目）出队；已排在低优先队列的 URL 被高优先请求
 * 命中时会直接提升到队首，不重复请求。单线程串行 + 100ms 间隔，
 * 避免把豆瓣刷到限流（{@link Upstream} 还有失败冷却兜底）。
 */
public final class WallWarm {

    private static final String TAG = "WallWarm";
    private static final Object LOCK = new Object();

    private static final LinkedList<String> sUrgent = new LinkedList<String>();
    private static final LinkedList<String> sWarm = new LinkedList<String>();
    /** 队列内去重（含两个队列），避免同一 URL 重复入队。 */
    private static final Set<String> sQueued = new HashSet<String>();
    /** 当天已成功缓存的 URL；跨天清空，第二天首启自然重新拉真实数据。 */
    private static final Set<String> sDone = new HashSet<String>();

    private static Context sCtx;
    private static String sDay = "";
    private static Thread sWorker;
    private static int sDoneCount;
    private static int sFailCount;
    private static volatile String sCurrent = "";

    private WallWarm() {}

    public static void start(Context ctx) {
        sCtx = ctx == null ? null : ctx.getApplicationContext();
        synchronized (LOCK) {
            if (sWorker != null && sWorker.isAlive()) return;
            sWorker = new Thread(new Runnable() {
                public void run() { loop(); }
            }, "hai-wall-warm");
            sWorker.setDaemon(true);
            sWorker.start();
        }
        try { Log.i(TAG, "warm worker started"); } catch (Throwable ignored) {}
    }

    /**
     * 入队。{@code priority=true} 表示手动翻页 / 切换到的类目，整体插到
     * urgent 队首且保持传入顺序（pg+1 在 pg+2 前面）。
     *
     * @return 实际入队/提升的条数
     */
    public static int enqueue(List<String> urls, boolean priority) {
        if (urls == null || urls.isEmpty()) return 0;
        int n = 0;
        List<String> fresh = new ArrayList<String>();
        synchronized (LOCK) {
            rollDay();
            for (String u : urls) {
                if (u == null || u.length() < 12) continue;
                if (Upstream.sanitize(u) == null) continue;   // 白名单硬边界
                if (sDone.contains(u)) continue;
                if (sQueued.contains(u)) {
                    // 低优先队列里已有 ⇒ 提升到高优先队首
                    if (priority && sWarm.remove(u)) {
                        sUrgent.addFirst(u);
                        n++;
                    }
                    continue;
                }
                fresh.add(u);
            }
            if (!fresh.isEmpty()) {
                if (priority) {
                    // 整体插到队首，内部顺序不变
                    for (int i = 0; i < fresh.size(); i++) {
                        sUrgent.add(i, fresh.get(i));
                    }
                } else {
                    sWarm.addAll(fresh);
                }
                sQueued.addAll(fresh);
                n += fresh.size();
            }
            if (n > 0) LOCK.notifyAll();
        }
        return n;
    }

    private static void rollDay() {
        String d = Upstream.dayKey();
        if (!d.equals(sDay)) {
            sDay = d;
            sDone.clear();
            sQueued.clear();
            sUrgent.clear();
            sWarm.clear();
            sDoneCount = 0;
            sFailCount = 0;
        }
    }

    private static String take() {
        synchronized (LOCK) {
            rollDay();
            String u = null;
            if (!sUrgent.isEmpty()) u = sUrgent.removeFirst();
            else if (!sWarm.isEmpty()) u = sWarm.removeFirst();
            if (u != null) sQueued.remove(u);
            return u;
        }
    }

    private static void loop() {
        while (true) {
            String u = take();
            if (u == null) {
                synchronized (LOCK) {
                    try { LOCK.wait(1500L); } catch (InterruptedException ignored) {}
                }
                continue;
            }
            sCurrent = u;
            boolean ok = false;
            try {
                // Upstream.fetch 自带内存 + 当天磁盘缓存，重复入队是廉价本地读
                ok = Upstream.fetch(sCtx, u, false).ok();
            } catch (Throwable ignored) {
            } finally {
                synchronized (LOCK) {
                    if (ok) { sDone.add(u); sDoneCount++; }
                    else sFailCount++;
                }
                sCurrent = "";
            }
            // 轻微节流：电视弱网下不把豆瓣刷进冷却
            try { Thread.sleep(100L); } catch (InterruptedException ignored) {}
        }
    }

    /** 供 /hai/warm/status 与 /hai/ping 回显，真机一眼能看到预热进度。 */
    public static String status() {
        synchronized (LOCK) {
            rollDay();
            return "{\"day\":\"" + sDay + "\",\"urgent\":" + sUrgent.size()
                    + ",\"warm\":" + sWarm.size() + ",\"done\":" + sDoneCount
                    + ",\"fail\":" + sFailCount + ",\"current\":\""
                    + esc(sCurrent) + "\"}";
        }
    }

    private static String esc(String s) {
        return s == null ? "" : s.replace("\\", "\\\\")
                .replace("\"", "\\\"").replace("\n", " ");
    }
}
