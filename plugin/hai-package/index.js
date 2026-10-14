/**
 * SEA2 插件：hai-package —— 「hai 本地包」接口维护
 *
 * 职责边界（单一）：
 *   只做一件事——把「hai 本地包」（11 服务器 /root/hai，由 oktv-hub 中枢维护）
 *   的接口检测与修复能力，暴露成 QQ 命令。
 *   巡检本身、域名池、通知逻辑都在中枢侧（oktv-hub），本插件只当「遥控器」，
 *   不重复实现任何抓取/探测逻辑，避免两套逻辑打架。
 *
 * QQ 命令（私聊，仅 superAdmin 有效）：
 *   「修复接口」「修复」「检测修复」「hai修复」
 *       → 立即回执「已触发」，随后异步调用中枢 /api/repair
 *          （上游同步 → 全池巡检 → 重新生成并落盘 /root/hai/jsm.json）
 *       → 完成后把「恢复了几条 / 仍失效几条」私聊回报。
 *       设计成「先回执再异步回报」：完整修复约需 60~150 秒，
 *       同步阻塞会卡住消息分发链，异步闭环体验更好，也便于中途掉线。
 *   「接口状态」「hai状态」「本地包状态」
 *       → 拉中枢 /api/status 回报：接口源数、域名存活数、上次巡检/每日巡检时间、配置地址。
 *   「hai帮助」
 *       → 命令说明。
 *
 * 与中枢的契约（HTTP，默认 http://127.0.0.1:8099/oktv）：
 *   POST /api/repair  → {ok, alive, checked, fixed:[TAG], still_offline:[TAG],
 *                        sync:{sources,new_domains,harvested}, elapsed, pkg}
 *   GET  /api/status  → {state:{last_health,last_daily}, pool:{domains_alive,domains_total},
 *                        tags, sites, pkg_dir, notify_enabled, public_base}
 *
 * 约定（与框架一致）：
 *   - 入口 plugins/hai-package/index.js，导出 class HaiPackagePlugin extends BasePlugin
 *   - core/plugin-manager.js 自动扫描 plugins/ 子目录并 new HaiPackagePlugin(name, config)
 *   - onEnable(db)/onDisable(db) 为生命周期钩子
 *   - onMessage 恒返回 false：本插件只识别命令，不阻断其它插件收消息
 *
 * 环境变量：
 *   HAI_HUB_BASE   (默认 http://127.0.0.1:8099/oktv) 中枢地址
 *   HAI_TIMEOUT_MS (默认 240000) 修复超时
 *   HAI_ADMIN      (可选) 管理员 QQ，未设则取 config.superAdmin
 *
 * 自检：
 *   curl -s -XPOST http://127.0.0.1:8099/oktv/api/repair | head -c 200
 */

'use strict';

const BasePlugin = require('../base-plugin');
const http = require('node:http');

const REPAIR_RE = /^(修复接口|修复|检测修复|hai修复|hai接口修复)$/i;
const STATUS_RE = /^(接口状态|hai状态|本地包状态|hai包状态|接口情况)$/i;
const HELP_RE = /^(hai帮助|接口帮助|hai菜单)$/i;

class HaiPackagePlugin extends BasePlugin {
    constructor(name, config) {
        super(name, config);

        // 只识别命令、不拦截消息：后续插件照常收到同一条消息
        this.priority = 50;
        this.permissionLevel = 2;          // 2 = SuperAdmin：仅超级管理员可用

        this.cfg = config || {};
        this.admin = String(process.env.HAI_ADMIN || this.cfg.superAdmin || '');

        this.base = (process.env.HAI_HUB_BASE || 'http://127.0.0.1:8099/oktv').replace(/\/+$/, '');
        this.timeout = parseInt(process.env.HAI_TIMEOUT_MS || '240000', 10);

        this.napcatHost = this.cfg.napcat_host || '127.0.0.1';
        this.napcatPort = this.cfg.napcat_port || 4000;
        this.napcatToken = this.cfg.napcat_token || '';

        this._busy = false;                // 同一时刻只允许一次修复，防连点
        this._lastAt = 0;
    }

