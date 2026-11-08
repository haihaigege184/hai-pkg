# PG 播放链路完全逆向规格 · 2026-11-08

> 目标（海哥定案）：**播放链路用 PG 原版 / 自重构 jar 完全掌控，站源照搬；UI 美化走 APK 内置本地 HTML；服务器只做站源与配置更新下发。**
> 本文件是「完全逆向 PG」的一手结论，所有条目带证据路径与行号，未确认项明确标注。

---

## 0. 结论先行

| 问题 | 结论 |
|---|---|
| 页面级 UI 在哪 | **APK dex**（30 个 Activity），jar 里只有少量自定义 View |
| 站源在哪 | **pg.jar / classes.dex**，`com/github/catvod/spider/` 456 个类 |
| `csp_*` 映射表在哪 | **不在 jar**，在 APK 侧 `"com.github.catvod.spider." + api.substring(4)` 字符串拼接反射 |
| 播放代理是不是一个服务 | **不是，是两套**：9978 = APK 侧 NanoHTTPD 服务（`Server`/`Input`/`Process`/`Proxy`，站源分发 + /media 管理接口）；10079 = Go 二进制流式媒体代理，10078 只是 iptables REDIRECT 转发端口 |
| R 加速是什么 | `/p/32/null/1048576/<b64url>/<name>` = **32 个 1MB 缓冲段的单连接流式透传**（`NanoHTTPD$Constants.DEFAULT_BUFFER_SEGMENTS=0x20`），服务本体是 APK `assets/go_proxy_video`（AArch64 Go ELF 9.48MB）。**32 不是线程数** |
| 为什么能 50MB/s、任意大文件秒起播 | 零等待起播（上游第一个字节即写播放器）+ 大缓冲预读吸收抖动 + 内存恒定 + 管道背压。当前 APK 源码注释明确「多线程下载实测反而更慢，已禁用」 |
| 「站源照搬 + 全进 jar」怎么同时成立 | 原版 `classes.dex` **一个字节不动**，我们的逻辑进 **`classes2.dex`**（DexClassLoader 原生支持多 dex）。但 APK 侧 `Server`/`Input` 仍在，要完全掌控还需处理启动权 |

---

## 1. 双端口拓扑（本轮最重要的发现）

```
APK 播放器
   │  GET http://127.0.0.1:10078/p/32/null/1048576/<b64url>/xxx.mkv   ← R 加速（媒体流）
   │      ↓ iptables REDIRECT 10078 → 10079
   ▼
[10079] go_proxy_video  ← APK assets/go_proxy_video，AArch64 Go ELF 9,482,456 B
   │      由 APK 侧 com/fongmi/android/tv/server/Server 复制到 filesDir + chmod + exec
   │      单连接顺序流式透传 → 网盘 CDN（大缓冲 32 段 × 1MB）
   │
   │  真链从哪来？
   ▼
[9978] APK 侧 NanoHTTPD 服务  ← com/fongmi/android/tv/server/{Server,Input,Process,Proxy}
   │      Input.a() 路由：/media /device /parse /log /cache /proxy?do=...
   │      Process.do() → Proxy.proxy(Map) 硬编码分发 → 站源 static proxy(Map) → 真 dlink+header
   │      jar 侧 com/github/catvod/spider/Proxy.smali:6 的 a:I=0x26fa(9978) 只是客户端常量
   ▼
站源类（456 个，pg.jar/classes.dex 原封不动）
```

**注意**：`DownloadProxyServer`/`Input`/`Server`/`Proxy` 这些服务类都在 **APK dex**，不在 jar。
所以「全进 jar、完全掌控播放代理链路」除了下发 classes2.dex，还必须解决
**APK 侧服务的启动权**（见第 4 节接管点）。这是本轮逆向最重要的架构结论。

补充证据：

