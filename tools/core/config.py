"""oktv-hub 全局配置与常量"""
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")
DIST_DIR = os.path.join(BASE_DIR, "dist")
LOG_DIR = os.path.join(BASE_DIR, "logs")
BASELINE_JSM = os.path.join(BASE_DIR, "baseline", "jsm.json")
BASELINE_LIB = os.path.join(BASE_DIR, "baseline", "lib")
# 原版清单（文件清单 + 关键字段指纹），供 /api/verify 做一致性核对
BASELINE_MANIFEST = os.path.join(BASE_DIR, "baseline", "manifest.json")

# 长期维护的「hai 本地包」目录（对外发布的成品包）
# 结构： <PKG_DIR>/jsm.json + pg.jar + js/ + lib/
PKG_DIR = os.environ.get("OKTV_PKG_DIR", "/root/hai")
PKG_NAME = os.environ.get("OKTV_PKG_NAME", "hai")

# 运行时数据文件
F_DOMAINS = os.path.join(DATA_DIR, "domains.json")    # 域名池（含健康状态）
F_SOURCES = os.path.join(DATA_DIR, "sources.json")    # 接口源注册表（TAG -> 元数据）
F_STATE = os.path.join(DATA_DIR, "state.json")        # 运行状态（上次同步/巡检时间等）
F_LOG = os.path.join(DATA_DIR, "events.jsonl")        # 事件日志（追加）
F_OVERRIDES = os.path.join(DATA_DIR, "overrides.json")  # 人工修正：手工加域名/禁用域名/模式
F_CK = os.path.join(DATA_DIR, "ck.json")              # 网盘 CK 保险库（一处保存/多端同步）
F_DEVICES = os.path.join(DATA_DIR, "devices.json")    # 客户端台账（谁来过/有没有贡献 CK）
F_PROBE = os.path.join(LOG_DIR, "probe.jsonl")        # 写入探针（CK 到底有没有被送回来）
F_RELAY = os.path.join(DATA_DIR, "relay.json")        # 外部中转路由表（显式登记才转发）
F_RELAY_LOG = os.path.join(LOG_DIR, "relay.jsonl")    # 中转流量原始记录（旁路采集证据）
F_DAV_LOG = os.path.join(LOG_DIR, "dav.jsonl")        # WebDAV 交互记录（端主动备份/恢复）
# 巡检状态：{TAG: {alive, ts, streak, notified_offline}}
#   streak           = 连续「一条可用地址都没有」的轮数（用于抑制抖动）
#   notified_offline = 本轮掉线是否已经告过警（避免反复骚扰）
F_HEALTH_STATE = os.path.join(DATA_DIR, "health_state.json")

# 实际下发给客户端的那一份配置 / 本地生成的那一份。
# 「接口状态」明细要读这两者而不是 baseline —— baseline 是原版母本，
# 永远不含失效标注，拿它算「是否已标注」恒为 0，正是用户看到
# 「明细说正常、配置里却标着失效」的原因（2026-10-15 修正）。
F_SERVED = os.path.join(PKG_DIR, "jsm.json")
F_DIST_JSM = os.path.join(DIST_DIR, "jsm.json")

# 服务参数
HOST = os.environ.get("OKTV_HOST", "0.0.0.0")
PORT = int(os.environ.get("OKTV_PORT", "8099"))
# 对外可访问地址（写入 jsm.json 的网关模式用；末尾不带 /）
PUBLIC_BASE = os.environ.get("OKTV_PUBLIC_BASE", "").rstrip("/")
# 局域网可达基址（只用于「接口状态」这类展示：告诉用户电视机该填哪个地址）。
# 留空则自动探测本机局域网 IP。
LAN_BASE = os.environ.get("OKTV_LAN_BASE", "").rstrip("/")
# 反向代理挂载前缀，如 hub 挂在 https://host/oktv 下则填 /oktv（默认无前缀）
PREFIX = os.environ.get("OKTV_PREFIX", "").rstrip("/")

