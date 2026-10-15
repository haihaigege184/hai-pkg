# OK影视 · 接口修复与热更新中枢（oktv-hub）

把「影视/OK影视」壳（`com.fongmi.android.tv`）外挂配置包 `pg.20261012-1935.zip`
里的失效接口救活，并让它**以后不会再失效**。

---

## 1. 问题根因（先说结论）

包里 79 个站点中，24 个是「网盘类接口」，它们的写法是：

```
ext = ./lib/tokenm.json$$$<站点根地址>$$$<proxy>$$$<flag>$$$<规则json>$$$<TAG>
                              ^^^^^^^^^^ 失效的就是这一段
```

- 抓取逻辑在 `pg.jar` 内（`csp_Wogg` / `csp_Mogg` / `csp_Wobg` / `csp_Hjzc`），**是数据驱动**的：
  同一个类靠 `TAG` + `<站点根地址>` 区分不同站点。
- 所以接口失效 = **站点换域名了**，不是代码坏了。改 `pg.jar` 毫无意义，改地址即可。
- 这些站换域名的频率极高，甚至用 `urldance.com` 这类域名轮换服务（`mogg.top`、`tudou.lvdoui.top` 都 302 到轮换域名）。
- 包里的上游发布源 `wogg.rr.nu` / `wogg.myfw.us` / `www.fish2018.us.kg` **已全部 DNS 解析失败**，靠包自身更新已经无望。

**结论**：必须把「配置」和「地址」解耦——配置里写稳定的中枢地址，地址由中枢动态维护。

---

## 2. 修复效果（实测）

| 接口 | 修复前 | 修复后 |
|---|---|---|
| 玩偶哥哥 Wogg | `wogg.888484.xyz` DNS 已废弃 | **`https://www.wogg.live/`**（自动采集到的最新域名） |
| 木偶哥哥 Mogg | `123.666291.xyz` | **`https://666.666291.xyz/`**（655KB 真实站点，另存 `www.muou.site` 备份） |
| 花卷 Hjzc | `hjzhencai.top` 时好时坏 | `https://www.hjzhencai.top/`（存活） |
| 土豆 TUDOU | — | 自动跟随轮换域名 |
| 域名池总体 | 24 条硬编码地址 | **61 条候选 / 28 条存活**（自动巡检得出） |

仍无可用域名的（站点确实已经跑路，等社区放出新域名后中枢会自动吸收）：
`PANTA 盘Ta`、`ZHIZHEN 至臻`、`MOGG 木偶哥哥`、`WOBG`、`WNLG`、`XIAOMI`、
`csp_Libvio`、`csp_DaPanSo`、`csp_EightEight`、`csp_Xb6v`、`TUDOU`，
以及 `LABI / HDHIVE` 各自尚未报废的那几个备用域名。

**注意：不再往站点名里写 `⚠️失效`。** 失效状态只在 `GET /api/sites`、`/live`
监视台和私聊告警里体现，配置本体与原版保持逐字一致（原因见 6.5 节）。

---

## 2.5 一次被实测推翻的误判（2026-10-15）

用户说「软件标注失效的接口实测可以使用」，一查**确实是我们判错了**：

| 站点 | 中枢原判 | 实测真相 |
|---|---|---|
| 蜡笔 `fmcf.fun` | dead（ConnectionError） | **200 + 623,811B 的真站页面**（瞬时失败被误判） |
| 聚影 `jying.top` | dead（停放页） | **200 + 3,619B 的 Vue 首页**（网盘 SPA 无 `/vod` 特征被误判） |
| 观影 / 栖影 / 影巢 | dead（CF 403 / 安全验证） | 数据中心 IP 被 WAF 拦，**家庭宽带可正常访问** |

修正判定口径后重跑全池巡检，**9 个 TAG 一次性恢复**：
`LABI`、`HUAMUCANG`、`GUANYING`、`PANLIAN`、`HDHIVE`、`hdhaven`、
`csp_XiaoZhang`、`csp_Meijumi`、`csp_PikaSo`。
同一时刻分布由「offline 8」变为 **ok 7 / guard 4 / offline 2**，
域名池存活 20 → 28。详见 **6.5 节**。