| 项 | 证据 |
|---|---|
| `Proxy.a = 0x26fa` | `.tmp/dec_pg/smali/com/github/catvod/spider/Proxy.smali:6` |
| `getPort()` 返回 `sget a:I` | 同文件 `:502-541`（控制流平坦化 sparse-switch，实际返回静态字段） |
| `getUrl()` / `getReflectionUrl()` | `:1008` / `:543`（字符串常量被 Unicode 混淆，运行时解密） |
| 10078 基址 | `com/github/catvod/parser/merge/R/a.1.smali:53` `http://127.0.0.1:10078`；`:55` 备选 `192.168.101.151:10078` |
| 基址可覆盖 | `R/a.1.smali:381` `global_goproxy_base`，`:377 d()` 优先读它、去掉尾部 `/`，否则 ping 探测后缓存 5 分钟（`:38 0x493e0`） |
| jar 内 URL 包装 | `R/a.1.smali:137 b()` → `/cfgo/`；`:257 c()` → `/p/0/`；`:161 /cfgo/null/`、`:281 /p/0/null/`、`:318 /p/0/` |
| 10079 另一组路由 | `parser/merge/q.1/a.smali:48` `http://127.0.0.1:10079/av3am3u/0/null/`；`S/u0.smali:6492` 同族 |
| YouTube 路由 | `parser/merge/Y/J.smali:257 /yt/nsig`、`:690 /yt/enhance`、`:1637 /yt/ping` |
| `/ping` 健康检查 | `youtube/sabr/SabrAbrClient.smali:1670 /ping`、`:1682 http://127.0.0.1:10078` |
| 1MB 常量 | `l.1/b2.smali:11105`、`l.1/t1.1.smali:16124`、`S/u0.smali:1529` `0x4130000000000000 = 1048576.0` |
| 真机实测 URL | `.workbuddy/memory/2026-10-18.md:12`、`2026-11-04.md:8`（9978/media 抓到 `/p/32/null/1048576/...`，logcat `DownloadProxyServer.serv final url:`） |
| **32 = 缓冲段数** | APK `com/fongmi/android/tv/server/NanoHTTPD$Constants.smali:42` `.field public static final DEFAULT_BUFFER_SEGMENTS:I = 0x20` |
| 多线程被禁用 | APK `HaiApiService.java:16956` 注释「多线程下载…实测反而更慢，已禁用」，改为单连接顺序流式透传 |
| Go 二进制启动 | APK `com/fongmi/android/tv/server/Server.smali` 复制 `assets/go_proxy_video` 到 filesDir、chmod、exec；端口 **10079** |
| iptables 转发 | APK `oOoOo0oOoO0oOoO.smali:256-302` REDIRECT 10078 → 10079（历史上会「莫名丢」，第一检查项） |
| 站源拼播放 URL | `Quark.smali:2785`、`UC.smali:2772`、`Baidu.smali:3363` 直调 `Proxy.getPort()/getUrl()` |
| 9978 路由分发 | APK `Input.smali` → `Process.do()` → `Proxy.proxy(Map)`（硬编码分发到 20+ 站源静态 proxy） |

**真机取证补充**（2026-11-08 09:40）：TV 上 `com.fongmi.android.tv`(PID 4542) 与 `com.hisen.android.tv`(PID 9521) 共存；`files/` 只有 `pg.jar`（4,614,411 B），**没有**释放的 `go_proxy_video`；未播放时 `/proc/net/tcp` 无 9978/10078/10079 监听 ⇒ **服务按需启动**，`go_proxy_video` 是播放时才释放执行的。

---

## 2. R 加速代理协议（逐段语义）

