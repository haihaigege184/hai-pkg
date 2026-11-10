// -*- coding: utf-8 -*-
package com.hisen.hai;

import android.content.Context;
import android.util.Log;

import java.lang.reflect.Field;
import java.lang.reflect.Method;
import java.lang.reflect.Modifier;
import java.net.InetSocketAddress;
import java.net.Socket;

/**
 * 提前拉起 PG 原版运行时（9978 站源分发 + 10079 spider 媒体代理）。
 *
 * <p><b>为什么需要</b>：改造版开机直接叠 {@code WebDetailActivity}（离线海报墙），
 * {@code HomeActivity} 从未创建 ⇒ PG 的两条运行时初始化都不会跑 ⇒
 * 站源拿不到资源、PG 聚合搜索恒为空。用户必须先按返回键进一次首页才恢复。
 *
 * <p><b>两条完全独立的入口（2026-11-09 逆向定案）</b>：
 * <ol>
 * <li><b>9978</b> — APK 侧 NanoHTTPD 站源分发。{@code BaseActivity.onCreate} 调
 *     {@code OoO0oOoOoO0oO0O0.oOoOoOoOoOoOoO0o.oOoOoOo0oO0oO0o0()V} 拉起。
 *     类在 <b>APK dex</b>，必须用宿主 {@code ctx.getClassLoader()}。
 * <li><b>10079 / aliproxy / sing-box / allinone</b> — spider 运行时二进制。
 *     APK 侧的 jar 加载器首次解析站源配置时反射调用
 *     {@code com.github.catvod.spider.Init.init(Context)}，后者解压 DE 目录的
 *     tar.xz、killall 旧进程并 exec。类在 <b>pg.jar 的 classes.dex</b>，
 *     必须用 {@code HaiBoot.class.getClassLoader()}（jar 自己的 DexClassLoader），
 *     用宿主 loader 必然 ClassNotFoundException。
 * <li><b>站源配置 sites</b> — {@code ApiConfig} 单例的 6 个容器字段只在
 *     {@code HomeActivity.onCreate} 模板里初始化并从 {@code Config.vod()} 解析；
 *     单例 {@code <clinit>} 只 new 了空壳 ⇒ 冷启动 {@code getSites()} 返回空列表
 *     ⇒ 聚合搜索零站源、不发任何请求。类在 APK dex，用 DB 缓存 json 同步解析即可。
 * </ol>
 * 早先误以为「9978 与 10079 由同一个 NanoWSD.start() 一起拉起」，只反射前者时
 * 实测 9978 起来了但搜索仍为空；补上 {@code Init.init} 后服务全起、搜索仍空，
 * 才定位到第三支 ApiConfig。三条缺一不可。
 *
 * <p><b>做法</b>：在 {@code HaiCore.start()}（App.onCreate 里的 DexClassLoader 入口）
 * 后台线程同时反射这两个入口，随后轮询 9978 + 10079 直到都监听。
 * 两者都不依赖 Activity / UI 线程，且各自幂等。
 *
 * <p><b>换包不失效</b>：9978 的类名在 APK dex 里，/live 换 PG 包只换服务器
 * jar / lib / js，不动 APK；{@code Init} 在新包 classes.dex 里，类名稳定。
 * 本类身在 classes2.dex，由服务器 {@code core/extinject.py} 每次换包自动重挂。
 * 任何一步缺失都优雅降级：反射不到只是记日志，原版观影链路不受影响。
 *
 * <p><b>绝不抛异常到 App</b>：所有 Throwable 就地吞掉。
 */
public final class HaiBoot {

    private static final String TAG = "HaiBoot";
    private static final int PORT_HTTP = 9978;   // APK NanoHTTPD 站源分发
    private static final int PORT_GO = 10079;    // allinone / aliproxy 媒体代理

    // ── 入口 1：APK 侧 9978 ─────────────────────────────────────────
    // 当前改造版 APK 反编译实测的 holder 类名。数组形式，将来重混淆只需追加候选。
    private static final String[] HOLDERS = {
            "OoOo0oO0oOoOo0oO.oOo0oOo0Oo0oO0Oo",
    };
    private static final String MANAGER_TYPE = "OoO0oOoOoO0oO0O0.oOoOoOoOoOoOoO0o";
    private static final String[] START_METHODS = {
            "oOoOoOo0oO0oO0o0",
    };

    // ── 入口 2：jar 侧 spider Init ───────────────────────────────────
    private static final String SPIDER_INIT = "com.github.catvod.spider.Init";