---

## 3. 工作原理

```
        ┌──────────────── 上游（7 个社区配置仓库 + 7 个采集源，多镜像）─────────────┐
        │  拉 jsm.json / js.json / 配置.json  →  抽 TAG + 域名  →  入池            │
        └──────────────────────────────┬──────────────────────────────────────────┘
                                       ▼
   ┌───────────  域名池（data/domains.json）  ───────────┐
   │  每个 TAG 一组候选域名，带 status / latency / bytes  │
   │  每 10 分钟并发巡检；重定向到新域名 → 自动吸收回池     │
   │  内容质量分：q2 真实站点 > q1 疑似停放页 > q0 失效     │
   └──────────────────────────┬──────────────────────────┘
                              ▼
   ┌───────────────  配置生成（每 60 秒或状态变更时）  ───────────────┐
   │  基线 jsm.json 里每个网盘接口的 ext 地址 → 换成当前最优域名       │
   │  可选同时输出一份「网关版」同源站点（走 /gw/<TAG>/ 反代）        │
   └──────────────────────────┬──────────────────────────────────────┘
                              ▼
                GET /jsm.json  →  APP 的「点播接口」
                /pg.jar /js/* /lib/*  一并托管，无需另装包
                /gw/<TAG>/<path>      网关反代，自动故障转移
```

三种模式（UI 可切换）：

| 模式 | 行为 | 适用 |
|---|---|---|
| `direct` | ext 直接写当前最优域名 | 链路最短，推荐日常用 |
| `dual` | direct + 额外输出网关版站点（`xxx_GW`） | 双保险 |
| `gateway` | ext 全部写中枢网关地址 | APP 配置写死后永不失效，多一跳 |

---

## 4. 接入方式

把 APP 的「点播接口」填成下面任意一个：

```
局域网（稳定、零依赖）：http://10.0.0.11:8099/oktv/jsm.json
公网快速隧道        ：https://<quick-tunnel>.trycloudflare.com/oktv/jsm.json
```

管理界面：`http://10.0.0.11:8099/oktv/`（暗色科技风，含域名池健康、一键巡检、上游同步、手工加域名、事件日志）

> `pg.jar` / `js/` / `lib/` 全部由中枢托管，`ext` 里的 `./lib/tokenlib.json`、
> `./lib/wogg.json` 等相对路径会自动解析到中枢，不用把 zip 再拷到设备上。

---

## 5. 部署与运维

```bash
# 从 Windows 开发机推送（首次会带 81MB 资产，之后用 --no-assets 只推代码）
python deploy/deploy.py
python deploy/deploy.py --no-assets

# 或直接在 11 上操作
systemctl status  oktv-hub
systemctl restart oktv-hub
journalctl -u oktv-hub -f
```

| 服务 | 说明 |
|---|---|
| `oktv-hub.service` | 中枢本体，监听 `127.0.0.1:8099` |
| `oktv-quicktunnel.service` | 免账号快速隧道，提供远程入口（URL 每次重启会变） |

后台定时任务（服务内线程，无需 cron）：巡检 10 分钟一次，上游同步 6 小时一次。

### 环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `OKTV_PORT` | 8099 | 监听端口 |
| `OKTV_HOST` | 0.0.0.0 | 绑定地址 |
| `OKTV_PREFIX` | 空 | 反代挂载前缀，如 `/oktv` |
| `OKTV_PUBLIC_BASE` | 空 | gateway 模式写入的对外地址 |
| `OKTV_LAN_BASE` | 空 | 局域网基址（只用于「接口状态」展示，留空自动探测） |
| `OKTV_HEALTH_INTERVAL` | 600 | 巡检周期（秒） |
| `OKTV_SYNC_INTERVAL` | 21600 | 上游同步周期（秒） |
| `OKTV_ABS_PATHS` | **0** | 相对路径绝对化（见 7 节，默认关） |
| `OKTV_MARK_NAMES` | **0** | 是否把 `⚠️失效` 写进站点名（默认关，见 6.5 节） |
| `OKTV_OFFLINE_CONFIRM` | 2 | 连续多少轮无可用才判失效并告警 |
| `OKTV_PROBE_ATTEMPTS` | 3 | 单地址探测重试次数（抗瞬时失败） |
| `OKTV_PROBE_RETRY_WAIT` | 1.0 | 重试间隔基数（秒），第 n 次失败等 `n × 该值` |
| `OKTV_RELAY_REWRITE` | **0** | 外部中转移写（见 7.5 节，确认链路可用后再开） |
| `OKTV_DAV` | 1 | 内置 WebDAV 端点（见 7.5 节） |
| `OKTV_DAV_PUBLIC` | 0 | WebDAV 是否放行公网来源 |