# 巡检 / 同步周期（秒）
HEALTH_INTERVAL = int(os.environ.get("OKTV_HEALTH_INTERVAL", "600"))     # 10 分钟
SYNC_INTERVAL = int(os.environ.get("OKTV_SYNC_INTERVAL", "21600"))       # 6 小时
# 每日巡检 + 报告时间（24 小时制，服务器本地时间）
DAILY_HOUR = int(os.environ.get("OKTV_DAILY_HOUR", "8"))
DAILY_MINUTE = int(os.environ.get("OKTV_DAILY_MINUTE", "0"))
# 发现失效时是否立即私聊管理员
NOTIFY_ON_FAIL = os.environ.get("OKTV_NOTIFY_ON_FAIL", "1") == "1"
NOTIFY_ON_FIX = os.environ.get("OKTV_NOTIFY_ON_FIX", "1") == "1"
NOTIFY_DAILY = os.environ.get("OKTV_NOTIFY_DAILY", "1") == "1"

HEALTH_TIMEOUT = int(os.environ.get("OKTV_HEALTH_TIMEOUT", "12"))
HEALTH_WORKERS = int(os.environ.get("OKTV_HEALTH_WORKERS", "16"))

# 网盘 CK 相关
# 请求级自动嗅探：任何带 CK 的请求经中枢且 2xx 时，自动入库并计一次播放成功
CK_AUTOSNIFF = os.environ.get("OKTV_CK_AUTOSNIFF", "1") == "1"
# 是否允许把真实 CK 下发给**公网**来源（默认关闭：公网只拿到空模板）
CK_PUBLIC = os.environ.get("OKTV_CK_PUBLIC", "0") == "1"
# 入库时是否自动做网盘登录态校验
CK_VALIDATE = os.environ.get("OKTV_CK_VALIDATE", "1") == "1"
# 接受 CK 上报的来源网段（逗号分隔前缀；空 = 允许所有来源）
CK_ALLOW_PREFIX = [x.strip() for x in
                   os.environ.get("OKTV_CK_ALLOW", "").split(",") if x.strip()]
# 写入探针：记录所有可能携带 CK 写入意图的请求（非 GET / 带 query / 带 CK 特征头）
PROBE_ENABLED = os.environ.get("OKTV_PROBE", "1") == "1"
# 相对路径绝对化：把配置里的 ./lib/ ./js/ 渲染成「客户端当前所用的中枢地址」。
#
# 【默认关闭 —— 2026-10-15 修正】
# 曾经默认开启，理由是「消除端把 ./lib/ 当成本地文件路径的歧义」。实测证明这条
# 理由是错的，而且副作用不小：
#   1. FongMi 官方文档明确写着「配置中的相对路径以配置文件 URL 为基准解析」，
#      原包本来就能正确解析 ./lib/tokenm.json；中枢也早就注册了 /lib/<path> 与
#      /js/<path> 兼容路由（见 app.py），实测 /lib/tokenm.json 返回 200 且内容
#      与 /hai/lib/tokenm.json 完全一致。
#   2. 绝对化会把 ext 里的地址改写成**请求所用的 Host**——从 127.0.0.1 访问就
#      写成 127.0.0.1、从公网入口访问就写成公网域名。同一份配置因入口不同而
#      不同，端一旦换入口就会指向错误地址。
#   3. 它把 63 个站点的 ext、2 条 lives 的 url 全改了，外观与原版不再逐字一致，
#      违反「不精简原版」这条红线；用户侧直观感受就是「跟原包有区别、变慢」。
# 需要恢复时置 OKTV_ABS_PATHS=1。公网入口若要配合，需让 CF ingress 放行
# /lib 与 /js（见 deploy/cloudflared-ingress.yml）。
ABS_PATHS = os.environ.get("OKTV_ABS_PATHS", "0") == "1"
# 外部中转改写：把 tokenm.json 里 open_api_url 之类「指向第三方」的地址改写成
# 中枢自己的 /gw/<tag>/<path>，透传上游并旁路采集凭据。
# 默认**关闭** —— 打开会改变端的凭据换取路径，确认中转链路可用后再启用。
RELAY_REWRITE = os.environ.get("OKTV_RELAY_REWRITE", "0") == "1"

# 内置 WebDAV 端点：接管 PG「WEBDAV 缓存网盘信息」的备份/恢复通道。
# 端主动 PUT 凭据上来 → 中枢落盘并入库；端 GET 恢复 → 中枢把最好的那份给它。
DAV_ENABLED = os.environ.get("OKTV_DAV", "1") == "1"
# 是否允许公网来源读写（默认否：只放行局域网/回环，避免成为公开的凭据中转）
DAV_PUBLIC = os.environ.get("OKTV_DAV_PUBLIC", "0") == "1"
HEALTH_MIN_BYTES = 800          # 响应体小于该值视为无效页（机房停放页/空页）