    // ── 入口 3：APK 侧站源配置（ApiConfig 单例）─────────────────────
    // 冷启动直落墙页时 HomeActivity 不跑，ApiConfig 的 <clinit> 只 new 了空壳，
    // 6 个容器字段全是 null ⇒ getSites() 返回 emptyList ⇒ 聚合搜索零站源。
    private static final String API_HOLDER = "oOoO0O0Oo0oO0O0o.oOoOoOo0O0O0oO0o";
    private static final String API_FIELD = "oOo0oOo0Oo0oO0Oo";
    private static final String API_CONFIG = "oOoO0O0Oo0oO0O0o.OoOoOo0O0o0oO0o0";
    private static final String CFG_CLASS = "com.fongmi.android.tv.bean.Config";
    private static final String GET_SITES = "oOoO0OoO0oOo0oOo";
    // HomeActivity:2446-2520 补做的 6 个容器（第 4 个就是 sites）
    private static final String[] LIST_FIELDS = {
            "OoOoOo0O0o0oO0o0",
            "oOoOoOoOoOoOoO0o",
            "oOo0oOo0Oo0oO0Oo",
            "oOoOoOo0oOo0o0oO",
            "oOoO0o0oOo0oO0Oo",
            "oOoOoOo0O0O0oO0o",
    };
    private static final String CFG_FIELD = "OoOoO0O0o0oOoO0O";
    // parseConfig 的三个子方法：sites / 其余配置 / 默认 Site+Parse
    private static final String[] PARSE_METHODS = {
            "OoOoO0o0oO0O0O0O",
            "OoOoOo0O0Oo0o0oO",
            "oOoOoOo0oO0oO0o0",
    };

    private static volatile ClassLoader sHostLoader;   // APK dex
    private static volatile Context sApp;
    private static volatile boolean sThreadRunning;
    private static volatile boolean sHttpInvoked;
    private static volatile boolean sSpiderInvoked;
    private static volatile int sSites;

    private HaiBoot() {}

    /** 由 {@code HaiCore.start()} 调用一次，保存宿主 loader / Context 并起守护线程。 */
    public static void startAsync(Context ctx) {
        try {
            ClassLoader cl = ctx == null ? null : ctx.getClassLoader();
            if (cl == null) cl = HaiBoot.class.getClassLoader();
            sHostLoader = cl;
            sApp = ctx == null ? null : ctx.getApplicationContext();
        } catch (Throwable ignored) {}
        kick();
    }

    /**
     * 幂等的「确保运行时在线」触发点。{@code /hai/ping} 与墙页首次请求会再踢一次，
     * 覆盖 App 进程未重建、或首轮反射太早导致二进制尚未释放的情况。
     */
    public static void kick() {
        if (serversUp() && sSites > 0) return;
        if (sThreadRunning) return;
        synchronized (HaiBoot.class) {
            if (sThreadRunning) return;
            sThreadRunning = true;
        }
        Thread t = new Thread(new Runnable() {
            @Override public void run() {
                try {
                    boot();
                } catch (Throwable t2) {
                    try { Log.w(TAG, "boot thread failed: " + t2); } catch (Throwable ignored) {}
                } finally {
                    sThreadRunning = false;
                }
            }
        }, "hai-boot-pg");
        t.setDaemon(true);
        t.start();
    }

    private static void boot() {
        // 让 App.onCreate 的通知渠道 / X5 初始化先跑完，也等 DE 目录二进制就绪。
        sleep(700);
        boolean httpTried = false;
        boolean spiderTried = false;
        boolean sitesTried = false;
        ClassLoader host = sHostLoader;
        if (host == null) host = HaiBoot.class.getClassLoader();
        for (int i = 0; i < 12; i++) {
            if (serversUp() && sSites > 0) {
                Log.i(TAG, "pg runtime up: 9978+10079 sites=" + sSites
                        + " (round " + i + ")");
                return;
            }
            if (!httpTried) {
                try {
                    if (invokeHttpOnce(host)) {
                        httpTried = true;
                        Log.i(TAG, "9978 start() reflected ok (round " + i + ")");
                    }
                } catch (Throwable t) {
                    try { Log.w(TAG, "9978 round " + i + ": " + t); } catch (Throwable ignored) {}
                }
            }
            if (!spiderTried) {
                spiderTried = initSpider();
            }
            if (!sitesTried) {
                initSites(host);
                sitesTried = true;
            }
            sleep(1200);
        }
        Log.w(TAG, "pg runtime still incomplete: 9978=" + portOpen(PORT_HTTP)
                + " 10079=" + portOpen(PORT_GO) + " sites=" + sSites
                + " httpInvoked=" + sHttpInvoked + " spiderInvoked=" + sSpiderInvoked);
    }