```
http://127.0.0.1:10078/p/<threads>/<headerEnc>/<chunkBytes>/<base64url(真链)>/<文件名>
                          │          │            │              │            └─ 给播放器看名字/后缀，决定 mimetype
                          │          │            │              └─ URL-safe base64，无 padding，解开是真实 dlink
                          │          │            └─ 每片字节数，实测 1048576 (1MB)
                          │          └─ null = 无自定义头；否则 URL 编码的 K=V&K2=V2，透传 Cookie/UA/Referer
                          └─ 缓冲段数（不是线程数！）= NanoHTTPD$Constants.DEFAULT_BUFFER_SEGMENTS 0x20；
                             0 = 不做分段（jar 内 R/a.c() 用的就是 /p/0/）
```

另一种格式（jar 内 `R/a.c()` 造的，`:257`）：`/p/0/null/<URLEncoder.encode(url)>` —— **只有 3 段、用 URL 编码而非 base64**，我们的服务两种都兼容。

普通请求代理：`/cfgo/null/<urlEncoded>`、`/cfgo/<hdrEncoded>/<urlEncoded>`（`R/a.b()`，`:137`），用于爬虫/配置请求；`R/a.i()` 的日志原文是 `MyRequest.requestForResponse try cfgo first`（`:1640`），说明请求优先走 cfgo。

### 为什么是「动态加载速度」（原版真实机制）

| 机制 | 作用 | 丢了会怎样 |
|---|---|---|
| **零等待起播** | 请求进来立刻向上游发单连接 GET（带 Range），第一个字节到达就写给播放器 | 变成「全量下载完才播」，100GB 原盘起不来 |
| **大缓冲预读** | 独立预读线程把上游流灌进 32 段 × 1MB 缓冲，吸收网络抖动 | 速度随 RTT 抖动，峰值上不去 |
| **内存恒定** | 不管 4K 还是 100GB，缓冲恒定，绝不缓存整片 | OOM / 首帧被长下载饿死 |
| **管道背压** | 缓冲满则预读线程阻塞，播放器读慢不会吃满带宽 | 无谓预取、发热卡顿 |
| **单连接顺序** | 当前 APK 源码注释「多线程下载…实测反而更慢，已禁用」 | 多线程争抢同一 CDN 连接反而降速 |
| **Range 透传** | 播放器 seek 带 `bytes=N-`，直接透传给上游 | seek 后从头拉 |
| **header 透传** | 百度 dlink 必须长 UA + BDUSS/STOKEN + `origin=dlna`（11-04 实证：短 UA 403、裸链 69KB/s、加 origin=dlna 7.16MB/s） | 403 / 31045 / 31326 |

> 记忆里「32 线程 × 1MB 分块」（2026-11-04 日流水）是当时的**推测**，本轮逆向已推翻，
> 以本文件为准。并发不是原版默认，但我们的实现仍保留为**可选加速模式**
> （`ProxyConfig.MODE_CONCURRENT`，11-04 自研 HaiDp 8 路 × 4MB 实测 11.5MB/s）。

> 实现见 `jar/src/com/hisen/hai/proxy/RangeStream.java`
> （`openStream()` 原版等价流式 + `openConcurrent()` 可选并发滑动窗口）与
> `DownloadProxyServer.java`（`/ping`、`/p/`、`/cfgo/`、`/hai/*` 路由）。

---

## 3. 站源照搬：ABI 契约（必须逐字保持）

> 本节结论来自 4436 个 smali 的逐类核验（csp-rev 全量报告），纠正了两个常见误解。

### 3.1 `csp_*` 映射真相

**jar 内没有映射表**。`Init.smali`（59358 行 / 98 方法）不是 csp 工厂，
`spider/` 下 `csp_` 零命中。反射拼接全在 **APK 侧**：

```
android/support/v4/media/session/OoOoOoOoO0oOoO0o.smali:2109
  方法 (String key, String api, String ext, String jarPath) -> Spider
  :2119 ".js" / :2131 ".py" / :2143 "csp_" + startsWith 三路分流
  :2540  const-string v2, "com.github.catvod.spider."
  :2676  api.split("csp_")  →  :2684 [1]  →  :2688 append
  :2701  DexClassLoader.loadClass() → :2709 newInstance() → :2717 check-cast Spider
  :2725  Spider.init(Context, ext)  → :2730 缓存
```