### CLI

```bash
python app.py run      # 启动服务
python app.py sync     # 只跑一次上游同步
python app.py check    # 只跑一次巡检
python app.py render   # 只重新生成一次配置
```

---

## 6. 公网入口现状（重要）

`cloudflared-backup` 这条隧道是 **Cloudflare 面板远程托管**的
（日志可见 `Updated to new configuration ... version=8`），
**本地 YAML 里加的 ingress 会被云端配置覆盖**，所以 `/oktv` 路径规则没有生效。

目前给了两个可用入口：

1. **局域网** `http://10.0.0.11:8099/oktv/jsm.json` —— 稳定，TV 盒子/手机同网即可用。
2. **快速隧道** —— 免账号，重启会换 URL。

要拿到永久公网域名，二选一：

- **给一个 CF API Token**（含 `Cloudflare Tunnel:Edit` + `DNS:Edit` 权限）：
  一次 API 调用即可把 `oktv.<你的域>` 写进隧道 ingress 并建好 DNS 记录；
- 或把该隧道改成**本地托管**（`cloudflared tunnel run` 不再拉取远程配置）。

---

## 6.5 失效判定口径（为什么「没响应」不等于「失效」）

用户反馈过两件事：「蜡笔接口反复失效和修复通知」「软件标注失效的接口实测可以
使用」。查下来**不是错觉，是探测口径的锅**。三条根因，逐条修掉：

### ① 瞬时网络失败被当成永久失效

实测：`fmcf.fun`（蜡笔）一次探测抛 `ConnectionError` 就被判死，随后手工访问
直接 **200 + 623KB** 的真站页面。DNS 抖动、对端限流、出口拥塞都会造成这种
一次性失败。现在 `probe()` 对**异常类失败**重试 `OKTV_PROBE_ATTEMPTS`（默认 3）
次（HTTP 层明确的 4xx/5xx 不重试，那是站点的确定回复）。

### ② 网盘聚合站的首页本来就不像影视站

`SITE_MARKERS`（`/vod` `/type/` `api.php` …）是 **MacCMS 影视站**的页面特征。
而蜡笔 / 聚影 / 盘链 / 观影 / 栖影 / 影巢 这些是**网盘聚合类**（PG 包里的
`csp_Wobg` / `csp_PingLian` / `csp_HdHaven` …），首页是 Vue/React SPA、
用 JSON 调后端，页面里根本没有 `/vod` 字样 → 一律被判「无站点特征」。

现在按 `NETDISK_API_HINTS` 识别出这类站点后放宽判定：
**像前端应用（有脚本/静态资源/挂载点）就按真站点算**，判活交给它的后端接口。
实测 `jying.top`（聚影，3.6KB Vue 首页）不再被判死。

### ③ Cloudflare / WAF 拦的是「我们这种 IP」，不是站点死了

`hdhaven.com`、`re0.me`、`观影` 的域名都返回 **CF 403 或「浏览器安全验证」页**。
数据中心 IP 被拦，但用户在家里的宽带上完全能用。原来把 CF 挑战页写在
`BAD_PAGE_HINTS` 里（和「域名已过期」并列），等于拿我们的网络环境去判用户的可用性。

现在拆出 `CHALLENGE_HINTS` 单独处理：这类页面判为**「存活但存疑」**（q1）——
**不替换原地址、不判失效、不告警**，只在明细里单列一档 `🛡`。告警判据也从
`usable_count(q>=2)` 放宽到 `survivable_count(q>=1)`：只要站点还有响应就不打扰，
真要判死必须是「一个响应的都没有」。