    // ------------------------------ 生命周期 ------------------------------

    async onEnable(db) {
        console.log(`[hai-package] 已加载：hub=${this.base} admin=${this.admin || '(未配置)'}`);
        // 中枢连通性预检，失败只告警不阻塞加载
        try {
            const s = await this._getJSON('/api/status');
            console.log(`[hai-package] 中枢在线：TAG ${s.tags} 个，`
                + `域名存活 ${s.pool && s.pool.domains_alive}/${s.pool && s.pool.domains_total}`);
        } catch (e) {
            console.warn(`[hai-package] 中枢暂时不可达（运行时再试）：${e.message}`);
        }
    }

    async onDisable(db) {
        console.log('[hai-package] 已卸载');
    }

    // ------------------------------ 命令入口 ------------------------------

    async onMessage(context) {
        const msg = (context.msg || '').trim();
        if (!msg) return false;

        if (!this.hasPermission(context, 2)) {
            if (REPAIR_RE.test(msg) || STATUS_RE.test(msg)) {
                await context.reply('🔒 「hai 本地包」维护命令仅超级管理员可用。');
            }
            return false;
        }

        if (HELP_RE.test(msg)) {
            await context.reply(this._helpText());
            return false;
        }

        if (STATUS_RE.test(msg)) {
            await this._handleStatus(context);
            return false;
        }

        if (REPAIR_RE.test(msg)) {
            await this._handleRepair(context);
            return false;
        }

        return false;
    }

    // ------------------------------ 命令实现 ------------------------------

    _helpText() {
        return [
            '🧰 hai 本地包 · 维护命令',
            '───────────────',
            '修复接口  —— 立即检测并修复本地包接口（约 1~3 分钟）',
            '接口状态  —— 查看接口源 / 域名存活 / 上次巡检时间',
            'hai帮助   —— 显示本说明',
            '───────────────',
            '说明：中枢每天 08:00 自动巡检一次，发现失效会自动找新域名并修复，',
            '     无需手动干预；上面命令只是「想立刻修」时用。',
        ].join('\n');
    }

    async _handleStatus(context) {
        try {
            const s = await this._getJSON('/api/status');
            const st = s.state || {};
            const pool = s.pool || {};
            const lines = [
                '📊 hai 本地包 · 接口状态',
                '───────────────',
                `接口源：${s.tags} 个  |  配置站点：${s.sites} 个`,
                `域名池：${pool.domains_alive}/${pool.domains_total} 存活`,
                `上次巡检：${this._ts(st.last_health)}`,
                `每日巡检：${this._ts(st.last_daily)}`,
                `通知通道：${s.notify_enabled ? '正常' : '未启用'}`,
                `包目录：${s.pkg_dir}`,
                '───────────────',
                `配置地址：${(s.public_base || '')}/hai/jsm.json`,
            ];
            await context.reply(lines.join('\n'));
        } catch (e) {
            await context.reply(`❌ 读取状态失败：${e.message}\n中枢是否在跑？${this.base}`);
        }
    }

    async _handleRepair(context) {
        if (this._busy) {
            await context.reply('⏳ 已有一轮修复正在进行，请稍候…');
            return;
        }
        if (Date.now() - this._lastAt < 60000) {
            await context.reply('⏳ 距上次修复不到 1 分钟，稍后再试。');
            return;
        }
        this._busy = true;
        this._lastAt = Date.now();

        await context.reply([
            '🔧 已触发「接口检测修复」',
            '正在：上游同步 → 全池巡检 → 重新生成 hai 本地包',
            '预计 1~3 分钟，完成后会私聊回报结果。',
        ].join('\n'));

        // 异步执行，立刻释放消息分发链
        this._runRepair().catch(() => { /* 内部已兜底通知 */ });
    }