⇒ **类名 = `"com.github.catvod.spider." + api.split("csp_")[1]`**。
`Init` 的真实作用是 jar 全局初始化：APK `OoOo0o0o0O0o0OoO.1/oOoOo0O0Oo0o0OoO.smali:3830-3878`
反射 `loadClass("com.github.catvod.spider.Init")` + `getMethod("init",[Context.class])`，
对应 `spider/Init.smali:39045 .method public static init(Landroid/content/Context;)V` —— **该签名绝对不能删**。

**照搬清单**：下发配置去重 65 个 `csp_*`，全部能在 `spider/` 找到同名 `.smali`，**0 缺失**：

```
AList AliShare AlistShare BLSGod Config Czsapp DaPanSo Douban FirstAid FourKZN
Funletu Guazi HDHaven Hjzc HongGuo HuamuCangShare HunHePan Kugou Libvio Local
Market Mayiys Meijumi MiaoSou Mipan Mogg Moli P115Share P123Share P189Share
PanSearch PanSouGo Panyq PikPakShare PikaSo PingLian Push PushShare QuarkPanso
QuarkShare Qwmp4 SambaShare SeedHub TGDouban TGYunPan TGYunPanLocal TTian
ThunderShare TianYiSou UCShare UpYun WebDAV Wo4k Wobg Wogg Xb6v XiaoZhang
YiSo YingSo Yinyuetai Youtube Ysj YunPan YunPanOne YunSo
```
（`push_agent` = `csp_Push`；`QuarkShare`/`UCShare`/`BaiduShare` 的 `.super` 分别是 `Quark`/`UC`/`Baidu`。）

### 3.2 Spider 基类在 APK，不在 jar

`com/github/catvod/crawler/Spider.smali` 位于 **APK 主 dex**（`public abstract`），
jar 下无 `crawler/` 目录；219 个站源顶级类全部 `.super` 它，
靠 DexClassLoader 的父委托拿到基类。**15 个可覆写虚方法**（默认返回 `""`/`null`/`false`）：

```
init(Context)V · init(Context,String)V · homeContent(Z)String · homeVideoContent()String
categoryContent(String,String,Z,HashMap)String · detailContent(List)String
searchContent(String,Z)String · searchContent(String,Z,String)String
playerContent(String,String,List)String · proxyLocal(Map)[Object · action(String)String
liveContent(String)String · manualVideoCheck()Z · isVideoFormat(String)Z · destroy()V
```
静态工具：`client()OkHttpClient`（:19）、`safeDns()Dns`（:34）。
兄弟实现：`SpiderNull`（加载失败兜底）、`com/fongmi/quickjs/crawler/Spider`（js）、
`com/undcover/freedom/pyramid/Spider`（py）。**第二个工厂：未找到**（`Init$Loader` 只是单例 holder）。

### 3.3 取流关键签名：*Share 子类不覆写

| 类 | `detailContent(List)String` | `playerContent(String,String,List)String` | `static proxy(Map)[Object` |
|---|---|---|---|
| Quark | `Quark.smali:684` | `:2969` | `:365` |
| UC | `UC.smali:682` | `:2954` | `:366` |
| Baidu | `Baidu.smali:982` | `:3571` | `:470` |

**`QuarkShare`/`UCShare`/`BaiduShare` 三个子类都不覆写 detailContent / playerContent / proxy**，
取流完全继承父类。它们只覆写 `categoryContent`、`homeContent`、两个 `searchContent`、
`init(Context,String)`、`client()`，外加私有分享链接拆分 `b/c/d/e`
（QuarkShare:99、UCShare:72、BaiduShare:81/167/469/773）——
把整条分享 URL 规范化、抽出 share_id + 提取码 + 子路径，编码进 `vod_id`（`主id$$$附加`），
再交回父类取流。`splitDetailId` 不在网盘类里，而在
`AiSubtitleService.smali:34148`（字段 a/b，`$$$` 分隔），被 22 类共用。
cookie/token 无显式方法名，经 `init(Context,String)` 的 `ext` 传入且字符串 XOR 加密。

