/**
 * hai-package —— 「hai 本地包」维护插件（SEA 机器人）
 *
 * 命令（容错解析：允许 @机器人 前缀、/ # ! 前缀、全角空格、结尾语气词）：
 *   修复接口   立即检测修复本地包接口（后台执行，分阶段回报进度）
 *   接口状态   汇总：接口数量 / 域名池 / 局域网与外网接口地址 / 失效清单
 *   接口明细   逐条列出全部接口（状态、域名、延迟），按页发送
 *   hai帮助    命令说明
 *
 * 设计原则（用户明确要求）：
 *   1. 任何命令**收到即回执** —— 绝不出现「发了命令毫无反应」；
 *   2. 长任务**分阶段回报** —— 每完成一步推一条进度，能判断在跑还是卡了；
 *   3. 任何异常**必须可见** —— 所有路径都有兜底，失败也要说出来；
 *   4. 回复走原会话，私聊只作降级 —— 避免 admin 未配置时结果无处可去。
 *
 * 手工验证：
 *   node -e "const P=require('./index.js');console.log(typeof P)"
 *   curl -s -XPOST http://127.0.0.1:8099/oktv/api/repair -H 'Content-Type: application/json' \
 *        -d '{"background":true,"reason":"manual"}'
 *   curl -s http://127.0.0.1:8099/oktv/api/repair/status
 */

'use strict';

const BasePlugin = require('../base-plugin');
const http = require('node:http');

// 用「包含」语义匹配，这样「接口状态怎么样」「帮我修复接口」也能命中
const HELP_RE = /(hai帮助|接口帮助|hai菜单)/i;
const SITES_RE = /(接口明细|接口列表|全部接口|接口详情|hai接口列表|查接口|搜索接口)/i;
const STATUS_RE = /(接口状态|接口情况|hai状态|本地包状态|hai包状态)/i;
const REPAIR_RE = /(修复接口|检测修复|hai修复|hai接口修复)/i;

// QQ 单条消息别太长，明细按每页 N 条拆分发送
const PAGE_SIZE = 28;
// 修复进度轮询
const POLL_MS = parseInt(process.env.HAI_POLL_MS || '2500', 10);
const POLL_MAX = 240;          // 2.5s * 240 = 10 分钟上限
const COOLDOWN_S = parseInt(process.env.HAI_COOLDOWN_S || '60', 10);   // 防连点