    /**
     * 入口 3：把 DB 里的激活配置解析进 ApiConfig 单例，让聚合搜索拿到 sites。
     *
     * <p>纯本地、同步、零网络：{@code Config.vod()} 取 Room 里 type=0 的激活行，
     * {@code getJson()} 是 App 自己缓存的 74 站点 jsm，直接喂给三个 parse 子方法。
     * 不碰 HomeActivity 的网络刷新 —— 之后 HomeActivity 真可见时会再跑一遍完整
     * 加载（含 GET jsm.json），以最新配置覆盖，二者幂等。
     *
     * <p>必须先补 6 个容器字段：单例 {@code <clinit>} 只调了 Object.<init>，
     * 字段全 null，直接调 reset()/parse 会 NPE。
     */
    private static void initSites(ClassLoader cl) {
        try {
            Class<?> apiCls = Class.forName(API_CONFIG, true, cl);
            Object api = Class.forName(API_HOLDER, true, cl).getField(API_FIELD).get(null);
            if (api == null) return;

            // 已经加载过就别重复初始化（HomeActivity 可能已经跑完）
            Object existing = apiCls.getMethod(GET_SITES).invoke(api);
            if (existing instanceof java.util.List && !((java.util.List<?>) existing).isEmpty()) {
                sSites = ((java.util.List<?>) existing).size();
                Log.i(TAG, "ApiConfig sites already loaded: " + sSites);
                return;
            }

            Class<?> cfgCls = Class.forName(CFG_CLASS, true, cl);
            Object cfg = cfgCls.getMethod("vod").invoke(null);
            if (cfg == null) return;

            for (String name : LIST_FIELDS) {
                try {
                    Field f = apiCls.getField(name);
                    f.setAccessible(true);
                    if (f.get(api) == null) f.set(api, new java.util.ArrayList<Object>());
                } catch (Throwable t) {
                    try { Log.w(TAG, "init list field " + name + ": " + t); } catch (Throwable ignored) {}
                }
            }
            // 2026-11-10 第四条入口：HomeActivity 不跑时 ApiConfig 里还有若干数组字段
            // 是 null。9978 的 /proxy 取链处理器访问它就抛
            // "Attempt to read from null array" → 500 → 原画 3003（预览不走这条链）。
            // 阶段6 误判第三个 parse 的 InvocationTargetException「非致命」，其实
            // 搜索能好只是因为 sites 已由前两个 parse 填好，取链仍必挂。
            // 统一把 null 数组补成空数组；HomeActivity 可见后原版完整加载会覆盖。
            for (Field f : apiCls.getDeclaredFields()) {
                try {
                    if (Modifier.isStatic(f.getModifiers())) continue;
                    if (!f.getType().isArray()) continue;
                    f.setAccessible(true);
                    if (f.get(api) != null) continue;
                    f.set(api, java.lang.reflect.Array.newInstance(
                            f.getType().getComponentType(), 0));
                    try { Log.i(TAG, "init null array field: " + f.getName()
                            + " " + f.getType().getComponentType().getName() + "[]"); }
                    catch (Throwable ignored) {}
                } catch (Throwable ignored) {}
            }
            try {
                Field f = apiCls.getField(CFG_FIELD);
                f.setAccessible(true);
                f.set(api, cfg);
            } catch (Throwable t) {
                try { Log.w(TAG, "set Config field: " + t); } catch (Throwable ignored) {}
            }

            String json = (String) cfgCls.getMethod("getJson").invoke(cfg);
            if (json == null || json.length() < 100) {
                Log.w(TAG, "Config.getJson() 为空，跳过 sites 解析");
                return;
            }
            Class<?> jpCls = Class.forName("com.google.gson.JsonParser", true, cl);
            Object parsed;
            try {
                parsed = jpCls.getMethod("parseString", String.class).invoke(null, json);
            } catch (Throwable t) {
                // 旧版 Gson 没有静态 parseString
                Object jp = jpCls.newInstance();
                parsed = jp.getClass().getMethod("parse", String.class).invoke(jp, json);
            }
            Object jo = parsed.getClass().getMethod("getAsJsonObject").invoke(parsed);
            Class<?> joCls = jo.getClass();
            for (String m : PARSE_METHODS) {
                try {
                    Method mm = apiCls.getDeclaredMethod(m, joCls);
                    mm.setAccessible(true);
                    mm.invoke(api, jo);
                } catch (Throwable t) {
                    Throwable cause = t.getCause() == null ? t : t.getCause();
                    try { Log.w(TAG, "parse " + m + ": " + cause); } catch (Throwable ignored) {}
                }
            }
            Object after = apiCls.getMethod(GET_SITES).invoke(api);
            sSites = (after instanceof java.util.List) ? ((java.util.List<?>) after).size() : 0;
            Log.i(TAG, "ApiConfig sites loaded from DB cache: " + sSites);
        } catch (Throwable t) {
            try { Log.w(TAG, "initSites failed: " + t); } catch (Throwable ignored) {}
        }
    }