### 3.4 456 类分组（顶级 219 / 内部类 237）

| 组 | 数量 | 典型 |
|---|---|---|
| 单字母/混淆辅助类 | 56 | A..Z, a.1, n.1, r |
| **网盘取流/代理核心** | **28** | Quark UC Baidu Ali P115 P123 P189 PikPak Thunder YD AList WebDAV Samba Local **Proxy** Push UpYun Panta QuarkPanso YunPan YunPanOne GuangYa PPMag HongGuo Hdh HdhSecure Bili Youtube |
| 网盘 `*Share` 分享子类 | 14 | QuarkShare UCShare BaiduShare AliShare AlistShare P115Share P123Share P189Share PikPakShare ThunderShare SambaShare PushShare GuangYaShare HuamuCangShare |
| 聚合/网盘搜索/磁力 | 25 | PanSearch PanSou PanSouGo PikaSo DaPanSo YiSo YingSo YunSo TianYiSou HunHePan Panyq PingLian SeedHub MiaoSou TGDouban TGYunPan… |
| 工具/服务/解析/加密/字幕 | 30 | Init Config Notice XPath* XBPQParser AiRecommendService AiSubtitleService SubtitleProxyCache DeepSeekWebClient MiguAuth… |
| 其余影视/内容/直播站源 | 68 | AppYsV2 BLSGod FourKZN Iqiyi JavBus Kugou Libvio Mogg Moli Netflav Qwmp4 Sukebei Wogg Xb6v YouKu Ysj… |

### 3.5 照搬可行性（决定我们怎么改）

**加载层可行**：APK 只依赖三样 ——「类名 = 去 csp_ 前缀」「Spider 15 个签名」「Init.init(Context)」。

**但「只换代理」会断**，耦合点密集：
- `Proxy.getPort()/getUrl()` 被 **27 类 37 处直调**；`Proxy.smali:1166 proxy(Map)`
  硬编码分发到 20+ 站源静态 `proxy(Map)`（:3787~:4021、:4788、:5209、:5216）；
- 12 个网盘类直调 `Init.initAliProxy(String)`（Quark:2444、UC:2441、Baidu:2996）；
- `Init` 被全站源当全局单例直引（`context()` 389 次）；
- jar 内**无类覆写 `proxyLocal`**，csp 取流走
  `Proxy.getUrl → jar 内 HTTP 服务器 → Proxy.proxy(Map) → 站源.proxy(Map)` 独立链路。

⇒ **必须保留 `Proxy` / `Init` 外壳签名，只换内部实现**。
我们采用「新增 classes2.dex、完全不碰 classes.dex」正是最安全的形态：
不改任何原有类，只是**占住 10078 端口**把 R 加速末段接管过来，
9978 的 `Proxy` 分发链与 456 个站源原封不动继续工作。

---

## 4. 「站源照搬 + 全进 jar」落地方案

原版 `pg.jar` 结构（实测）：9 个条目，`classes.dex` = 11,449,536 B，其余是
`assets/*`（ebook.mp4 / input.html / mic.xml / po_token.html / push.html / silence.mp3.gz）、
`META-INF/MANIFEST.MF`、`ShA1sUm.dat`。

Android `DexPathList` 对同一 jar 会依次加载 `classes.dex`、`classes2.dex` …
⇒ **原版 dex 一个字节不改，我们的逻辑编进 `classes2.dex` 追加进去**。