UA = ("Mozilla/5.0 (Linux; Android 12; SM-G981B) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36")

# 上游配置镜像（按优先级尝试；{repo} / {path} 会被替换）
UPSTREAM_JSM_MIRRORS = [
    "https://fastly.jsdelivr.net/gh/{repo}@main/{path}",
    "https://ghproxy.net/https://raw.githubusercontent.com/{repo}/main/{path}",
    "https://gh-proxy.com/https://raw.githubusercontent.com/{repo}/main/{path}",
    "https://raw.githubusercontent.com/{repo}/main/{path}",
]

# 上游配置仓库。格式 "owner/repo:路径" 或 "owner/repo@分支:路径"
UPSTREAM_REPOS = [
    "pkujf/TVBOX:jsm.json",
    "angelo0o0/tv:jsm.json",
    "gaotianliuyun/gao:jsm.json",
    "gaotianliuyun/gao@master:js.json",
    "luotong1995/gao@master:js.json",
    "BartleySun/tvList@master:0825.json",
    "aiwu12/tBox:配置.json",
    "muzili0712/tBox:安卓配置.json",
    "qist/tvbox@master:jsm.json",       # 收录 150+ 站点，救命源
]

# 通用域名采集源：这些社区的配置里散落着各站点当前域名，
# 直接正则扫出所有 URL，再按关键词归到对应 TAG（host 匹配，避免误收）。
# 上游 jsm.json 停更时，这里往往还有人在维护最新域名。
HARVEST_SOURCES = [
    "https://fastly.jsdelivr.net/gh/pkujf/TVBOX@main/jsm.json",
    "https://fastly.jsdelivr.net/gh/angelo0o0/tv@main/jsm.json",
    "https://fastly.jsdelivr.net/gh/gaotianliuyun/gao@master/js.json",
    "https://fastly.jsdelivr.net/gh/BartleySun/tvList@master/0825.json",
    "https://fastly.jsdelivr.net/gh/aiwu12/tBox@main/配置.json",
    "https://fastly.jsdelivr.net/gh/muzili0712/tBox@main/安卓配置.json",
    "https://fastly.jsdelivr.net/gh/qist/tvbox@master/jsm.json",
]

# TAG 关键词别名：采集到的 host 命中任一关键词即归入该 TAG
TAG_ALIASES = {
    "WOGG": ["wogg", "wanou", "wo4k"],
    "MOGG": ["mogg", "muou", "666291"],
    "LABI": ["labi", "xiaocgege", "duopan", "fmcf"],
    "WOBG": ["wobg", "wobge", "yydsys"],
    "XIAOMI": ["mucpan", "mihdr"],
    "ZHIZHEN": ["mihdr", "zhizhen"],
    "TUDOU": ["lvdoui", "tudou"],
    "HJZC": ["hjzhencai", "huajuan"],
    "WNLG": ["yydsys"],
    "PANLIAN": ["pinglian"],
    "PANTA": ["panta"],
    "WONIU": ["wn4k", "woniu"],
    "HUAMUCANG": ["jying", "huamucang"],
    "GUANYING": ["guanying"],
    "HDHIVE": ["hdhive"],
    "hdhaven": ["hdhaven"],
    "csp_Libvio": ["libvio"],
    "csp_Meijumi": ["meijumi"],
    "csp_Moli": ["hdmoli"],
    "csp_PikaSo": ["pikaso"],
    "csp_Xb6v": ["xb6v", "66ss"],
    "csp_Qwmp4": ["qwmp4"],
    "csp_YunSo": ["yunso"],
    "csp_Qianfan": ["qianfan"],
    "csp_DaPanSo": ["dapanso"],
    "csp_EightEight": ["662688"],
    "csp_XiaoZhang": ["xzyshd"],
}