    /** 入口 2：反射 jar classes.dex 里的 spider 全局初始化器。 */
    private static boolean initSpider() {
        Context c = sApp;
        if (c == null) return false;
        try {
            // 关键：必须用 jar 自己的 DexClassLoader（同时含 classes.dex + classes2.dex）。
            // 宿主 PathClassLoader 里根本没有 com.github.catvod.spider.Init。
            ClassLoader jarLoader = HaiBoot.class.getClassLoader();
            Class<?> init = Class.forName(SPIDER_INIT, true, jarLoader);
            Method m = init.getMethod("init", Context.class);
            m.invoke(null, c);
            sSpiderInvoked = true;
            Log.i(TAG, "spider Init.init(Context) reflected ok");
            return true;
        } catch (Throwable t) {
            try { Log.w(TAG, "spider Init.init failed: " + t); } catch (Throwable ignored) {}
            return false;
        }
    }

    /**
     * 入口 1：反射 APK 侧 9978 服务管理器。先按已知字段类型精确匹配，
     * 再退化为「遍历所有静态引用字段 + 精确方法名」，抗字段/类型改名。
     */
    private static boolean invokeHttpOnce(ClassLoader cl) {
        for (String hn : HOLDERS) {
            Class<?> holder;
            try {
                holder = Class.forName(hn, true, cl);
            } catch (Throwable t) {
                continue;
            }
            Field[] fs;
            try {
                fs = holder.getDeclaredFields();
            } catch (Throwable t) {
                continue;
            }
            // 第一遍：字段类型精确匹配
            for (Field f : fs) {
                if (!isCandidateField(f) || !MANAGER_TYPE.equals(f.getType().getName())) continue;
                if (tryInvoke(f)) return true;
            }
            // 第二遍：类型改名也能命中（只要方法名还在）
            for (Field f : fs) {
                if (!isCandidateField(f) || MANAGER_TYPE.equals(f.getType().getName())) continue;
                if (tryInvoke(f)) return true;
            }
        }
        return false;
    }

    private static boolean isCandidateField(Field f) {
        try {
            if (!Modifier.isStatic(f.getModifiers())) return false;
            Class<?> t = f.getType();
            return !t.isPrimitive() && !t.isArray() && t != String.class;
        } catch (Throwable t) {
            return false;
        }
    }

    private static boolean tryInvoke(Field f) {
        try {
            f.setAccessible(true);
            Object mgr = f.get(null);
            if (mgr == null) return false;
            for (String mn : START_METHODS) {
                try {
                    Method m = mgr.getClass().getDeclaredMethod(mn);
                    m.setAccessible(true);
                    m.invoke(mgr);
                    sHttpInvoked = true;
                    return true;
                } catch (NoSuchMethodException ignored) {
                    // 换下一个候选方法名
                } catch (Throwable t) {
                    try { Log.w(TAG, "invoke " + mn + " failed: " + t); } catch (Throwable ignored) {}
                }
            }
        } catch (Throwable ignored) {}
        return false;
    }

    private static boolean serversUp() {
        return portOpen(PORT_HTTP) && portOpen(PORT_GO);
    }

    private static boolean portOpen(int port) {
        Socket s = null;
        try {
            s = new Socket();
            s.connect(new InetSocketAddress("127.0.0.1", port), 300);
            return true;
        } catch (Throwable t) {
            return false;
        } finally {
            if (s != null) {
                try { s.close(); } catch (Throwable ignored) {}
            }
        }
    }

    private static void sleep(long ms) {
        try { Thread.sleep(ms); } catch (Throwable ignored) {}
    }

    /** 供 /hai/ping 之类诊断接口回显。 */
    public static String status() {
        return "{\"httpInvoked\":" + sHttpInvoked
                + ",\"spiderInvoked\":" + sSpiderInvoked
                + ",\"sites\":" + sSites
                + ",\"http9978\":" + portOpen(PORT_HTTP)
                + ",\"go10079\":" + portOpen(PORT_GO) + "}";
    }
}
