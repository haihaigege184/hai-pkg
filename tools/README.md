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

## 7. 手工兜底

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

## 8. 目录结构

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
│  └─ gateway.py          /gw/<TAG>/ 反代（含故障转移、Location/Cookie 改写）
├─ baseline/              原始 jsm.json（基线，只读）
├─ assets/                完整 pg 包（pg.jar / js / lib），由中枢托管
├─ dist/jsm.json          生成好的成品配置
├─ deploy/                systemd 单元、一键部署、隧道 ingress 片段
├─ data/                  运行时数据（域名池、状态、事件日志、overrides）
└─ web/index.html         管理界面
```