    async _runRepair() {
        const started = Date.now();
        try {
            const r = await this._postJSON('/api/repair', { reason: 'sea-plugin' });
            const lines = [
                '✅ hai 本地包 · 修复完成',
                '───────────────',
                `耗时：${r.elapsed || Math.round((Date.now() - started) / 1000)}s`,
                `巡检：${r.checked} 个地址，存活 ${r.alive} 个`,
                `上游：${(r.sync && r.sync.sources) || 0} 个源，新增候选域名 `
                    + `${((r.sync && r.sync.new_domains) || 0) + ((r.sync && r.sync.harvested) || 0)} 个`,
                `本次修复：${(r.fixed || []).length} 个`,
            ];
            if ((r.fixed || []).length) lines.push('已恢复：' + r.fixed.slice(0, 10).join('、'));
            if ((r.still_offline || []).length) {
                lines.push(`仍失效：${r.still_offline.length} 个（${r.still_offline.slice(0, 8).join('、')}）`);
            } else {
                lines.push('结论：全部接口可用 ✅');
            }
            lines.push('───────────────');
            lines.push('hai 本地包已更新，电视机重新进入「点播接口」即可生效。');
            await this._sendPrivate(lines.join('\n'));
        } catch (e) {
            await this._sendPrivate([
                '❌ hai 本地包 · 修复失败',
                '───────────────',
                `原因：${e.message}`,
                `中枢：${this.base}`,
                '排查：systemctl status oktv-hub',
            ].join('\n'));
        } finally {
            this._busy = false;
        }
    }

    // ------------------------------ 工具方法 ------------------------------

    _ts(sec) {
        if (!sec) return '—';
        const d = new Date(sec * 1000);
        const p = (n) => String(n).padStart(2, '0');
        return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} `
            + `${p(d.getHours())}:${p(d.getMinutes())}`;
    }

    _request(method, path, body) {
        return new Promise((resolve, reject) => {
            let u;
            try { u = new URL(this.base + path); } catch (e) { return reject(e); }
            const payload = body ? Buffer.from(JSON.stringify(body), 'utf8') : null;
            const headers = { 'Content-Type': 'application/json' };
            if (payload) headers['Content-Length'] = payload.length;

            const req = http.request({
                hostname: u.hostname, port: u.port || 80,
                path: u.pathname + u.search,
                method, headers, timeout: this.timeout,
            }, (res) => {
                let data = '';
                res.on('data', (c) => { data += c; });
                res.on('end', () => {
                    if (res.statusCode !== 200) {
                        return reject(new Error(`HTTP ${res.statusCode}: ${data.slice(0, 120)}`));
                    }
                    try { resolve(JSON.parse(data)); }
                    catch (e) { reject(new Error('响应非 JSON: ' + data.slice(0, 120))); }
                });
            });
            req.on('error', (e) => reject(new Error(e.message)));
            req.on('timeout', () => { req.destroy(); reject(new Error('中枢响应超时')); });
            if (payload) req.write(payload);
            req.end();
        });
    }

    _getJSON(path) { return this._request('GET', path, null); }
    _postJSON(path, body) { return this._request('POST', path, body || {}); }

    /** 主动私聊管理员（不依赖 context.reply，用于异步回报） */
    _sendPrivate(text) {
        return new Promise((resolve) => {
            if (!this.admin) return resolve(false);
            const payload = Buffer.from(JSON.stringify({
                user_id: parseInt(this.admin, 10), message: text,
            }), 'utf8');
            const headers = {
                'Content-Type': 'application/json',
                'Content-Length': payload.length,
            };
            if (this.napcatToken) headers['Authorization'] = `Bearer ${this.napcatToken}`;
            const req = http.request({
                hostname: this.napcatHost, port: this.napcatPort,
                path: '/send_private_msg', method: 'POST',
                headers, timeout: 15000,
            }, (res) => { res.resume(); res.on('end', () => resolve(true)); });
            req.on('error', () => resolve(false));
            req.on('timeout', () => { req.destroy(); resolve(false); });
            req.write(payload);
            req.end();
        });
    }
}

module.exports = HaiPackagePlugin;