```
pg.jar
├─ classes.dex   11,449,536 B   456 站源类 + Init + Proxy + 包名白名单  ← 照搬，sha256 前后一致
├─ classes2.dex      27,588 B   com.hisen.hai.*  ← 我们完全掌控
│   ├─ HaiCore                    入口（APK 反射调 start(Context)）
│   ├─ proxy/DownloadProxyServer /ping /p/ /cfgo/ /hai/*
│   ├─ proxy/RangeStream         32 路 × 1MB 滑动窗口并发流
│   ├─ proxy/ProxyConfig         协议常量 / b64url / header 解析 / mimetype
│   └─ api/HaiApiServer         同形 /hai/* 数据层 + 站源页数据 + 豆瓣图 Referer 代理
└─ assets/* / MANIFEST.MF / ShA1sUm.dat  原样
```

构建：`python jar/build_hai_jar.py`（javac → classes.jar → d8 → classes2.dex → 合并 → 自检）。
**自检会断言原版 `classes.dex` 逐字节一致**，不一致直接失败。
产物落在 `jar/out/pg.jar`，**不覆盖基线、不上传**；`--deploy` 才上传 `/root/hai/pg.jar`（自动先打服务器 tar 基线）。

**回滚**：删掉 `classes2.dex` 即纯原版；或直接用 `/root/oktv-pre-*.tar.gz` 还原。

### 接管点（部署时必须处理，否则掌控不了）

**现状**：APK 侧 `Server` 启动 Go 二进制监听 **10079**，并有 iptables 把 **10078 → 10079**。
播放器拿到的 URL 写的是 10078。所以我们的 Java 服务要真正接管，有三条路：

1. **改 REDIRECT 目标（最轻，推荐）**：保留现有 10078 入口，把 iptables 规则改成
   `10078 → <我们的端口>`，我们的 `DownloadProxyServer` 监听那个端口。
   Go 二进制可以不起，也可以留着做 A/B 对照。
   ⚠️ 记忆红线：这条 REDIRECT 规则「会莫名丢，第一检查项」，已写进 `install-recovery.sh`。
2. **直接占 10079**：让 `Server` 跳过 Go 二进制启动（APK 侧一行 smali），
   我们的服务监听 10079，现有 REDIRECT 不用改。一劳永逸但要重打包 APK。
3. **`global_goproxy_base`**：`R/a.d():381` 是 jar 侧官方覆盖点，把 `R/a` 的
   `/yt/*`、`/ping` 等请求基址指到我们服务，**无需改字节码**，但只覆盖 R/a 这一条支线，
   不覆盖 `Proxy.getUrl()` 主链。

另外，**9978 的 `Server`/`Input`/`Process` 在 APK dex，不在 jar**：
`classes2.dex` 与它们是不同类路径，可以共存但不会自动替换。
若要把 9978 的站源分发也收进 jar，需要 APK 侧把启动指向我们的类，
或让 `Proxy.proxy(Map)` 的分发结果由我们的服务代理 —— 属于阶段 3。
本阶段先做到「10078/10079 的 R 加速末段完全由 jar 掌控」，9978 保持原版照搬。

---

## 5. APK 侧接入（阶段 2，本轮未部署）

| 项 | 现状 | 要改成 |
|---|---|---|
| 详情页 URL | `WebDetailActivity.smali:237` 硬编码 `http://10.0.0.11:8099/hai/poster/d/` | `file:///android_asset/haiui/detail.html?vid=...` |
| WebView 设置 | `onCreate:148-252` 已开 JS / DOM Storage / WideViewPort，cacheMode=2 | 追加 `setAllowFileAccessFromFileURLs(true)`、`setAllowUniversalAccessFromFileURLs(true)`；注入 `HAI_BASE` |
| jar 服务启动 | 无 | `onCreate` 加一行 `invoke-static {p0}, Lcom/hisen/hai/HaiCore;->start(Landroid/content/Context;)V` |
| UI 资源 | 服务器渲染 | 阶段 1 已产出 `android/assets/haiui/`（CSS 408 行 + JS 1396 行 + index/detail.html + bridge.js/inject.js） |

