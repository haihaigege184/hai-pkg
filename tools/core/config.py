"""oktv-hub 全局配置与常量"""
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")
DIST_DIR = os.path.join(BASE_DIR, "dist")
LOG_DIR = os.path.join(BASE_DIR, "logs")
BASELINE_JSM = os.path.join(BASE_DIR, "baseline", "jsm.json")
BASELINE_LIB = os.path.join(BASE_DIR, "baseline", "lib")

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

# 服务参数
HOST = os.environ.get("OKTV_HOST", "0.0.0.0")
PORT = int(os.environ.get("OKTV_PORT", "8099"))
# 对外可访问地址（写入 jsm.json 的网关模式用；末尾不带 /）
PUBLIC_BASE = os.environ.get("OKTV_PUBLIC_BASE", "").rstrip("/")
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
BAD_PAGE_HINTS = (
    "域名已过期", "域名已到期", "域名过期", "域名出售", "域名转让", "dnspod",
    "请输入密码", "请输入访问密码", "访问密码", "本站已关闭", "站点已关闭",
    "已停止服务", "just a moment", "challenge-platform", "attention required",
    "cf-chl", "urldance",
)
