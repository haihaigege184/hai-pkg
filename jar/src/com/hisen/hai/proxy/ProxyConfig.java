// -*- coding: utf-8 -*-
package com.hisen.hai.proxy;

import android.util.Base64;

import java.io.UnsupportedEncodingException;
import java.net.URLDecoder;
import java.util.LinkedHashMap;
import java.util.Map;

/**
 * PG「R 加速代理」协议常量与编解码。
 *
 * <p>逆向来源（全部实证，见 jar/SPEC.md）：
 * <ul>
 *   <li>电视播放百度 25.3GB 原盘时，9978/media 返回
 *       {@code http://127.0.0.1:10078/p/32/null/1048576/<base64url>/xxx.mkv}</li>
 *   <li>pg.jar 里 {@code com/github/catvod/parser/merge/R/a.smali} 负责拼代理 URL：
 *       {@code b()} -> /cfgo/，{@code c()} -> /p/0/，{@code d()} 返回基址，
 *       且优先读全局配置 {@code global_goproxy_base}（R/a.smali:381）。</li>
 *   <li>基址默认 {@code http://127.0.0.1:10078}（R/a.smali:53），
 *       备选 {@code http://192.168.101.151:10078}（:55），探测后缓存 5 分钟。</li>
 *   <li>服务本体是 APK assets 里的 {@code go_proxy_video}（AArch64 Go ELF, 9.48MB），
 *       由 APK 侧 {@code com.fongmi.android.tv.server.Server} 复制到 filesDir 启动，
 *       <b>实际监听 10079</b>；10078 由 iptables REDIRECT 转发过去。9978 是 APK 侧
 *       NanoHTTPD 服务（{@code Proxy}/{@code Input}/{@code Process}），负责 /media 等管理接口
 *       与 {@code Proxy.proxy(Map)} 站源分发。</li>
 *   <li><b>2026-11-08 逆向修正</b>：/p/ 里的 {@code 32} 不是线程数，而是
 *       {@code NanoHTTPD$Constants.DEFAULT_BUFFER_SEGMENTS = 0x20}（32 个 1MB 缓冲段）。
 *       当前 APK 的 {@code HaiApiService} 注释原文「多线程下载…实测反而更慢，已禁用」，
 *       实际是<b>单连接顺序流式透传 + 大缓冲预读</b>。峰值 50MB/s 由此而来，不是并发。</li>
 * </ul>
 *
 * <p>本类只做协议层，不含任何业务逻辑，便于逐字对照原版。
 */
public final class ProxyConfig {

    /** jar 内 R/a.smali:53 写死的端口。端上原版 Go 代理监听 10079，
     *  10078 由 iptables REDIRECT 转发过去（设备全局规则，原版 OK影视 也走这条）。 */
    public static final int PORT = 10078;
    public static final String DEFAULT_BASE = "http://127.0.0.1:" + PORT;

    /**
     * 海哥影视 Java 代理固定监听端口（2026-11-08 定案）。
     * 用 10090 而非 10079：10079 是原版 Go 代理的端口，原版 OK影视 仍走
     * 全局 REDIRECT 10078→10079；我们给海哥影视单独建一条
     * 「uid-owner 匹配 com.hisen.android.tv → REDIRECT 10078→10090」的规则，
     * 两条链路互不抢占，实现双包共存。 */
    public static final int HAI_PORT = 10090;

    /**
     * 站源/配置中枢（服务器）。2026-11-09 定案：播放链路完全回归 PG 原版 Go，
     * Java 代理只负责「APK 内置电视墙 UI」这一条同源链路：
     *   APK assets 提供墙页 HTML（本地、离线可见）→ 页面内 /hai/* 相对路径
     *   全部落到 127.0.0.1:10090 同源 → 本 jar 反代到下面的 UPSTREAM。
     * 这样服务器地址只存在于 jar（可热更新），APK 里零硬编码；
     * 后续把各接口逐步本地实现即可渐进裁撤服务器，UI 不用重打包。
     */
    public static volatile String UPSTREAM = "http://10.0.0.11:8099";

    /** APK assets 里内置电视墙页路径（build_hai_tv.py 的 step_assets 复制进去）。 */
    public static final String WALL_ASSET = "haiui/wall.html";