> smali 红线（用户长期记忆）：手写 smali **结尾只到 `.end method`，不要 `.end class`**；
> `invoke-*` 描述符必须单行；`.locals` 要覆盖所有 v 寄存器。

---

## 6. 服务器瘦身范围（下一阶段，本轮未动）

停用（只留回滚档）：`_eps_build` / `core/pan_ep.py` / `core/panwall.py` /
`/hai/poster/play` / `/hai/dp` / `/hai/hls` / `HaiDp` 分片 / 百度 HLS 退化 /
自研剧集合并 / `play_mode` 灰度 / 海报墙 HTML 渲染。

保留：`jsm.json` 渲染（`ABS_JSM` 绝对化 + `spider=file:///.../pg.jar`）、
`lib/*.js` 站源脚本、pg.jar 下发、更新检查、`/api/verify`、`/live` 维护页。

---

## 6.5 设置站源页复刻规格（真机观察 + 已确认数据结构）

> 来源：2026-11-08 真机截图逐帧操作（`_work/absjsm_setting.png` / `absjsm_ok.png` /
> `absjsm_input.png` / `absjsm_list*.png`）+ Config 表 / prefs 直接读取。
> 深层 smali 调用链仍在逆向中，本节只写**已实证**的部分。

### 页面结构与遥控操作（1920×1080）

| 元素 | 位置 / 操作 | 行为 |
|---|---|---|
| 「点播」配置行 | y≈88，输入框 x≈800，右侧按钮 x≈1828 | 第一行；输入当前配置地址 |
| 配置地址历史列表 | `input tap 1828 88` → **必须补 `keyevent 23`** | 弹出历史列表，↑↓ 选择；TV 上 `input tap` 只移焦点，不触发点击 |
| 地址输入对话框 | `input tap 800 88` + `keyevent 23` | 输入框预填 `http://10.0.0.11:8099/hai/jsm.json`，「确定」按钮 x≈1151,y≈683 |
| 重选触发 | 弹「确定」后 | 重新 HTTP 拉 jsm → 解析 → 落 Config 表 → 站点重建（11-08 已修爆栈） |

**踩坑**：连按 7 次下键会越界退出弹窗；`input tap` 只移焦点，必须补 `keyevent 23`。

### 数据存储（已实证）

| 存储 | 内容 |
|---|---|
| SQLite `/data/user_de/0/com.hisen.android.tv/databases/tv` 表 `Config` | 列 `id/type/url/time/json`；激活行 = **原版 `AppDatabase.getConfig(type)`：`type=? ORDER BY time DESC LIMIT 1`**（不要求 json 非空）。`type` 语义：**0=点播 / 1=直播 / 2=壁纸**，订阅 urls 是展开成多条 `type=0` 行，**不是** type=1（旧记忆写错，已修正）。实测激活 id=49 |
| SharedPreferences `com.hisen.android.tv_preferences.xml` | `config_0/config_1/config_2` **不是历史**，是 `Config.update()` 按 `"config_"+type` 写的当前 URL 指针；历史列表来自 Config 表 SQL。另有 `site_mode`（0 默认/1 显示分类/2 隐藏分类）、`config_cache` |
| 表 `Site` | `CREATE TABLE Site(key TEXT NOT NULL, searchable INTEGER, changeable INTEGER, PRIMARY KEY(key))` — 站源勾选状态；`Site.save()` 事务写入，`Site.sync()` 只同步已启用的 |
| 冷启动 | HomeActivity.onCreate → `ApiConfig.load()`（读 Config 表激活行）→ `loadConfig()`；**json 缓存非空时走「解析缓存」分支，不联网**。只有「设置→配置」重选才真正 HTTP 拉 jsm |

### 已落地的复刻（`android/assets/haiui/settings.html`）