### 一并修掉的噪音源

* **不再往站点名里写 `⚠️失效`**（`OKTV_MARK_NAMES` 默认关）。除了破坏「与原版
  逐字一致」，端还会缓存配置 —— 等站点恢复了中枢已经去掉标注、端上那份还是旧的，
  于是又变成「恢复了标注还在」。失效状态改由 `/api/sites`、`/live`、通知表达。
* **告警/恢复通知加滞后**：连续 `OKTV_OFFLINE_CONFIRM`（默认 2）轮都无可用才判
  失效并告警；恢复通知只在**确实告过警**时才发，抖动轮次不再误报「已恢复」。

### 看结果：`GET /api/sites`

站点视角明细（79 条，与客户端里看到的一一对应），状态六档：

| 状态 | 图标 | 含义 |
|---|---|---|
| `ok` | ✅ | 已指向可用地址 |
| `guard` | 🛡 | 有响应但被 WAF/人机校验拦（不判失效） |
| `watch` | 👀 | 本轮无可用，未达判定阈值 |
| `offline` | ⚠️ | 已确认失效（会告警） |
| `unknown` | ❓ | 域名池里还没有这条记录 |
| `local` | 📦 | 纯本地脚本，不参与域名巡检 |

明细读的是**实际下发的那一份**配置（served → dist → baseline），所以 `marked`
（是否已标注）反映的是客户端真正拿到的东西，而不是永远为 0 的原版母本。

修完这一轮，同一时刻的分布从「offline 8」变成 **ok 7 / guard 4 / offline 2**，
域名池存活 20 → 28，`core_ok` 仍为 `true`、`renamed` 为 0。

---

## 7. 网盘 CK：一处保存，局域网多端自动生效

整包 79 个站点里有 **59 个**的 ext 第一段是 `./lib/tokenm.json` —— 夸克 / UC /
百度 / 115 / 迅雷 / 移动 / PikPak / 123 / 189 / 阿里 / 光雅 的凭据全部由这一个
文件承载（字段全集见 `lib/tokentemplate.json`）。所以只要把 tokenm.json 做成
**动态渲染 + 可写回**，就实现了「一台设置、全屋通用」。

### 三条写入入口（都在 `/hai/lib/tokenm.json`，任意方法）

| 入口 | 形态 | 用途 |
|---|---|---|
| `POST/PUT/PATCH/…` | JSON / form / multipart / 裸文本 | 端自己把凭据送回来 |
| query string | `?quark_cookie=…` | 简易上报 |
| 请求头 | `Cookie` / `X-CK` / `Authorization` | 复用现成凭据传递方式 |

识别出的值会走统一覆盖策略：**valid 覆盖一切；unverifed 只填空；invalid 绝不
覆盖 valid**（新值没验过不会顶掉已生效的旧值）。入库后立刻体现在下发的
tokenm.json 里，**其他设备下次加载配置即自动生效，无需再扫码**。

### 路径绝对化（`OKTV_ABS_PATHS`，**默认关**）

> 这一节记录一次**被实测推翻的判断**，留档避免以后有人再把它打开。

曾经的推理是：原版包为「zip 解压到本地」设计，`./lib/tokenm.json` 是**本地
文件路径**，HTTP 配置源下会被解析成端上的本地文件，导致 CK 回不到中枢。
于是默认把 `./lib/`、`./js/` 渲染成「客户端正在用的绝对地址」。

实测证明这条推理是错的，而且副作用不小：

1. **FongMi 官方文档写明「配置中的相对路径以配置文件 URL 为基准解析」**，
   原包本来就能正确取到 `./lib/tokenm.json`；中枢也早已注册 `/lib/<path>`、
   `/js/<path>` 兼容路由，实测 `/lib/tokenm.json` 返回 200、内容与
   `/hai/lib/tokenm.json` 逐字节一致。
