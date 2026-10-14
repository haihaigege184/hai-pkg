# hai 本地包

> 面向 **影视 / OK影视 / TVBox(FongMi 系)** 的自维护接口包。
> 站点域名一直漂移，这个包会**自己把失效域名换掉**，让电视机长期不用管。

[![health](https://img.shields.io/badge/dynamic/json?url=https%3A%2F%2Fhai.haihaigege.dpdns.org%2Fhai%2Fversion.json&query=%24.sites&label=sites&color=brightgreen)](https://hai.haihaigege.dpdns.org/hai/jsm.json)

---

## 一、直接在电视机上用（推荐）

播放器里「点播接口 / 配置地址」填下面任意一条：

| 场景 | 地址 |
| --- | --- |
| **公网（任何网络）** | `https://hai.haihaigege.dpdns.org/hai/jsm.json` |
| **局域网（同一 WiFi，最快）** | `http://10.0.0.11:8099/hai/jsm.json` |

填完保存即可，不需要下载 zip、不需要解压、不需要手动上传 `js` / `lib`。

> 相对路径（`./pg.jar`、`./lib/*.json`）会按配置地址自动解析，
> 所以用哪条地址都行，包内资源会自动从同一台服务器加载。

### 网盘扫码（首次用要做的唯一一件事）

包里的 `lib/tokenm.json` 默认是空的，第一次播放阿里云盘 / 夸克 / UC 等网盘内容时，
播放器会弹二维码，用对应网盘 App 扫一下就行。也可以自己填：

```bash
# 在部署机 /root/hai/lib/ 下
cp tokentemplate.json tokenm.json
# 按 README.txt 里的字段说明填 token / cookie
```

---

## 二、它解决了什么问题

`pg` 这类包的抓取逻辑（`pg.jar`）本身没问题，坏的是**站点地址**：

| 站点 | 包内写死的地址 | 实际情况 |
| --- | --- | --- |
| 玩偶哥哥 | `wogg.888484.xyz` | 域名已废弃 → 现为 `woggpan.888484.xyz` / `www.wogg.live` |
| 木偶哥哥 | `123.666291.xyz` | 域名漂移 → `666.666291.xyz` 仍在，`muou.asia` 已是 DNSPod 停放页 |
| 蜡笔 | `xiaocgege.shop` | 已迁移到 `www.fmcf.fun` |
| 土豆 | `tudou.lvdoui.top` | 已跳转到 urldance 密码页（站点跑路） |

这些站用的是**同一套 jar 里的 CSP**，靠 `TAG` + 站点地址区分。
所以「换域名」是唯一有效修法 —— **改 jar 一点用都没有**。

本仓库把「**配置**」和「**地址**」彻底解耦：

```
公开社区配置 ──┐
              ├─→ 候选域名入池 → 健康巡检 → 选主 → 重新生成 jsm.json
站点名关键词 ──┤
历史域名知识库 ┘
```

---

## 三、健康巡检怎么判断「能用」

只按响应体大小判断会被骗 —— `muou.asia` 的 DNSPod **「域名已过期」停放页有 25KB**，
`urldance.com` 的**密码跳转页有 8KB**，都大于旧的 20KB 阈值。

所以改成**内容特征双判**：

| 判据 | 结果 |
| --- | --- |
| HTTP ≥ 400 / DNS 失败 / 连接超时 / SSL 异常 | `dead` |
| 响应体 < 800B | `dead` |
| 命中「域名已过期 / 请输入密码 / just a moment / urldance / dnspod …」**且**无站点特征 | `dead` |
| 带站点特征（`/vod` `/type/` `/search` `api.php` …）且 ≥ 100KB | **q3 完整真站** |
| 带站点特征 | **q2 真站** |
| 存活但无站点特征（门户页/空壳站） | q1 兜底，**不会被用来替换原地址** |

选主顺序：`q3 → q2 → q1`，同级看延迟。
只有 `q≥2` 才计入「可用」，通知里的「失效」因此是真的影响观看。

---

## 四、自动化维护

服务器上跑着一个中枢（`tools/` 里是它的全部源码）：

| 能力 | 说明 |
| --- | --- |
| 定时巡检 | 每 10 分钟探一次域名池 |
| 上游同步 | 每 6 小时拉 8 个公开社区配置 + 采集域名 |
| **每日巡检** | 每天 08:00 跑一次完整「同步 → 巡检 → 重生成」，并私聊管理员 |
| 失效告警 | 某接口由「有可用」变「无可用」时立即通知 |
| 恢复通知 | 失效接口重新拿到可用地址时通知 |
| 网关反代 | `/gw/<TAG>/…` 实时转发到当前最优域名，配置写死也不会随域名漂移失效 |

### QQ 命令（SEA 机器人插件）

插件在 `plugin/hai-package/index.js`，装到 SEA2 的 `plugins/` 目录即可。

| 命令 | 效果 |
| --- | --- |
| `修复接口` | 立即触发一次「同步 → 全池巡检 → 重生成 hai 包」 |
| `hai状态` | 查当前接口源数 / 可用域名数 / 失效率 |
| `hai帮助` | 命令列表 |

权限级别 2（管理员及以上），有 60 秒防连点。

---

## 五、自己部署一份

```bash
# 目标机：Debian 10+ / Python 3.8+
python3 -m venv /opt/oktv-hub/.venv
/opt/oktv-hub/.venv/bin/pip install flask requests
cp tools/deploy/oktv-hub.service /etc/systemd/system/
systemctl daemon-reload && systemctl enable --now oktv-hub
```

环境变量（都在 service 文件里）：

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `OKTV_PORT` | `8099` | 监听端口 |
| `OKTV_PKG_DIR` | `/root/hai` | 成品包目录（`jsm.json` + `pg.jar` + `js` + `lib`） |
| `OKTV_PUBLIC_BASE` | – | 对外地址，网关备份站点用它拼 |
| `OKTV_PREFIX` | – | 挂在子路径时填，如 `/oktv` |
| `OKTV_DAILY_HOUR` | `8` | 每日巡检时间 |
| `OKTV_SEA_CONFIG` | `/root/sea2/config.json` | 读 NapCat 地址/token/管理员 QQ |

公网入口（Cloudflare 远程托管隧道必须走 API，本地 YAML 会被云端覆盖）：

```bash
CF_API_TOKEN=xxx python3 tools/deploy/cf_public_ingress.py --apply
```

### HTTP 接口

| 路径 | 用途 |
| --- | --- |
| `/hai/jsm.json` | 成品配置（播放器用这个） |
| `/hai/pg.jar` · `/hai/js/*` · `/hai/lib/*` | 包内资源 |
| `/hai/version.json` | 版本 / 站点数 / 上次巡检时间 |
| `/gw/<TAG>/…` | 网关反代（域名漂移免疫） |
| `/api/status` `/api/pool` | 状态与域名池明细 |
| `/api/repair` `POST` | 一键检测修复（插件调它） |
| `/api/check` `/api/sync` `/api/render` `POST` | 单独跑巡检/同步/生成 |
| `/` | 暗色管理界面 |

---

## 六、目录结构

```
hai/
  jsm.json                    ← 当前发布的成品配置（快照，服务器实时生成）
plugin/
  hai-package/index.js        ← SEA 机器人插件
tools/
  app.py                      ← 中枢主服务
  core/
    config.py                 ← 上游源 / 别名 / 种子域名 / 阈值
    sources.py                ← ext 协议解析、域名规整、私有地址过滤
    health.py                 ← 探活 + 内容特征分级 + 选主
    sync.py                   ← 上游同步 + host/站名双通道域名采集
    patch.py                  ← 生成修复后的 jsm.json 并原子落盘
    gateway.py                ← /gw 反代（故障转移 + Location/Cookie 改写）
    notify.py                 ← SEA / NapCat 私聊通知
    store.py                  ← 状态与事件日志
  baseline/                   ← 原始基线（不带任何修复）
  web/index.html              ← 管理界面
  deploy/                     ← systemd / 部署脚本 / 隧道配置
```

---

## 七、已知边界

- **包内 14 个 `lib/*` 文件上游发行版本身就没有**（`tokenm.json`、`hdhaven.txt`、
  `panta.txt` …）。这不是本仓库造成的；`tokenm.json` 按官方说明由使用者从
  `tokentemplate.json` 复制。缺失的资源 404 后由播放器自行降级，与原版行为一致。
- 部分站点（土豆、盘链、盘Ta、至臻、栖影、大盘搜、88网盘、校长影视、聚影、观影）
  **从国内家宽已不可达**：DNS 废弃、502、Cloudflare 403 或跳转密码页。
  这类站点没有「正确域名」可换，接口会被标注 `⚠️失效`，等上游复活后自动恢复。
- 域名池只收**公网地址**：上游配置里大量 `http://127.0.0.1/…` 占位符必须过滤，
  否则播放器会去连自己。

---

## 八、免责声明

本仓库不存储、不分发任何影视资源，只维护「公开站点地址 → 播放器接口」的映射配置。
所有内容均来自互联网公开分享，如有版权问题请联系源站删除。
