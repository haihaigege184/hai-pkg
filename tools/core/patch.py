"""配置生成：把基线 jsm.json 中失效的站点地址替换成「当前可用」的地址

两种模式
  direct  —— 直接把 ext 里的 baseURL 写成当前最优存活域名。
             优点：链路最短、最贴近原生体验。缺点：APP 若缓存了配置，域名再漂移就会失效。
  gateway —— 把 baseURL 写成中枢的网关地址 http://<hub>/gw/<TAG>/，
             由中枢实时反代到当前最优域名。
             优点：配置一次写死，之后域名怎么漂移都不用改 APP。缺点：多一跳。

默认 dual（推荐）：direct 为主，网关作为同名备份站点附带输出，二者都写进配置。
"""
import json
import os

from . import config as C
from . import health
from . import order as O
from . import renderbase as RENDER
from . import sources as S
from . import store

_OFFLINE_TAG = "OFFLINE"


def load_baseline():
    with open(C.BASELINE_JSM, "r", encoding="utf-8-sig") as f:
        return json.load(f)


def gateway_base(tag):
    # 走 renderbase 而不是直接读 C.PUBLIC_BASE：只有这一处是「渲染口径」的
    # 唯一权威来源。默认（OKTV_RENDER_PICK=0 且未手动 pin）返回的仍是
    # PUBLIC_BASE 第一项，行为与引入该模块前逐字一致。
    pub = RENDER.current()
    if not pub:
        return None
    return f"{pub}/gw/{tag}/"


def _base_alive(ent, base, minq=2):
    """原地址在池里是否仍然可用（alive 且质量达标）

    用于「优先贴近原版」：原地址自己还好端端活着的时候不要动它。以前只要池里
    有更快/更优的地址就换，结果像 Mogg 这种原地址一直可用的站，ext 也会被改写成
    另一个域名 —— 配置就会与原版不一致（用户明确要求尽量不动原版）。
    """
    if not ent or not base:
        return False
    for d in (ent.get("domains") or []):
        if not S.same_site(d.get("url") or "", base):
            continue
        return d.get("status") == "alive" and int(d.get("quality") or 0) >= minq
    return False


def _health_state():
    """读巡检状态（含每 TAG 的连续失败轮数），用于失效标注的滞后判定"""
    f = getattr(C, "F_HEALTH_STATE", None)
    if not f:
        return {}
    try:
        return store.load(f, {}) or {}
    except Exception:
        return {}


def _applied_bases():
    """读「我们主动改写过的 base」——{TAG: url}

    【为什么需要 —— 2026-10-17】build() 每轮都从 baseline 重建，而只有探到可用
    地址时才把新 base 写回 ext。于是某一轮探测抖动（本轮无可用）时，上一轮的修复
    会被静默撤销、退回原版的**已知死域名**；下一轮探通又写回来 —— 服务端下发的
    配置自己来回翻，客户端看到的就是「接口反复失效与修复」。
    有了这份记录，本轮没有更好的选择时**保持上次的修复**，不回退。
    """
    f = getattr(C, "F_BASES", None)
    if not f:
        return {}
    try:
        return store.load(f, {}) or {}
    except Exception:
        return {}