2. 绝对化把 ext 里的地址写成**请求所用的 Host** —— 从 `127.0.0.1` 访问就写成
   `127.0.0.1`，从公网入口访问就写成公网域名。同一份配置因入口不同而不同，
   端一旦换入口就会指向错误地址。
3. 它一次改掉 **63 个站点的 ext + 2 条 lives 的 url**，外观与原版不再逐字一致，
   违反「不精简原版」这条红线 —— 用户侧的直接感受就是「跟原包有区别」。

现在默认关闭（`OKTV_ABS_PATHS=0`）。实测端请求日志里 `/hai/lib/tokenm.json`
被取 55 次、`/lib/tokenm.json` 6 次，**唯一的 404 来自我自己的 curl 测试**，
端从未因相对路径取不到文件 —— 也证明「起播慢」不能归因到这一层。

确实需要时置 `OKTV_ABS_PATHS=1`；公网入口要配合放行 `/lib` 与 `/js`
（见 `deploy/cloudflared-ingress.yml`）。

### 看得见：探针 + 台账 + 监视台

CK 采集天生有盲区：扫码动作和网盘 API 请求**都不经过中枢**，中枢只能看到
「客户端主动送回来」的那些请求。所以这里不做取舍，全部留证：

* `GET /api/probe` —— 写入探针：非 GET、带 query、带 CK 特征头的请求原样落盘
  （`logs/probe.jsonl`，含 method / query / body 片段 / 关键头 / UA）
* `GET /api/devices` —— 客户端台账：谁连过、请求数、**有没有把 CK 送回来过**
* `POST /api/probe/reset`（别名 `POST /api/devices/reset`）—— **清零基线**：
  清空探针记录与客户端台账，便于干净地做一次真机测试。
  只清「证据」，不动 `data/ck.json` 里已入库的凭据（凭据要清请用 `/api/ck/drop`）。
  `/live` 上有对应的「🧹 清零基线」按钮。
* `GET /live` —— 手机浏览器打开，每 3 秒刷新，**边在 OK影视 里操作边看请求有没有回来**

排查「CK 没同步上」时先看 `/live`：只有 `GET` 说明端只是在取配置，
CK 还留在端自己身上。

中枢自身的管理/自测流量（`/api/status`、`/api/probe/reset`…）**既不进探针也不进台账**
（`probe.is_ignored()`），否则清零动作本身会被立刻记成一次写入、把干净基线弄脏。

### 安全

* 真实 CK **只回局域网来源**；公网来源拿到的是空模板（`X-CK-Scope: public-masked`）
* 身份判定只看 `CF-Connecting-IP` / `X-Real-IP` / `remote_addr`，
  **刻意不读 `X-Forwarded-For`** —— CF 是在客户端已有 XFF 后面 append 真实地址的，
  取第一段等于允许任何人伪造 `X-Forwarded-For: 10.0.0.108` 冒充内网设备把 CK 拿走
* 公网入口（`hai.haihaigege.dpdns.org`）的 ingress 只放行 `/hai` 与 `/gw`，
  `/api/ck`、`/ck`、`/live` 本身在公网不可达

### 若要新增网盘渠道

在 `core/ckvault.py` 的 `CHANNELS` 里加一条（字段名必须与
`lib/tokentemplate.json` 一致），再按需在 `core/ckcheck.py` 里加登录态校验。

---

## 7.5 让凭据「自己走回来」：外部中转 + WebDAV 端点

前面三节解决的是**下发**（一处保存、多端生效）。真正的难点是**采集**：

> 手机在 OK影视 里扫码设好了 CK，凭什么会送到中枢？

静态分析 `pg.jar`（dex 字符串表被混淆，但 ASCII 串可读）之后，链路是这样的：

| 动作 | 走什么 | 中枢能否看到 |
|---|---|---|
| 扫码授权 | 网盘官方 OAuth（端直连） | ✗ |
| 播放取流 | 网盘 CDN（端直连） | ✗ |
| 读配置 | `ext` 指向的 `tokenm.json` | ✓（但只有读） |