# 站名关键词：社区配置里站点名字相对稳定，而域名一直换。
# 只要有人在配置里写了「盘Ta」这个站，就把他写的地址收进 PANTA 的域名池。
TAG_NAME_KEYS = {
    "WOGG": ["玩偶哥哥", "玩偶"],
    "MOGG": ["木偶哥哥", "木偶"],
    "LABI": ["蜡笔"],
    "PANTA": ["盘Ta", "盘TA", "盘ta"],
    "PANLIAN": ["盘链"],
    "WONIU": ["蜗牛"],
    "HUAMUCANG": ["聚影"],
    "GUANYING": ["观影"],
    "HDHIVE": ["影巢"],
    "hdhaven": ["栖影"],
    "ZHIZHEN": ["至臻"],
    "XIAOMI": ["玉米", "小米网盘"],
    "TUDOU": ["土豆"],
    "HJZC": ["花卷"],
    "4KZN": ["4K指南"],
    "csp_DaPanSo": ["大盘搜", "DaPanSo"],
    "csp_EightEight": ["88网盘", "88Pan"],
    "csp_Libvio": ["LIBVIO", "libvio"],
    "csp_XiaoZhang": ["校长影视"],
    "csp_Meijumi": ["美剧迷"],
    "csp_Moli": ["HDmoli", "Moli"],
    "csp_PikaSo": ["皮卡搜"],
    "csp_Qianfan": ["千帆"],
    "csp_Qwmp4": ["七味"],
    "csp_Xb6v": ["新6V"],
    "csp_YunSo": ["小云搜索", "云搜"],
}

# 上游 pg 包发布镜像（用于同步 jar/js/lib 本体）
UPSTREAM_PKG_MIRRORS = [
    "http://wogg.rr.nu/pg/jsm.json",
    "http://wogg.myfw.us/pg/jsm.json",
    "http://www.fish2018.us.kg/p/jsm.json",
]

# 已知历史域名知识库：上游全部失效时作为兜底候选（新域名会被自动吸收，这里是种子）
SEED_DOMAINS = {
    "WOGG": ["http://wogg.888484.xyz/", "https://wogg.link/", "http://wogg.rr.nu/",
             "https://www.weixine.link/", "https://woggpan.888484.xyz/",
             "https://www.wogg.one/", "https://www.wogg.xyz/", "https://wogg.xyz/"],
    "MOGG": ["http://123.666291.xyz/", "http://mogg.top/", "https://666.666291.xyz/",
             "https://www.muou.site/"],
    "WOBG": ["https://wobge.run.goorm.io/", "https://tv.yydsys.top/"],
    "LABI": ["https://www.fmcf.fun/", "https://xiaocgege.shop/",
             "https://duopan.fun/"],
    "XIAOMI": ["https://mihdr.top/", "https://www.mucpan.cc/"],
    "TUDOU": ["https://tudou.lvdoui.top/"],
}

# 内容质量判定 —— 光看字节数不够：DNSPod「域名已过期」停放页约 25KB，
# 密码跳转页约 8KB，都能骗过纯体积阈值。改为「站点特征标记 + 反例标记」双判。
REAL_MIN_BYTES = 20000          # 兼容保留
SITE_FULL_BYTES = 100000        # 达到该体积且带站点特征 -> 满分（q3）
SITE_MIN_BYTES = 4000           # 带站点特征但体积偏小时的下限

# 命中任一即说明是「真站点的页面结构」（MacCMS/网盘聚合站通用特征）
SITE_MARKERS = (
    "/vod", "/type/", "/search", "api.php", "?ac=", "vodshow", "vodtype",
    "mac_url", "links.php", "/index.php/vod", "voddetail",
)

# 命中且页面没有站点特征 -> 判定为不可用（停放页/密码页/CF 挑战页）
# 【2026-10-15 修正】原来把 CF 挑战页（just a moment / attention required /
# challenge-platform / cf-chl）也放在这里，等于把「**站点活着、只是不让我们这种
# 数据中心 IP 进**」误判成死站 —— 实测 hdhaven.com、re0.me 都返回 CF 403，
# 而用户在家里的宽带上完全能用。这类页面改由 CHALLENGE_HINTS 单独处理。
# urldance 是域名停放/跳转服务，仍属真·不可用，保留。
BAD_PAGE_HINTS = (
    "域名已过期", "域名已到期", "域名过期", "域名出售", "域名转让", "dnspod",
    "请输入密码", "请输入访问密码", "访问密码", "本站已关闭", "站点已关闭",
    "已停止服务", "urldance",
)

# 人机校验 / WAF 拦截页 —— **不能**据此判死。
# 现象：服务器（数据中心 IP）被 Cloudflare / 站点 WAF 拦下，返回 403/503 或
# 「浏览器安全验证」页；而真实客户端在家庭宽带上通常能正常通过。
# 因此判为「存活但质量存疑」，既不替换原地址、也不触发失效告警。
CHALLENGE_HINTS = (
    "attention required", "just a moment", "challenge-platform", "cf-chl",
    "cf-error-details", "cloudflare", "checking your browser",
    "浏览器安全验证", "安全验证", "请稍候", "验证您是真人",
    "verify you are human", "verifying you are human", "enable javascript and cookies",
)