- 左侧 **9 行设置导航**：`点播/直播/壁纸/播放/网盘及弹幕配置/数据/字幕/其他/关于`（真机坐标一致）；
  ↑↓ 移动，OK / → 进入右栏，← / 返回键回左栏。
- **焦点区状态机** `nav → hist / dlg / sites / play / info`：
  - 点播行 → 配置历史（↑↓ 选，OK 把历史 URL 回填输入框并进对话框）
  - 对话框 → 4 按钮 `取消 / 确定 / 扫码 / 历史`（←→ 选，OK 执行）+ 二维码占位
  - 网盘及弹幕配置行 → 站源列表（↑↓ 选站源，←→ 切「可搜 / 可换」，OK 保存）
  - 播放行 → 代理链路只读信息；其余行 → 只读镜像
- 键码两套都认：WebView 送 DOM `37/38/39/40`，原生 `19/20/21/22`；OK `23/66/13`，返回 `4/27`。
- 数据源全部走 jar 本地服务，零服务器依赖：`/hai/site/active`（激活 Config 行）、
  `/hai/site/configs`（Config 表 `type=0` 按 time 倒序的历史）、`/hai/site/list`
  （sites 数组 + 合并 `Site` 表偏好）、`/hai/site/prefs`（写回 `Site` 表）、`/hai/ping`。
- **站源勾选写回 App 原生 `Site(key,searchable,changeable)` 表**，不是 WebView localStorage；
  保存按请求计数，全部返回后才刷新，避免写一半重载。`HaiApiServer.dbPath()` 用
  `Context.createDeviceProtectedStorageContext().getDatabasePath("tv")` 取真实路径，硬编码仅作回退。
- 视觉：深色透明卡片、发光青/紫字、焦点霓虹描边 + 位移（海哥既定风格）。

**复刻检查清单（剩余）**：① 配置地址「确定」真正写库并触发 App 重载（需调原版
`Config.update()` / `ApiConfig.load()`，jar 侧目前只读镜像，避免与 App 主线程抢锁）；
② 扫码按钮接相机（WebView 无相机能力，需原生桥）；③ `site_mode` 三模式 UI 切换；
④ 原版页面每项背后的 smali 调用链深挖；⑤ 带 `classes2.dex` 的 jar 真机部署与验证。

---

## 7. 待补 / 未确认

- ~~设置站源页逐控件复刻规格~~ → 已完成并落地（见 6.5 节）；剩余是「确定」写库、扫码、
  `site_mode` 三模式的实操接线，见上方清单。
- `go_proxy_video` 内部字符串被混淆/无明文路由常量（ELF AArch64、Go build info 不可见）；
  协议结论来自**真机抓包 + jar 侧 URL 构造**双向印证，不是从二进制反汇编得到。
- `/yt/*`、`/av3am3u/*` 目前只占位返回 501，YouTube / 直播链路尚未复刻。
- 9978 `Proxy.proxy(Map)` 分发表的全量 `do` 值尚未逐项导出。
- 真机尚未加载过带 `classes2.dex` 的 jar（本轮只构建 + 自检，未部署）。

---

## 8. 本轮验证记录

| 项 | 结果 |
|---|---|
| Java 源码编译 | 5 个文件全部通过（固定项目 JDK 17 + android.jar API34） |
| d8 → classes2.dex | 成功，31,844 B（含站源页接口、Site 表写回、dbPath 兜底） |
| 合并自检 | 原版 `classes.dex` sha256 `21076c730cbe7bae…` 前后完全一致；产物 4,624,241 B，md5 `b7f3e99e7eba996d7d3c313222548437` |
| settings.html | 内联 JS `node --check` 通过；本地引用 `style.css` 存在，缺失 0 |
| 现网状态 | 未部署；`/root/hai/pg.jar` 仍是原版 `d8c3bd96…`，`core_ok=true`，`play_mode=legacy` |
| 基线 | `v1.0-apkui-baseline`、`/root/oktv-pre-apkui-20261108.tar.gz` |