const STAGE_TEXT = {
    starting: '准备中',
    sync: '①/③ 上游同步',
    check: '②/③ 全池巡检',
    render: '③/③ 重新生成配置',
    done: '完成',
    error: '失败',
};

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
        const raw = (context.msg || '').trim();
        if (!raw) return false;

        // 容错解析：剥掉 @机器人 / 命令前缀 / 全角空格 / 结尾标点
        const msg = this._normalize(raw);
        if (!msg) return false;

        const cmd = this._route(msg);
        if (!cmd) return false;

        // 权限不足也必须有回执 —— 沉默是用户最无法接受的失败方式
        if (!this.hasPermission(context, 2)) {
            await this._say(context, `🔒「${msg}」是 hai 本地包维护命令，仅超级管理员可用。`);
            return false;
        }

        try {
            await this[`_cmd_${cmd}`](context);
        } catch (e) {
            await this._say(context, `❌ 命令「${msg}」执行出错：${(e && e.message) || e}`);
        }
        return false;
    }

    /** 剥离 @提及、命令前缀、多余空白与结尾标点 */
    _normalize(s) {
        let t = String(s || '');
        t = t.replace(/[＠@][^\s＠@]{1,32}/g, ' ');     // @某人 / @机器人（含全角＠）
        t = t.replace(/[\u3000\s]+/g, ' ').trim();      // 全角空格与连续空白归一
        t = t.replace(/^[/\\#!！。，,、:：]+/, '').trim(); // 开头的命令前缀符
        t = t.replace(/[。，,、！!？?~～]+$/, '').trim(); // 结尾语气词
        return t;
    }

    /** 取命令词后面的关键词：「接口明细 玩偶哥哥」-> 「玩偶哥哥」 */
    _kwOf(msg, re) {
        let t = String(msg || '');
        if (!re.test(t)) return '';
        t = t.replace(re, ' ');                              // 非全局，只去掉第一处命令词
        t = t.replace(/^[\s:：\-—_,，、/|]+/, '').trim();       // 剥前导分隔符
        t = t.replace(/[。，,、！!？?~～]+$/, '').trim();       // 剥结尾语气词
        return t;
    }

    /** 关键词是否命中该接口（名字 / key / TAG / api / 当前域名都能匹配） */
    _matchSite(x, k) {
        return [x.name, x.key, x.tag, x.api, x.best, x.proxy]
            .some((v) => String(v || '').toLowerCase().includes(k));
    }

    _route(msg) {
        if (HELP_RE.test(msg)) return 'help';
        if (SITES_RE.test(msg)) return 'sites';
        if (STATUS_RE.test(msg)) return 'status';
        if (REPAIR_RE.test(msg)) return 'repair';
        return null;
    }

    // ------------------------------ 命令实现 ------------------------------

    _helpText() {
        return [
            '🧰 hai 本地包 · 维护命令',
            '───────────────',
            '接口状态  —— 汇总：接口数量 + 域名池 + 局域网/外网接口地址',
            '接口明细  —— 逐条列出全部接口（中文名、状态、域名、延迟）',
            '接口明细 玩偶 —— 只列匹配关键词的接口（名字/key/TAG/域名都能匹配）',
            '修复接口  —— 检测并修复失效接口（约 1~3 分钟，分步回报进度）',
            'hai帮助   —— 显示本说明',
            '───────────────',
            '说明：中枢每天 08:00 自动巡检一次；域名池抖动时有滞后判定，',
            '     连续两轮都取不到可用地址才会判失效并告警，不会来回刷通知。',
            '     失效状态只在「接口状态/明细」里体现，不改写站点名（与原版一致）。',
            '     命令支持带 @ 前缀，例如「@机器人 接口状态」。',
        ].join('\n');
    }

    async _cmd_help(context) {
        await this._say(context, this._helpText());
    }

    /** 汇总视图：接口数量 + 双地址 + 失效清单 */
    async _cmd_status(context) {
        await this._say(context, '⏳ 收到「接口状态」，正在读取中枢…');
        let j;
        try {
            j = await this._getJSON('/api/sites');
        } catch (e) {
            await this._say(context, `❌ 读取状态失败：${e.message}\n中枢地址：${this.base}\n`
                + '排查：systemctl status oktv-hub');
            return;
        }

        const ep = j.endpoints || {};
        const pool = j.pool || {};
        const bad = (j.items || []).filter((x) => x.status === 'offline');
        const watch = (j.items || []).filter((x) => x.status === 'watch');
        const guard = (j.items || []).filter((x) => x.status === 'guard');
        // 配置里标着失效、但实际有可用地址 —— 用户反馈过的「恢复了没去掉标注」
        const stale = (j.items || []).filter((x) => x.stale_mark);

        const lines = [
            '📊 hai 本地包 · 接口状态',
            '───────────────',
            `接口 ${j.total} 个 ｜ 正常 ${j.ok} ｜ 失效 ${j.offline}`
                + ` ｜ 观察 ${j.watch} ｜ 存疑 ${j.guard || 0}`,
            `域名池 ${pool.domains_alive}/${pool.domains_total} 存活`
                + `（可用 ${pool.domains_usable}）`,
            `上次巡检：${this._ts(this._lastCheck(j))}`,
            '───────────────',
            '🌐 接口地址（填进 OK影视 的「配置地址」）',
        ];
        if (ep.recommend) lines.push(` 推荐 ${ep.recommend}`);
        if (ep.lan) lines.push(` 局域网 ${ep.lan}`);
        if (ep.public) lines.push(` 外网 ${ep.public}`);
        lines.push('───────────────');

        if (bad.length) {
            lines.push(`⚠️ 失效 ${bad.length} 个（连续 ${j.confirm_rounds} 轮无可用）`);
            for (const x of bad.slice(0, 12)) {
                lines.push(` · ${x.name || x.key}（${x.api}）域名 ${x.usable}/${x.total}`);
            }
            if (bad.length > 12) lines.push(` …另有 ${bad.length - 12} 个`);
        } else {
            lines.push('✅ 没有已确认失效的接口');
        }
        if (watch.length) {
            lines.push(`👀 观察中 ${watch.length} 个（本轮无可用，未达判定阈值）`);
        }
        if (guard.length) {
            lines.push(`🛡 存疑 ${guard.length} 个（有响应但被 WAF/人机校验拦，客户端通常可正常）`);
            for (const x of guard.slice(0, 8)) lines.push(` · ${x.name || x.key}｜${x.api}`);
        }
        if (stale.length) {
            lines.push(`🔖 标注与实际不符 ${stale.length} 个（配置里标着失效、实测可用）`);
        }
        lines.push('───────────────');
        lines.push('发「接口明细」逐条查看全部接口；找单个接口用「接口明细 玩偶」这样带关键词。');
        await this._say(context, lines.join('\n'));
    }

    /** 明细视图：逐条列出全部接口，按每页 PAGE_SIZE 拆分发送；可带关键词筛选 */
    async _cmd_sites(context) {
        const kw = this._kwOf(this._normalize(String(context.msg || '')), SITES_RE);
        await this._say(context, kw
            ? `⏳ 收到「接口明细 ${kw}」，正在检索…`
            : '⏳ 收到「接口明细」，正在读取中枢…');

        let j;
        try {
            j = await this._getJSON('/api/sites');
        } catch (e) {
            await this._say(context, `❌ 读取接口明细失败：${e.message}\n中枢地址：${this.base}`);
            return;
        }

        const all = j.items || [];
        if (!all.length) {
            await this._say(context, 'ℹ️ 中枢还没有接口数据，先发一次「修复接口」建立域名池。');
            return;
        }

        let items = all;
        if (kw) {
            const k = kw.toLowerCase();
            items = all.filter((x) => this._matchSite(x, k));
            if (!items.length) {
                await this._say(context, `🔍 没找到匹配「${kw}」的接口（共 ${all.length} 个）。\n`
                    + '可试：玩偶 / 木偶 / 夸克 / 百度 / 网盘 / 磁力 / 阿里\n'
                    + '或发「接口明细」看全部。');
                return;
            }
        }

        const pages = Math.ceil(items.length / PAGE_SIZE);
        const head = [
            `📋 hai 本地包 · 接口明细${kw ? `（筛选「${kw}」）` : ''}`
                + `（${items.length} 条，共 ${pages} 页）`,
            kw
                ? `匹配 ${items.length} 条（全部 ${all.length} 条）`
                : `正常 ${j.ok} ｜ 失效 ${j.offline} ｜ 观察 ${j.watch}`
                    + ` ｜ 存疑 ${j.guard || 0} ｜ 本地 ${j.local_sites} ｜ 未建立 ${j.unknown}`,
            '───────────────',
        ].join('\n');

        for (let p = 0; p < pages; p++) {
            const chunk = items.slice(p * PAGE_SIZE, (p + 1) * PAGE_SIZE);
            const body = chunk.map((x) => this._fmtSite(x)).join('\n');
            const text = (p === 0 ? `${head}\n` : `（第 ${p + 1}/${pages} 页）\n`) + body;
            await this._say(context, text);
        }
    }

    /**
     * 单条接口的展示。
     *
     * 中文名优先 —— 用户是按「玩偶哥哥」「木偶哥哥」这类名字找接口的，
     * 只给英文 key（Mogg / Wogg）会让人以为列表里没有这些接口。
     */
    _fmtSite(x) {
        const icon = {
            ok: '✅', offline: '⚠️', watch: '👀', guard: '🛡',
            unknown: '❓', local: '📦',
        }[x.status] || '·';

        const name = String(x.name || '').trim();
        const key = String(x.key || '').trim();
        let label = name || key;
        if (name && key && name !== key) label += `（${key}）`;

        let extra;
        if (x.status === 'ok') {
            extra = `${this._hostOf(x.best)} ${x.latency}ms`;
        } else if (x.status === 'local') {
            extra = '本地';
        } else if (x.status === 'unknown') {
            extra = '域名池未建立';
        } else if (x.status === 'guard') {
            extra = 'WAF 拦（客户端通常可正常）';
        } else {
            extra = `域名 ${x.usable}/${x.total}`;
            if (x.streak) extra += `（已 ${x.streak} 轮无可用）`;
        }
        return `${icon} ${label} · ${extra}${x.stale_mark ? ' · 🔖标注过期' : ''}`;
    }

    // ------------------------------ 修复（后台 + 分阶段进度）------------------------------

    async _cmd_repair(context) {
        if (this._busy) {
            await this._say(context, '⏳ 已有一轮修复正在进行，完成后会回报结果。');
            return;
        }
        const since = Math.round((Date.now() - this._lastAt) / 1000);
        if (this._lastAt && since < COOLDOWN_S) {
            await this._say(context, `⏳ 距上次修复仅 ${since} 秒（防连点 ${COOLDOWN_S}s），请稍后再试。`);
            return;
        }

        this._busy = true;
        this._lastAt = Date.now();

        // 第一时间回执 —— 让用户立刻知道命令已被接收
        await this._say(context, [
            '🔧 收到「修复接口」，开始执行',
            '① 上游同步 → ② 全池巡检 → ③ 重新生成配置',
            '每完成一步都会回报，预计 1~3 分钟。',
        ].join('\n'));

        let follow = false;
        try {
            const r = await this._postJSON('/api/repair',
                { reason: 'sea-plugin', background: true });
            if (r && (r.accepted || r.ok)) {
                follow = true;
            } else if (r && r.busy) {
                await this._say(context, `⚠️ 中枢已有一轮修复在跑`
                    + `（当前：${STAGE_TEXT[r.stage] || r.stage || '未知'}），改为跟随该任务。`);
                follow = true;
            }
        } catch (e) {
            await this._say(context, `❌ 无法启动修复：${e.message}\n中枢地址：${this.base}`);
            this._busy = false;
            return;
        }

        if (!follow) {
            await this._say(context, '❌ 中枢未接受修复请求（返回内容异常）。');
            this._busy = false;
            return;
        }

        // 后台跟踪进度，不阻塞消息分发链
        this._runRepairWatch(context).catch(async (e) => {
            this._busy = false;
            await this._say(context, `❌ 进度跟踪异常：${(e && e.message) || e}`);
        });
    }

    /**
     * 轮询中枢进度，分阶段回报。
     *
     * 关键：判据不是「当前 stage 字符串」，而是「某一步的成果数据是否已经出现」
     * （sync / check 在各自完成时写入并一直保留到任务结束）。这样即使轮询间隔
     * 正好跨过了某个阶段，该阶段的完成信息也不会丢 —— 下一次轮询会把它补报出来。
     */
    async _runRepairWatch(context) {
        const t0 = Date.now();
        const reported = new Set();     // 已回报完成的步骤
        let ticking = '';               // 已提示过「进行中」的阶段

        try {
            for (let i = 0; i < POLL_MAX; i++) {
                await this._sleep(POLL_MS);

                let s;
                try {
                    s = await this._getJSON('/api/repair/status');
                } catch (e) {
                    continue;               // 单次查询失败不打断整体跟踪
                }

                // 1) 收集「已完成但还没报过」的步骤（含被轮询跨过的阶段）
                const doneLines = this._collectDone(s, reported);

                // 2) 结束判定：先把最后一步的完成回报补上，再发汇总
                if (s.stage === 'done') {
                    if (doneLines.length) await this._say(context, doneLines.join('\n'));
                    await this._say(context, this._doneText(s, t0));
                    return;
                }
                if (s.stage === 'error') {
                    if (doneLines.length) await this._say(context, doneLines.join('\n'));
                    await this._say(context, [
                        '❌ hai 本地包 · 修复失败',
                        '───────────────',
                        `原因：${s.error || '未知错误'}`,
                        `中枢：${this.base}`,
                        '排查：systemctl status oktv-hub',
                    ].join('\n'));
                    return;
                }

                // 3) 「上一步完成」与「当前步进行中」合并成一条，避免刷屏
                const cur = s.stage;
                const showTick = cur && cur !== ticking
                    && cur !== 'starting' && cur !== 'idle';
                if (showTick) ticking = cur;
                const lines = doneLines.slice();
                if (showTick) {
                    lines.push(`⏳ ${STAGE_TEXT[cur] || cur} 中 …`
                        + `（已用时 ${this._elapsed(s.started)}）`);
                }
                if (lines.length) await this._say(context, lines.join('\n'));
            }
            await this._say(context,
                `⚠️ 修复超过 ${Math.round((POLL_MAX * POLL_MS) / 60000)} 分钟仍未结束。\n`
                + '请发「接口状态」查看当前进展，或用 systemctl status oktv-hub 排查。');
        } finally {
            this._busy = false;
        }
    }

    /** 返回「已经出成果、但还没报过」的步骤文案（含被轮询跨过的阶段）*/
    _collectDone(s, reported) {
        const lines = [];
        if (s.sync && !reported.has('sync')) {
            reported.add('sync');
            lines.push(`✅ ${STAGE_TEXT.sync} 完成${this._stageDetail('sync', s)}`);
        }
        if (s.check && !reported.has('check')) {
            reported.add('check');
            lines.push(`✅ ${STAGE_TEXT.check} 完成${this._stageDetail('check', s)}`);
        }
        if (s.stage === 'done' && !reported.has('render')) {
            reported.add('render');
            lines.push(`✅ ${STAGE_TEXT.render} 完成`);
        }
        return lines;
    }

    _stageDetail(stage, s) {
        if (stage === 'sync' && s.sync) {
            const added = (s.sync.new_domains || 0) + (s.sync.harvested || 0);
            return ` —— ${s.sync.sources || 0} 个源，新增候选域名 ${added} 个`;
        }
        if (stage === 'check' && s.check) {
            return ` —— 检查 ${s.check.checked || 0} 个地址，存活 ${s.check.alive || 0} 个`;
        }
        return '';
    }

    _doneText(s, t0) {
        const r = (s && s.result) || {};
        const lines = [
            '🎉 hai 本地包 · 修复完成',
            '───────────────',
            `耗时：${r.elapsed || Math.round((Date.now() - t0) / 1000)}s`,
            `巡检：${r.checked || 0} 个地址，存活 ${r.alive || 0} 个`,
            `上游：${(r.sync && r.sync.sources) || 0} 个源，新增候选域名 `
                + `${((r.sync && r.sync.new_domains) || 0) + ((r.sync && r.sync.harvested) || 0)} 个`,
            `本次修复：${(r.fixed || []).length} 个`,
        ];
        if ((r.fixed || []).length) {
            lines.push('已恢复：' + r.fixed.slice(0, 10).join('、'));
        }
        if ((r.still_offline || []).length) {
            lines.push(`仍失效：${r.still_offline.length} 个（`
                + `${r.still_offline.slice(0, 8).join('、')}）`);
        } else {
            lines.push('结论：全部接口可用 ✅');
        }
        lines.push('───────────────');
        lines.push('配置已更新，电视机重新进入「点播接口」即可生效。');
        return lines.join('\n');
    }

    // ------------------------------ 工具方法 ------------------------------

    /** 统一回复出口：优先原会话，失败降级私聊，绝不抛给调用方 */
    async _say(context, text) {
        try {
            if (context && typeof context.reply === 'function') {
                await context.reply(text);
                return true;
            }
        } catch (e) {
            console.warn(`[hai-package] reply 失败，降级私聊：${e.message}`);
        }
        return await this._sendPrivate(text);
    }

    _sleep(ms) {
        return new Promise((resolve) => { setTimeout(resolve, ms); });
    }

    _elapsed(started) {
        if (!started) return '0s';
        const d = Math.max(0, Math.floor(Date.now() / 1000) - Number(started));
        return d >= 60 ? `${Math.floor(d / 60)}m${d % 60}s` : `${d}s`;
    }

    _ts(sec) {
        if (!sec) return '—';
        const d = new Date(sec * 1000);
        const p = (n) => String(n).padStart(2, '0');
        return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} `
            + `${p(d.getHours())}:${p(d.getMinutes())}`;
    }

    _hostOf(url) {
        if (!url) return '-';
        return String(url).replace(/^https?:\/\//, '').replace(/\/+$/, '').slice(0, 32);
    }

    /** 巡检时间取站点 checked 里最新的一个 */
    _lastCheck(j) {
        let mx = 0;
        for (const x of (j.items || [])) {
            if (x.checked && x.checked > mx) mx = x.checked;
        }
        return mx;
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
                    let parsed = null;
                    try { parsed = JSON.parse(data); } catch (e) { /* 非 JSON */ }
                    if (res.statusCode !== 200) {
                        // 业务冲突（如 409 已有任务在跑）带 JSON 时交给调用方判断
                        if (parsed && typeof parsed === 'object') {
                            return resolve(Object.assign({ __http: res.statusCode }, parsed));
                        }
                        return reject(new Error(`HTTP ${res.statusCode}: ${data.slice(0, 120)}`));
                    }
                    if (!parsed) {
                        return reject(new Error('响应非 JSON: ' + data.slice(0, 120)));
                    }
                    resolve(parsed);
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

    /** 主动私聊管理员（不依赖 context.reply，用于异步回报降级） */
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
