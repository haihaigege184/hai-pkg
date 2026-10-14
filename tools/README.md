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
| 域名池总体 | 24 条硬编码地址 | **52 条候选 / 25 条存活**（自动巡检得出） |

仍无可用域名的（站点确实已经跑路，等社区放出新域名后中枢会自动吸收）：
`LABI 蜡笔`、`XIAOMI 小米网盘`、`ZHIZHEN 至臻`、`WOBG 玩偶表哥`、`WNLG`、
`HDHIVE`、`PANLIAN`、`PANTA`、`WONIU`、`csp_DaPanSo`、`csp_EightEight`、
`csp_Libvio`、`csp_XiaoZhang`、`hdhaven`。
这些源在配置里会被自动标成 `⚠️失效`，不会再干扰用户点选。

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
| `OKTV_HEALTH_INTERVAL` | 600 | 巡检周期（秒） |
| `OKTV_SYNC_INTERVAL` | 21600 | 上游同步周期（秒） |

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

### 路径绝对化（`OKTV_ABS_PATHS=1`，默认开）

原版包是为「zip 解压到本地、配置指向本地 jsm.json」设计的，那种场景下
`./lib/tokenm.json` 是一个**本地文件路径**。而我们的用法是 HTTP 配置源 ——
同一个字符串到底被解析成网络地址还是端上的本地文件，取决于 PG 内部实现。
一旦落到端本地，扫码得到的 CK 就永远回不到中枢。

所以下发配置时会把 `./lib/`、`./js/` 渲染成**该客户端自己正在用的绝对地址**：

```
请求 http://10.0.0.11:8099/hai/jsm.json        -> http://10.0.0.11:8099/hai/lib/tokenm.json
请求 http://10.0.0.11:8099/oktv/hai/jsm.json   -> http://10.0.0.11:8099/oktv/hai/lib/tokenm.json
请求 https://hai.haihaigege.dpdns.org/hai/jsm.json -> https://…/hai/lib/tokenm.json
```

基准是**配置根目录**（jsm.json 所在目录），少一层或多一层都会让端去取一个
不存在的文件。对「本地解压」用法的端没有影响 —— 那种端根本不经过这次渲染。

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

> ⚠️ 实测 `api.extscreen.com` 目前整站返回 404（连根路径都是），这个默认上游
> 很可能已经停用。**通道本身是通的**（已验证：透传 + 采集 + 记录全部生效），
> 但端到底会不会请求它、请求时用的什么地址，只能靠真机实测。

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
│  └─ absbase.py          相对路径绝对化（按客户端所见地址渲染）
├─ baseline/              原始 jsm.json（基线，只读）
├─ assets/                完整 pg 包（pg.jar / js / lib），由中枢托管
├─ dist/jsm.json          生成好的成品配置
├─ deploy/                systemd 单元、一键部署、隧道 ingress 片段、sshx 远程工具
├─ data/                  运行时数据（域名池、状态、事件日志、CK 保险库、设备台账）
└─ web/
   ├─ index.html          管理界面
   ├─ ck.html             手机捕获台（手动粘贴兜底）
   └─ live.html           CK 同步监视台（实时看请求有没有回来）
```