    /** 原版实测 URL 段：/p/32/null/1048576/...
     *  32 = 缓冲段数（DEFAULT_BUFFER_SEGMENTS），1048576 = 每段 1MB。
     *  注意：历史记忆里写的「32 线程 × 1MB 分块」是当时的推测，已被逆向证据推翻。 */
    public static final int DEFAULT_SEGMENTS = 32;
    public static final int DEFAULT_CHUNK = 1024 * 1024; // 1048576

    /** 可选并发加速模式的路数（11-04 自研 HaiDp 8路×4MB 实测 11.5MB/s）。 */
    public static final int DEFAULT_THREADS = 32;

    /** 在途分片窗口（仅并发模式用）。不是把整部片切成 10 万个 Future，而是滑动窗口，
     *  这是「多大网盘资源都能起播」且不吃内存的关键。 */
    public static final int DEFAULT_WINDOW = DEFAULT_THREADS * 2;

    /** 流式模式的预读管道缓冲。原版是 32×1MB，端上折中取 8MB 防 OOM。 */
    public static final int PIPE_BUFFER = 8 * 1024 * 1024;

    /** 播放模式：STREAM = 原版等价（默认）；CONCURRENT = 可选并发加速。 */
    public static final int MODE_STREAM = 0;
    public static final int MODE_CONCURRENT = 1;
    public static volatile int sMode = MODE_STREAM;

    public static final int CONNECT_TIMEOUT_MS = 10_000;
    public static final int READ_TIMEOUT_MS = 15_000;
    public static final int MAX_RETRY = 2;

    /** 全局开关：允许把基址指到我们自己的服务（对应原版 global_goproxy_base）。 */
    public static volatile String sBase = DEFAULT_BASE;

    private ProxyConfig() {}

    /** base64url -> 原始字符串。原版 URL 用的是 url-safe 变体，无 padding。 */
    public static String b64Decode(String s) {
        try {
            int flags = Base64.URL_SAFE | Base64.NO_WRAP;
            byte[] raw = Base64.decode(s, flags);
            return new String(raw, "UTF-8");
        } catch (Throwable t) {
            // 兼容某些站源用了标准 base64（含 + / =）
            try {
                byte[] raw = Base64.decode(s, Base64.DEFAULT | Base64.NO_WRAP);
                return new String(raw, "UTF-8");
            } catch (Throwable t2) {
                return "";
            }
        }
    }

    public static String urlDecode(String s) {
        try {
            return URLDecoder.decode(s, "UTF-8");
        } catch (UnsupportedEncodingException e) {
            return s;
        } catch (Throwable t) {
            return s;
        }
    }

    /**
     * 解析 headerEnc 段。原版传 {@code null} 表示无自定义头；
     * 非空时是 URL 编码过的 {@code K=V&K2=V2}（也可能带 ';' 分隔）。
     * 对百度网盘尤其关键：Cookie(BDUSS/STOKEN) + 长 UA + origin=dlna 全靠它透传。
     */
    public static Map<String, String> parseHeaders(String enc) {
        Map<String, String> out = new LinkedHashMap<String, String>();
        if (enc == null || enc.length() == 0 || "null".equals(enc)) return out;
        String s = urlDecode(enc).replace(';', '&');
        for (String pair : s.split("&")) {
            int i = pair.indexOf('=');
            if (i <= 0) continue;
            String k = pair.substring(0, i).trim();
            String v = urlDecode(pair.substring(i + 1).trim());
            if (k.length() > 0) out.put(k, v);
        }
        return out;
    }

    /** 由文件名后缀猜 Content-Type —— 播放器靠它选 extractor/mimetype。 */
    public static String mimeOf(String name) {
        String n = name == null ? "" : name.toLowerCase();
        if (n.endsWith(".mkv")) return "video/x-matroska";
        if (n.endsWith(".mp4") || n.endsWith(".m4v")) return "video/mp4";
        if (n.endsWith(".ts")) return "video/mp2t";
        if (n.endsWith(".m3u8")) return "application/vnd.apple.mpegurl";
        if (n.endsWith(".avi")) return "video/x-msvideo";
        if (n.endsWith(".mov")) return "video/quicktime";
        if (n.endsWith(".flv")) return "video/x-flv";
        if (n.endsWith(".webm")) return "video/webm";
        if (n.endsWith(".mp3")) return "audio/mpeg";
        if (n.endsWith(".m4a")) return "audio/mp4";
        if (n.endsWith(".flac")) return "audio/flac";
        return "application/octet-stream";
    }
}