端对 `tokenm.json` **只 GET、从不 PUT/POST**（实测：真实客户端 `okhttp/5.0.0-alpha.14`
每 15~30 秒 GET 一次，零写入）。所以必须把「本该直连第三方」的那一步引到中枢。

### A. 外部中转 —— 把凭据换取请求接到中枢（`core/relay.py`）

PG 的 `tokenm.json` 里有一批指向外部服务的字段，最典型的是：

```json
"open_api_url": "postparam|http://api.extscreen.com/aliyundrive/token"
```

`postparam|` 是 PG 的 ext 协议动词，**端会把凭据 POST 到这个 URL** 换取 access_token。
这个文件由中枢渲染，所以改一行就能把它引到 `/gw/ali/token`：

```
端 POST → /gw/ali/token → 原样透传 → 真正的上游
                       ↘ 请求体/响应体就地提取凭据 → 入库
```

* 路由在 `core/relay.py` 的 `DEFAULT_ROUTES`；上游地址、字段映射、渠道都可配
* **只有显式登记过的路由才转发**，未登记的一律落回域名池反代（不能让中枢变成开放代理）
* 开关：`POST /api/relay {"rewrite": true|false}`，或管理页/`/live` 上的按钮
* 原始记录落 `logs/relay.jsonl`（含请求体、响应体、命中字段）

> ⚠️ 关于 `api.extscreen.com`：**先前的「整站 404」结论要修正**。
> 那个 404 是对**根路径** `/` 的响应；`/aliyundrive/token` 本身返回的是
> **403 + 10.6KB 自定义错误页**（蓝色 `#39f` 模板，不是 Cloudflare），
> 说明接口存在、只是拒绝了假 token。但它**按 UA 分流**：换成客户端同款
> `okhttp/5.0.0-alpha.14` 去请求会变成 `404 page not found` —— 即
> **PG 原包里这个换 token 的地址已经和上游对不上了**（换域名/换协议）。
> 这不是我们改坏的，打开中转移写反而会把这个问题提前暴露；所以
> `OKTV_RELAY_REWRITE` 保持关闭，等确认要用再开。

### B. WebDAV 端点 —— PG 原生、由端主动发起的双向通道（`core/dav.py`）

「网盘及弹幕配置」里有一个 **WEBDAV 缓存网盘信息**，对应 PG 包内的
`com.github.catvod.spider.WebDAVConfig`。若干凭据类会把登录态以 `<key>.json`
PUT 上去（字符串证据）：

```
GuangYaUser            -> guangya.json
HDHavenCredentials     -> hdhaven.json
HdhiveCredentials      -> hdhive.json
Pan123OpenCredentials  -> 123_open.json
YoutubeCredentials     -> youtube.json
Bili.User              -> Cookie 刷新
```

地址存在 SharedPreferences 的 `global_panconfig_webdavconfig_address`（UI 就是那个输入框）。

**这条通道比 A 更有价值**：它是端**主动发起**的外发请求，不受「端不回写」的限制。
把地址填成中枢，链路就变成「端 ⇄ 中枢」：

```
PUT  端备份凭据上来 → 中枢落盘 data/dav/ + 提取入库   ← 采集
GET  端点「恢复」   → 中枢把手里的那份给它           ← 下发
```

* 地址：`http://10.0.0.11:8099/gw/dav`（挂在 `/gw` 下是刻意的 ——
  CF 隧道 ingress 只放行 `/hai` 与 `/gw`，这样局域网与公网同形）
* 账号密码**随便填**，中枢不校验（端会带它自己配置的那份，中枢只吞下来记录）
* 真正的门禁是来源网段：默认只放行局域网/回环（`OKTV_DAV_PUBLIC=1` 才放行公网）
* 实现了 `OPTIONS / PROPFIND(Depth 0,1) / MKCOL / GET / PUT / DELETE`；
  `/gw/dav` 与 `/gw/dav/` 都能直接命中（不依赖 308 —— PROPFIND/PUT 被重定向基本就废了）
* 记录落 `logs/dav.jsonl`，状态见 `GET /api/dav`