def build(pool, mode="dual", public_base=None):
    """生成修复后的 jsm.json 内容（dict）

    mode: direct | gateway | dual

    public_base: 一次性指定渲染口径（= renderbase 的手动 pin）。
        当前没有任何调用方传它；保留是为了外部脚本仍能覆盖渲染口径，
        但**改走 renderbase.set_manual**，不再直接改写 C.PUBLIC_BASE 全局
        —— 否则「页面显示的渲染口径」与「实际渲染用的」会对不上。
    """
    jsm = load_baseline()
    if public_base:
        RENDER.set_manual(public_base, reason="build(public_base=...) 传入")

    hstate = _health_state()
    need = max(1, int(getattr(C, "OFFLINE_CONFIRM_ROUNDS", 2)))
    applied = _applied_bases()

    changed, offline, clone_sites = [], [], []
    new_applied = {}
    for s in jsm.get("sites", []):
        ext = s.get("ext")
        if not isinstance(ext, str):
            continue
        info = S.parse_ext(ext)
        if not info:
            continue
        # 用与 report.sites_report / sources.build_sources 完全一致的 TAG 推导：
        # ext 尾部 TAG 优先，没有就退回 api。否则「第二段直接写域名」的站点
        # （hdmoli / 662688.xyz / libvio.to 等 10 个）会被整片跳过 —— 它们既不
        # 参与失效判定、域名死了也不会被换掉。
        tag = S.tag_of(s, info)
        if not tag:
            continue
        ent = pool.get(tag)
        detail = health.pick_best_detail(ent) if ent else None
        # 只认 q>=2（带站点特征、或网盘聚合站的正常首页）。q1 是门户页/空壳站/
        # 人机校验页，用它去替换原地址反而是降级，所以宁可不改。
        best = detail["url"] if detail and (detail.get("quality") or 0) >= 2 else None
        # 「有东西活着」：q>=1 也算。它不用于替换地址，但用于**免于误判失效**
        # —— 人机校验页/前端空壳证明站点没死，只是我们这种数据中心 IP 探不到真相。
        alive_any = health.survivable_count(ent) if ent else 0

        if mode == "gateway":
            gb = gateway_base(tag)
            if gb:
                s["ext"] = S.set_ext_base(ext, gb)
                changed.append({"tag": tag, "key": s.get("key"), "to": gb})
            continue

        keep = applied.get(tag)
        # 上一轮写过的地址可能后来被用户判弃用（RETIRED_DOMAINS），此时不能再用它
        # 兜底 —— 否则「保持上次修复」会把一个已到期弃用的域名继续下发。
        if keep and S.is_retired(tag, keep):
            keep = None
        target = None
        if best:
            # 两条「尽量贴近原版」的守则，顺序不能反：
            #   1) 原地址自己还活着（alive 且 q>=2）-> 一字不改，哪怕池里有更快的
            #   2) 要改就只改实质不同的地址，池里那份如果只是规范化写法（差尾斜杠/
            #      大小写）也不写回
            if not _base_alive(ent, info["base"]):
                target = best
        elif keep and not _base_alive(ent, info["base"]) \
                and S.norm_base(keep) != S.norm_base(info["base"]):
            # 本轮一条可用地址都没有，但上一轮修过 —— **保持上次的修复**，
            # 绝不退回原版那个已知死域名（否则配置自己来回翻，见 _applied_bases）
            target = keep

        if target and S.norm_base(target) != S.norm_base(info["base"]):
            changed.append({"tag": tag, "key": s.get("key"),
                            "from": info["base"], "to": target})
            s["ext"] = S.set_ext_base(ext, target)
            new_applied[tag] = target

        if best:
            if mode == "dual":
                gb = gateway_base(tag)
                if gb:
                    clone = json.loads(json.dumps(s, ensure_ascii=False))
                    clone["key"] = s.get("key", tag) + "_GW"
                    clone["name"] = (s.get("name") or tag) + "·网关"
                    clone["ext"] = S.set_ext_base(ext, gb)
                    clone_sites.append(clone)
        elif alive_any > 0:
            # 有存活地址但质量存疑 —— 保留原地址，也**不判失效、不告警**。
            # 这一档专门用来吸收「人机校验页 / 前端空壳」这类探不到真相的情况。
            store.log_event("health",
                            f"{tag}({s.get('key')}) 有 {alive_any} 条地址存活但质量存疑"
                            f"（人机校验/空壳），保留原地址，不判失效")
        else:
            # 本轮没有可用地址 —— 但不立刻打「⚠️失效」。
            # 域名池本身会抖（偶发超时/5xx），一抖就改名字会让用户看到接口名
            # 一会儿一个样（用户反馈的「反复失效」「标注失效但实测能用」）。
            # 只有**连续 need 轮**都无可用才认定真失效。
            streak = int((hstate.get(tag) or {}).get("streak") or 0)
            confirmed = streak >= need
            offline.append({"tag": tag, "key": s.get("key"),
                            "streak": streak, "confirmed": confirmed})
            # 标注写进 name 会破坏与原版的逐字一致性，且端缓存后「恢复了也不消失」
            # （用户 3 号反馈）。默认只记事件、不改 name；确需直观看失效时用
            # OKTV_MARK_NAMES=1 临时打开。失效状态由 /api/sites、/live、通知表达。
            if confirmed and getattr(C, "MARK_NAMES", False):
                s["name"] = (s.get("name") or "") + "⚠️失效"
                store.log_event("offline",
                                f"{tag}({s.get('key')}) 连续 {streak} 轮无可用地址，已标注失效")
            elif confirmed:
                store.log_event("offline",
                                f"{tag}({s.get('key')}) 连续 {streak} 轮无可用地址，"
                                f"判定失效（未写入 name，见 /api/sites）")
            else:
                store.log_event("health",
                                f"{tag}({s.get('key')}) 本轮无可用地址（连续 {streak} 轮，"
                                f"未达 {need} 轮阈值，暂不标注失效）")

    if mode == "dual":
        jsm["sites"] = jsm.get("sites", []) + clone_sites

    # 合并 overrides：手工新增/删除/改名
    jsm = _apply_overrides(jsm)

    # 接口选择页排序：置顶项 + 其余按实测延迟升序（只动顺序，不动内容）
    ostat = O.apply(jsm, pool)

    # 记下「我们改写过的 base」，供下一轮在没有更好选择时保持（不回退到原版死域名）
    if mode != "gateway":
        merged = dict(applied)
        merged.update(new_applied)
        try:
            store.save(C.F_BASES, merged)
        except Exception as e:
            store.log_event("error", f"bases 落盘失败: {type(e).__name__}: {e}")

    stat = {"changed": changed, "offline": offline, "clones": len(clone_sites),
            "order": ostat}
    return jsm, stat


