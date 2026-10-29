# 海哥影视（二开 APK）

在原版 Fongmi/TVBox 2.7.3 基础上重打包的二开版本，**与原版共存**，不替换、不卸载原版。

| 项 | 值 |
|---|---|
| 包名 | `com.hai.tv`（原版 `com.fongmi.android.tv`） |
| 应用名 | 海哥影视 |
| 签名 | 自签 keystore（与原版不同，故必须改包名共存） |
| 入口改造 | 点海报 → `WebDetailActivity`（自建，加载中枢 `/hai/poster/d/<id>`） |

## 为什么必须改包名

原版签名无法复用，改包重签后签名与原版不一致；同包名安装会
`INSTALL_FAILED_UPDATE_INCOMPATIBLE`，卸载原版又等于毁掉用户已登录的网盘态。
所以 `applicationId` 改成 `com.hai.tv`，两个包各自独立数据目录，互不干扰。

改包名时**必须同步**：

1. `package="com.hai.tv"`；
2. 5 处 `authorities`（`androidx-startup` / `autosize-init-provider` /
   `customactivityoncrashinitprovider` / `provider` / `com.fongmi.quickjs.provider`）；
3. `DYNAMIC_RECEIVER_NOT_EXPORTED_PERMISSION` 也要改，否则
   `INSTALL_FAILED_DUPLICATE_PERMISSION`；
4. R 类**不用搬**，`com.fongmi.android.tv.R$*` 原地可用（R 只是常量容器）；
5. `SharedPreferences` 默认文件名跟包名走 —— 配置迁移时要
   `cp com.fongmi.android.tv_preferences.xml com.hai.tv_preferences.xml`，
   否则首页退回「简洁风格」、看不到点播入口。

## 打包

```bash
python android/build_hai_tv.py --apk <原版.apk> --work F:/tvmod
```

- 反编译 ≈1.5 分钟；改过 manifest 后全量重打包 5.5~14 分钟（98MB）。
- **工作目录必须是纯英文路径**，中文路径下 aapt2 报
  `failed to open directory: 系统找不到指定的文件`。
- 流程：`apktool d` → 注入 smali → 改 manifest/应用名 → `apktool b` →
  `zipalign -p -f 4` → `apksigner sign`。

## 详情页注入

原版 2.7.3 的 dex 里**没有** `DetailActivity`（`_work/apk_extract` 是另一份构建），
点海报只有一条路：`VideoActivity.OoOo0oOoO0o0oO0o(Activity,String,String,String,String)V`。

改造方式：把该方法体整段替换成跳 `WebDetailActivity`，其余 6 处调用点
（片单/海报墙/搜索/收藏/VodActivity/Collect）自动生效。

`WebDetailActivity` 是纯手工 smali，不依赖任何资源 id：建 WebView →
`requestFocus()`（不加这句遥控器按键进不到渲染进程）→ 加载中枢详情页；
`haiplay://play` 由 `WebDetailActivity$1.shouldOverrideUrlLoading` 拦下并回落播放器。

## 已知问题：网盘中继端口 10078 / 10079

PG jar 生成的网盘播放地址写死 `http://127.0.0.1:10078/p/...`，而客户端实际监听的
中继在 **10079**（`ss -ltnp` 实测：`com.hai.tv` 只有 9978 与 10079）。
于是播放器连 10078 一律 `ECONNREFUSED` —— 这就是「能进播放器但播放失败」的根因。

临时解法（已写进电视 `/system/bin/install-recovery.sh` 开机脚本）：

```sh
iptables -t nat -A OUTPUT -p tcp --dport 10078 -j REDIRECT --to-ports 10079
```

彻底解法要么给设备补上 PG 的 Go 代理（10078），要么改 `pg.jar` 里的端口常量。