> ⚠️ 静态证据里，**阿里云盘不在 WebDAV 同步白名单内**。所以：
> 阿里云盘走 A（`open_api_url`），影巢/栖影/123开放/光雅/YouTube/B站 走 B。
> 两者都实测过链路，但「端到底发出哪些请求」必须在真机上确认。

---

## 8. 手工兜底

上游也找不到时，在管理界面的「加入池」里填 `TAG` + 域名即可，会立刻探测并入池；
或直接改 `data/overrides.json`：

```json
{
  "drop_keys": ["蜡笔网盘"],
  "add_sites": [
    {
      "key": "MySource", "name": "我的源|网盘", "type": 3,
      "api": "csp_Wobg", "quickSearch": 1, "changeable": 1, "filterable": 1,
      "timeout": 60,
      "ext": "./lib/tokenm.json$$$https://example.com/$$$noproxy$$$1$$$./lib/wogg.json$$$MYTAG"
    }
  ]
}
```

---

## 9. 目录结构

```
oktv-hub/
├─ app.py                 Flask 服务 + 路由 + 后台调度 + CLI
├─ core/
│  ├─ config.py           常量：上游仓库、采集源、TAG 别名、内置域名知识库
│  ├─ store.py            JSON 原子读写 + 事件日志
│  ├─ sources.py          ext 解析 / 域名规整与判重
│  ├─ health.py           并发巡检 + 质量打分 + 选主
│  ├─ sync.py             上游同步 + 通用域名采集
│  ├─ patch.py            生成修复后的 jsm.json
│  ├─ gateway.py          /gw/<TAG>/ 反代（含故障转移、Location/Cookie 改写）
│  ├─ ckvault.py          网盘 CK 保险库（提取/覆盖策略/渲染 tokenm）
│  ├─ ckcheck.py          网盘登录态真实校验
│  ├─ probe.py            写入探针（CK 到底有没有被送回来）
│  ├─ devices.py          客户端台账（谁连过/有没有贡献 CK）
│  ├─ report.py           接口状态明细（站点视角 79 条 + 局域网/外网地址）
│  ├─ relay.py            外部中转（把凭据换取请求接到中枢，旁路采集）
│  ├─ dav.py              内置 WebDAV 端点（接管 PG 的「WEBDAV 缓存网盘信息」）
│  ├─ libfill.py          lib 资源自动补全（防 404 风暴）
│  └─ absbase.py          相对路径绝对化（按客户端所见地址渲染，默认关）
├─ baseline/              原始 jsm.json（基线，只读）
├─ assets/                完整 pg 包（pg.jar / js / lib），由中枢托管
├─ dist/jsm.json          生成好的成品配置
├─ _work/                 开发期自测脚本（不入仓：巡检样本、插件自检、端到端）
├─ deploy/                systemd 单元、一键部署、隧道 ingress 片段、sshx 远程工具
├─ data/                  运行时数据（域名池、状态、事件日志、CK 保险库、设备台账）
└─ web/
   ├─ index.html          管理界面
   ├─ ck.html             手机捕获台（手动粘贴兜底）
   └─ live.html           CK 同步监视台（实时看请求有没有回来）
```

## 10. 机器人命令（`plugin/hai-package`）

私聊、仅超级管理员有效：

| 命令 | 作用 |
|---|---|
| `修复接口` | 触发一次完整闭环：上游同步 → 全池巡检 → 重新生成并落盘 `/root/hai/jsm.json`（约 1~3 分钟，先回执后异步私聊回报） |
| `接口状态` | 汇总：各状态数量 + 域名池存活 + 上次巡检时间 + **局域网/外网两个接口地址** + 失效清单 |
| `接口明细` | 逐条列出全部 79 个接口（状态图标 / 域名 / 延迟），每 28 条分页，第 2 页起走私聊 |
| `hai帮助` | 命令说明 |

状态图标：`✅` 正常 · `🛡` 存疑（WAF 拦，不判失效） · `👀` 观察中 ·
`⚠️` 已确认失效 · `❓` 域名池未建立 · `📦` 本地脚本。
明细里若出现 `🔖标注过期`，表示配置里标着失效但实测有可用地址 —— 发一次
`修复接口` 即可重算清掉。