def _apply_overrides(jsm):
    ov = store.load(C.F_OVERRIDES, {}) or {}
    drop = set(ov.get("drop_keys", []))
    if drop:
        jsm["sites"] = [s for s in jsm.get("sites", []) if s.get("key") not in drop]
    for extra in ov.get("add_sites", []):
        if not any(s.get("key") == extra.get("key") for s in jsm.get("sites", [])):
            jsm["sites"].append(extra)
    return jsm


def write_dist(jsm):
    """写出成品配置：本地 dist/ 一份，长期维护的 hai 包目录（PKG_DIR）一份"""
    os.makedirs(C.DIST_DIR, exist_ok=True)
    body = json.dumps(jsm, ensure_ascii=False, indent=1)
    p = os.path.join(C.DIST_DIR, "jsm.json")
    with open(p, "w", encoding="utf-8") as f:
        f.write(body)

    pkg = _write_pkg(jsm, body)

    ver = {"generated": __import__("time").strftime("%Y-%m-%d %H:%M:%S"),
           "sites": len(jsm.get("sites", [])),
           "mode": store.get_state().get("mode"),
           "package": C.PKG_DIR if pkg else None}
    with open(os.path.join(C.DIST_DIR, "version.json"), "w", encoding="utf-8") as f:
        json.dump(ver, f, ensure_ascii=False, indent=2)
    return p


def _write_pkg(jsm, body):
    """把成品配置写进 hai 包目录；目录不存在则跳过（例如本地开发机）"""
    try:
        if not os.path.isdir(C.PKG_DIR):
            return False
        # 先写临时文件再原子替换，避免电视机正好读到半截配置
        tmp = os.path.join(C.PKG_DIR, ".jsm.json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(body)
        os.replace(tmp, os.path.join(C.PKG_DIR, "jsm.json"))
        with open(os.path.join(C.PKG_DIR, "VERSION"), "w", encoding="utf-8") as f:
            f.write(store.get_state().get("version", "1.0.0") + "\n" +
                    __import__("time").strftime("%Y-%m-%d %H:%M:%S\n"))
        return True
    except Exception as e:
        store.log_event("error", f"写 hai 包失败: {type(e).__name__}: {e}")
        return False


def regenerate(pool, mode=None):
    mode = mode or store.get_state().get("mode", "direct")
    jsm, stat = build(pool, mode=mode)
    write_dist(jsm)
    conf = sum(1 for x in stat["offline"] if x.get("confirmed"))
    store.log_event("render", f"生成配置：模式 {mode}，改写 {len(stat['changed'])} 条，"
                              f"无可用 {len(stat['offline'])} 条（已确认失效 {conf} 条），"
                              f"网关备份 {stat['clones']} 条")
    return jsm, stat