# ---------------------------------------------------------------- 巡检判定修正
# 【为什么要单独给「网盘聚合类站点」开后门 —— 2026-10-15】
# 用户实测反馈：软件里被标「⚠️失效」的接口，实际点进去是能用的；蜡笔接口则
# 反复失效又恢复、反复收到告警。
#
# 根因：SITE_MARKERS 那串特征（/vod /type/ api.php …）是 **MacCMS 影视站**的
# 页面结构。而下面这些接口是 **网盘聚合类**（对应 PG 包里的 csp_Wobg /
# csp_PingLian / csp_HDHaven / csp_Panta 等 Spider），它们的首页是前端 SPA、
# 用 JSON 调后端，页面里根本没有 /vod 之类字样。
# 于是 _classify 一律给 q1「无站点特征」，而 patch.build 只认 q>=2，
# 结果就是：域名明明活着，却被判成 offline 并打上「⚠️失效」。
#
# 这些 Spider 的首页只要「HTTP 200 + 体积正常 + 没有命中停放页/密码页/CF 挑战页」
# 就应该算可用 —— 判活靠的是它后端的 JSON 接口，不是首页 HTML 长什么样。
NETDISK_API_HINTS = (
    "wobg", "pinglian", "hdh", "hdhaven", "huamucang", "guanying",
    "panta", "yiso", "woniu", "qianfan", "mucpan", "tudou", "wogg",
    "catopen", "pikpak", "alist", "open",
)
# 网盘类站点首页的最小体积（SPA 首页通常几 KB 起）
SITE_APP_MIN_BYTES = 1500
# 前端空壳页的下限。实测「盘链」首页只有 730B（Vue 空壳，标题 PANLIAN），
# 真站存在但首页极简。低于此值仍按停放页处理。
SITE_APP_SHELL_MIN_BYTES = 500

# 探测重试次数。
# 【为什么必须重试 —— 2026-10-15】实测「蜡笔」的 fmcf.fun 一次探测抛
# ConnectionError 就被判死并打上 ⚠️失效，但随后手工 curl 直接 200 + 623KB
# 的真站页面。域名池判定最怕这种**瞬时失败**被当成永久失效 ——
# 它正是用户「标注失效的接口实测可以使用」现象的主要来源。
PROBE_ATTEMPTS = int(os.environ.get("OKTV_PROBE_ATTEMPTS", "3"))
# 重试间隔（秒），第 n 次失败后等待 n*该值
PROBE_RETRY_WAIT = float(os.environ.get("OKTV_PROBE_RETRY_WAIT", "1.0"))

# 连续多少次巡检都「一条可用地址都没有」才真正判定该 TAG 失效。
# 目的：抑制抖动 —— 站点偶尔超时/偶发 5xx 不该立刻改配置、更不该发告警。
OFFLINE_CONFIRM_ROUNDS = int(os.environ.get("OKTV_OFFLINE_CONFIRM", "2"))

# 是否把「⚠️失效」后缀直接写进站点 name。
#
# 【默认关闭 —— 2026-10-15 修正】
# 曾经默认开启，同 8 个站点名会被改成「观影|网盘|磁力⚠️失效」。问题有三：
#   1. 破坏「与原版逐字一致」这条红线 —— 用户一比对就发现站点名/分类跟原包不一样
#      （用户反馈的「直播源分类与 PG 原包有区别」就有这一份来源）。
#   2. 客户端会缓存配置。等站点恢复了，中枢这边已经去掉标注，端上那份还是旧的，
#      用户就会看到「标注失效但其实能用」「恢复了标注还在」——用户 3 号的投诉。
#   3. name 在客户端里同时承担**分组/显示**语义，塞后缀会连带影响观感。
# 失效状态改由三处表达，都不动配置本体：
#   * GET /api/sites            接口状态明细（站点视角）
#   * /live 监视台               可视化面板
#   * 私聊告警 / 恢复通知         只在真正确认失效的那一刻发一次
# 确实需要「直接在客户端里看到哪个失效」时置 OKTV_MARK_NAMES=1 临时打开。
MARK_NAMES = os.environ.get("OKTV_MARK_NAMES", "0") == "1"
