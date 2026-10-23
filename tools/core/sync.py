"""上游同步：从多个公开镜像拉取最新接口配置，自动发现新域名并并入域名池

设计动机
  这些网盘类接口的站点域名会不停漂移（换域名、被墙、跑路）。
  单靠包里写死的地址必然失效；因此把「配置」与「地址」解耦：
      上游配置 -> 抽取候选域名 -> 入池 -> 健康检查 -> 选主 -> 重新生成配置
"""
import json
import os
import re
import time

import requests
import urllib3

from . import config as C
from . import health
from . import navwatch
from . import sources as S
from . import store
from . import upstream

urllib3.disable_warnings()


def _fetch(url, timeout=None):
    try:
        r = requests.get(url, timeout=timeout or getattr(C, "UPSTREAM_TIMEOUT", 10),
                         verify=False, headers={"User-Agent": C.UA})
        if r.status_code == 200 and len(r.content) > 200:
            return r.text
    except Exception:
        pass
    return None


def fetch_upstream_jsm():
    """按镜像优先级拉取上游配置，返回 (cfg_dict, url) 或 (None, None)"""
    feeds = fetch_all_upstream()
    return (feeds[0][0], feeds[0][1]) if feeds else (None, None)


def fetch_all_upstream():
    """拉取**所有**可达的上游配置（不只第一个）。

    不同社区仓库收录的域名池并不一致，全部合并才能最大化候选域名覆盖面。

    返回 `[(cfg_dict, url, note), ...]`。

    【2026-10-22 改成返回 dict 而不是原始文本】
    以前只收「json.loads 能过」的，于是三类源等于不存在：
      * 带 `//` JS 注释的配置（饭太硬、王二小都这样）；
      * `图片藏配置`（JPEG 尾部 base64）；
      * 跳转页（gh-proxy 先回一个 location.replace 的壳）。
    现在统一交给 `core/upstream.fetch_config()` 识别，note 里记清是哪种形态。
    """
    out = []
    rows = []                      # 供 navwatch.record_feeds 记账（地址变化留档）
    for item in C.UPSTREAM_REPOS:
        spec, _, path = item.partition(":")
        repo, _, branch = spec.partition("@")
        branch = branch or "main"
        path = path or "jsm.json"
        got = False
        for tpl in C.UPSTREAM_JSM_MIRRORS:
            url = tpl.replace("@main/", "@" + branch + "/").format(repo=repo, path=path)
            cfg, final, note = upstream.fetch_config(url)
            if not cfg:
                continue
            out.append((cfg, url, note, repo.split("/")[-1]))
            # 第 5 位 track=False：GitHub 源有多镜像（fastly / cdn / gh-proxy），
            # 本轮谁先成功用谁，地址每轮都在换 —— 不参与「接口地址变化」判定
            rows.append((repo.split("/")[-1], final or url, True, note, False))
            got = True
            break          # 该仓库已拿到一份，换下一个仓库
        if not got:
            rows.append((repo.split("/")[-1], "", False, "所有镜像都抓不到", False))
    # 直连上游（非 GitHub 镜像）：饭太硬 / 王二小 / 江苏福建 / 南风 这类站自己发的配置
    for feed in getattr(C, "UPSTREAM_FEEDS", []) or []:
        if feed.get("dead"):
            continue                       # 已判死的只展示，不浪费超时
        got = None
        errs = []
        cand = list(feed.get("urls") or [])
        # 导航页里该站**当前**的地址排在最前：作者换域名时第一时间改的是导航页，
        # 我们手写的 UPSTREAM_FEEDS 反而是滞后的那一份。取上一次落盘的快照
        # （不在这里再抓一遍，抓取由 navwatch.scan 在同步开头统一做）。
        try:
            for u in navwatch.urls_for(feed["name"], feed.get("nav")):
                if u not in cand:
                    cand.insert(0, u)
        except Exception:
            pass
        for url in cand:
            cfg, final, note = upstream.fetch_config(url)
            if cfg:
                got = (cfg, final, note, feed["name"])
                rows.append((feed["name"], final or url, True, note, True))
                break
            errs.append("%s → %s" % (url.split("/")[2] if "//" in url else url, note[:60]))
        if got:
            out.append(got)
        else:
            rows.append((feed["name"], "", False, "；".join(errs)[:160], True))
            store.log_event("sync", "直连上游 %s 不可用：%s" % (feed["name"], "；".join(errs)[:240]))
    # 每个源的「接口地址」变化留档（站方换路径 / 换域名都能看出来）
    try:
        navwatch.record_feeds(rows)
    except Exception as e:
        store.log_event("error", "上游地址记账失败: %s: %s" % (type(e).__name__, e))
    return out


def harvest_domains(pool):
    """通用域名采集：扫描社区配置里的所有 URL，按 host 关键词归入对应 TAG。

    解决「上游 jsm.json 停更、但站点其实换了域名」的场景——
    只要还有人把新域名写进自己的配置里，这里就能捞到。
    """
    found = {}
    for url in C.HARVEST_SOURCES:
        txt = _fetch(url)
        if not txt:
            continue
        for raw in re.findall(r'https?://[A-Za-z0-9._\-]+', txt):
            host = S.norm_host(raw)
            if not host:
                continue
            for tag, keys in C.TAG_ALIASES.items():
                if any(k in host for k in keys):
                    found.setdefault(tag, []).append(raw)
    added_total = 0
    for tag, urls in found.items():
        ent = pool.setdefault(tag, {"tag": tag, "domains": []})
        added = S.merge_domains(ent, urls, origin="harvest")
        if added:
            added_total += len(added)
            store.log_event("harvest", f"{tag} 采集到 {len(added)} 个候选域名: {', '.join(added)}")
    return added_total


def harvest_by_name(pool, feeds):
    """按「站名关键词」从上游配置里认领域名。

    这些站点换域名像换衣服，但站名基本不动（盘Ta / 蜡笔 / 影巢 …）。
    社区配置里只要还有人写着这个名字，就能把它的新地址捞回来，
    比按 host 关键词匹配覆盖面大得多。

    feeds 元素是 `(cfg_dict, url[, note])`；为兼容旧调用也接受 `(text, url)`。
    """
    added_total = 0
    for feed in feeds:
        jsm, used = feed[0], feed[1]
        if isinstance(jsm, str):
            try:
                jsm = json.loads(jsm)
            except Exception:
                continue
        if not isinstance(jsm, dict):
            continue
        for s in jsm.get("sites", []):
            name = str(s.get("name") or "")
            if not name:
                continue
            ext = s.get("ext")
            info = S.parse_ext(ext) if isinstance(ext, str) else None
            if not info:
                continue
            base = S.norm_base(info["base"])
            if not base:
                continue
            for tag, keys in C.TAG_NAME_KEYS.items():
                if not any(k in name for k in keys):
                    continue
                ent = pool.setdefault(tag, {"tag": tag, "domains": []})
                if S.merge_domains(ent, [base], origin="byname"):
                    added_total += 1
                    store.log_event(
                        "harvest", f"{tag} 按站名「{name}」认领域名 {base}（来源 {used.split('/')[2]}）")
    return added_total


def extract_from_jsm(jsm):
    """从上游配置里抽取 {tag: [域名...]}"""
    found = {}
    for s in jsm.get("sites", []):
        ext = s.get("ext")
        if not isinstance(ext, str):
            continue
        info = S.parse_ext(ext)
        if not info or not info["tag"]:
            continue
        found.setdefault(info["tag"], [])
        b = S.norm_base(info["base"])
        if b and b not in found[info["tag"]]:
            found[info["tag"]].append(b)
    return found


def harvest_pkg(pool):
    """从**成品包自身**采集域名 —— 比社区上游更权威的一条来源。

    【为什么必须加这个 —— 2026-10-22 实测】
    8 个社区上游里能找到的域名，与池里已有的**完全重合**（新增恒为 0）：
      * 这些网盘聚合站（玩偶/木偶/蜡笔/盘Ta…）不进公开 TVBox 配置，
        社区里翻来覆去就是同一份地址，互相抄；
      * 8 个上游里只有 qist/tvbox 还在更新（其余最近一次 push 是 2024 年）。
    真正的「新地址」首发在**作者发布的包里**：`js/*.js` 与 `lib/*.json` 里
    写死了各站当前域名。实测当前包就挖出 3 个池里没有的地址
    （libvio.app / libvio.cc / tv.libvio.cc）。
    所以每轮同步 + 每次换包后都扫一遍包目录。
    """
    root = C.PKG_DIR if os.path.isdir(C.PKG_DIR) else os.path.join(C.BASE_DIR, "assets")
    skip_ext = (".jar", ".xz", ".gz", ".zip", ".png", ".jpg", ".jpeg", ".gif",
                ".webp", ".ico", ".ttf", ".woff", ".woff2", ".so", ".dex")
    found = {}
    scanned = 0
    for dp, _dns, fns in os.walk(root):
        for fn in fns:
            if fn.lower().endswith(skip_ext):
                continue
            p = os.path.join(dp, fn)
            try:
                if os.path.getsize(p) > 4 * 1024 * 1024:      # 超大文件跳过（min.js 也够用）
                    continue
                txt = open(p, "rb").read().decode("latin-1")
            except Exception:
                continue
            scanned += 1
            for raw in re.findall(r'https?://[A-Za-z0-9._\-]+', txt):
                host = S.norm_host(raw)
                if not host:
                    continue
                for tag, keys in C.TAG_ALIASES.items():
                    if any(k in host for k in keys):
                        found.setdefault(tag, []).append(raw)
    added_total = 0
    for tag, urls in found.items():
        ent = pool.setdefault(tag, {"tag": tag, "domains": []})
        added = S.merge_domains(ent, urls, origin="pkg")
        if added:
            added_total += len(added)
            store.log_event("harvest", f"{tag} 从包内采集到 {len(added)} 个候选域名: {', '.join(added)}")
    return added_total, scanned


def _norm_name(s):
    """站名归一：去掉表情、装饰符、空格，只留中日韩与字母数字。

    PG 里叫「👽玩偶哥哥┃4K弹幕」，上游叫「玩偶 • 4K」——
    直接比字符串永远不相等，必须剥掉装饰再比。
    """
    return re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]+", "", str(s or "")).lower()


# 壳子自带的功能项，不是「站源」，加进去也没内容
_BUILTIN_APIS = {"csp_LocalFile", "csp_Config", "csp_PushAgent", "csp_QuickSearch"}


def _brand_of(name):
    """站名「品牌」：归一化后取前 2 个汉字（英文取前 4 个字母）。

    PG 叫「木偶哥哥|网盘」，上游叫「木偶 • 4K」——
    归一化后是 `木偶哥哥网盘` vs `木偶4k`，互不包含，子串匹配也失效。
    但「品牌」都是 `木偶`。同类还有 蜡笔|网盘 vs 蜡笔 • 4K。
    """
    nn = _norm_name(name)
    m = re.match(r"[\u4e00-\u9fff]{2,}", nn)
    if m:
        return m.group(0)[:2]
    return nn[:4]


def _name_is_known(nn, names):
    """归一化站名是否已在 PG 里（完全相等 / 互相包含 / 品牌相同）。"""
    if not nn:
        return False
    if nn in names:
        return True
    if len(nn) < 2:
        return False
    for kn in names:
        if len(kn) < 2:
            continue
        if nn in kn or kn in nn:
            return True
        if _brand_of(nn) and _brand_of(nn) == _brand_of(kn) and len(_brand_of(nn)) >= 2:
            return True
    return False


def _known_site_index():
    """PG 包里现有的站点指纹：key 集合 / api 集合 / 归一化站名集合。

    【为什么不能只比 key —— 2026-10-22 实测】
    拿上游 9 份配置直接按 key 比，得出「507 个 PG 没有的站点」，
    但里面 玩偶/木偶/蜡笔/配置中心/本地 全是**已经有的站**，只是 key 不同：
        PG    : key=Wogg        api=csp_Wogg  name=👽玩偶哥哥┃4K弹幕
        上游  : key=玩偶         api=csp_Wogg  name=玩偶 • 4K
    `api` 相同就说明是同一个站。所以三个维度任一命中即视为「已有」。
    """
    keys, apis, names = set(), set(), set()
    for p in (os.path.join(C.DIST_DIR, "jsm.json"), C.BASELINE_JSM,
              os.path.join(getattr(C, "PKG_DIR", ""), "jsm.json")):
        try:
            with open(p, "r", encoding="utf-8") as f:
                d = json.load(f)
        except Exception:
            continue
        for s in d.get("sites") or []:
            if not isinstance(s, dict):
                continue
            if s.get("key"):
                keys.add(str(s["key"]))
            if s.get("api"):
                apis.add(str(s["api"]))
            n = _norm_name(s.get("name"))
            if n:
                names.add(n)
        if keys:
            break
    return keys, apis, names


def collect_new_sites(feeds):
    """从上游配置里挑出「PG 包里没有的站点」，落盘 `data/new_sites.json`。

    **只登记，不注入**。理由：
      * 直接往 jsm.json 塞站点属于「新增」，会改变下发内容；而 patch.build()
        每轮从 baseline 重建，塞进去也会被下一轮冲掉，除非单独持久化；
      * 这些游站很多是别人的站源，能不能用、要不要给老人小孩看，得人来定。
    清单摊在维护页「上游源」下方，要哪几个再加。

    过滤规则（宁可少报，不要虚报）：
      1. key / api / 归一化站名 任一已在 PG 里 → 不算新；
      2. `api` 以 `./` 开头（`./lib/drpy2.min.js`、`./py/xxx.py`）→ 需要包内不存在的
         脚本文件，加进 PG 也是坏站 → 跳过；
      3. 没有 ext/base 也没有 csp_ 类 api 的（纯内置占位）→ 跳过。

    返回登记到的条数。
    """
    keys, apis, names = _known_site_index()
    if not keys:
        return 0
    found = {}
    for feed in feeds:
        jsm, used = feed[0], feed[1]
        if not isinstance(jsm, dict):
            continue
        for s in jsm.get("sites") or []:
            if not isinstance(s, dict):
                continue
            key = str(s.get("key") or "")
            api = str(s.get("api") or "")
            name = str(s.get("name") or "")
            nn = _norm_name(name)
            if not key and not nn:
                continue
            if key in keys or api in apis or _name_is_known(nn, names):
                continue
            if api in _BUILTIN_APIS:
                continue                      # 壳子自带功能项，不是站源
            if api.startswith("./") or api.startswith("../") or api.startswith("http"):
                continue                      # 需要额外脚本文件，包里没有
            if not api.startswith("csp_"):
                continue                      # 无 api 或非常规 api，加了也是空壳
            ext = s.get("ext")
            base = ""
            if isinstance(ext, str):
                info = S.parse_ext(ext)
                if info:
                    base = S.norm_base(info.get("base") or "")
            if not base and not api:
                continue
            rec = found.get(key or nn)
            if rec:
                continue
            found[key or nn] = {
                "key": key or nn,
                "name": name,
                "api": api,
                "type": s.get("type"),
                "base": base,
                "source": used,
                "seen": int(time.time()),
            }
    try:
        os.makedirs(C.DATA_DIR, exist_ok=True)
        tmp = os.path.join(C.DATA_DIR, "new_sites.json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"known": len(keys), "known_api": len(apis), "count": len(found),
                       "at": int(time.time()), "sites": list(found.values())},
                      f, ensure_ascii=False, indent=2)
        os.replace(tmp, os.path.join(C.DATA_DIR, "new_sites.json"))
    except Exception as e:
        store.log_event("sync", "登记新站点失败: %s" % e)
    if found:
        store.log_event("sync", "上游发现 %d 个 PG 里没有的站点（已登记，未注入）：%s"
                        % (len(found), "、".join(list(found)[:12])))
    return len(found)


def sync_pool(pool, sources_registry):
    """执行一次完整同步：拉取所有上游 -> 补域名入池 -> 登记新 TAG
    返回统计 dict
    """
    # ① 先抓导航页（饭太硬导航）。它记着各站**当前**该用的地址，先抓才能让
    #    fetch_all_upstream 把新地址排到候选最前面 —— 否则作者换了域名要等下一轮。
    nav = {}
    try:
        nst = navwatch.scan()
        nav = {"ok": bool(nst.get("ok")), "count": len(nst.get("items") or []),
               "changed_total": nst.get("changed_total") or 0,
               "changes": nst.get("changes") or {},
               "at": nst.get("at") or 0, "changes_at": nst.get("changes_at") or 0,
               "note": nst.get("note") or ""}
    except Exception as e:
        store.log_event("error", "导航页检查失败: %s: %s" % (type(e).__name__, e))
    # ② 再拉各上游配置（会用到 ① 的快照补地址）
    feeds = fetch_all_upstream()
    stat = {"ok": False, "sources": [f[1] for f in feeds], "new_domains": 0,
            "new_tags": [], "tags": 0, "harvested": 0, "pkg_added": 0,
            "pkg_scanned": 0, "source_detail": [], "new_sites": [], "nav": nav}
    if not feeds:
        store.log_event("sync", "上游配置全部不可达，仅执行域名采集")
        stat["harvested"] = harvest_domains(pool)
        stat["pkg_added"], stat["pkg_scanned"] = harvest_pkg(pool)
        health.check_all(pool, only_dead=True)
        store.set_state(last_sync=int(time.time()), last_sync_source="仅采集")
        stat["ok"] = bool(stat["harvested"] or stat["pkg_added"])
        return stat

    merged_tags = {}
    for item in feeds:
        jsm, used, note = item[0], item[1], item[2]
        fname = item[3] if len(item) > 3 else ""
        one = {"source": used, "name": fname, "sites": len(jsm.get("sites") or []),
               "tags": 0, "new": 0, "harvested": 0, "how": note}
        for tag, doms in extract_from_jsm(jsm).items():
            bucket = merged_tags.setdefault(tag, [])
            one["tags"] += 1
            for d in doms:
                if d not in bucket:
                    bucket.append(d)
        # 单源贡献度：这条源给某个 TAG 带来了几个池里没有的地址
        _name_add = harvest_by_name(pool, [(jsm, used)])
        one["new"] = _name_add
        one["harvested"] = _name_add
        stat["source_detail"].append(one)

    stat["tags"] = len(merged_tags)
    for tag, doms in merged_tags.items():
        if tag not in pool:
            pool[tag] = {"tag": tag, "domains": [], "origin_new": True,
                         "api": "", "proxy": "", "rules": ""}
            stat["new_tags"].append(tag)
        added = S.merge_domains(pool[tag], doms, origin="upstream")
        stat["new_domains"] += len(added)
        if added:
            store.log_event("sync", f"{tag} 并入 {len(added)} 个候选域名: {', '.join(added)}")

    # 种子兜底：SEED_DOMAINS 是人手维护「作者刚公布的域名」的入口（换域名时先写
    # 这里，比等上游配置更新快）。每轮补一次，池里已有的靠 merge_domains 去重；
    # 用户已判弃用的域名由 merge_domains 自己挡掉，不会回流。
    seeded = 0
    for tag, doms in (getattr(C, "SEED_DOMAINS", {}) or {}).items():
        e = pool.setdefault(tag, {"tag": tag, "domains": [], "api": "",
                                  "proxy": "", "rules": ""})
        add = S.merge_domains(e, doms, origin="seed")
        if add:
            seeded += len(add)
            store.log_event("sync", f"{tag} 从种子表补入 {len(add)} 个域名: {', '.join(add)}")
    stat["seeded"] = seeded

    # 通用域名采集（补上游覆盖不到的站点）
    stat["harvested"] = harvest_domains(pool)
    # 按站名认领（域名漂移最有效的兜底通道）
    stat["harvested"] += harvest_by_name(pool, feeds)
    # 包内域名采集（作者自己正在用的地址，最权威）
    stat["pkg_added"], stat["pkg_scanned"] = harvest_pkg(pool)
    # 上游里「PG 没有的站点」—— 只登记不注入
    stat["new_sites"] = collect_new_sites(feeds)

    # 新并入的域名立刻探测一次
    if stat["new_domains"] or stat["harvested"] or stat["pkg_added"] or stat.get("seeded"):
        health.check_all(pool, only_dead=True)

    store.set_state(last_sync=int(time.time()),
                    last_sync_source=f"{len(feeds)} 个上游源")
    store.log_event("sync", f"同步完成：{len(feeds)} 个上游源，覆盖 {stat['tags']} 个 TAG，"
                            f"新增域名 {stat['new_domains']} 个，采集 {stat['harvested']} 个，"
                            f"包内 {stat['pkg_added']} 个（扫 {stat['pkg_scanned']} 文件）")
    stat["ok"] = True
    return stat


def seed_pool_from_baseline(pool, registry):
    """首次运行时，用基线配置 + 内置已知域名知识库播种"""
    for tag, ent in registry.items():
        e = pool.setdefault(tag, {"tag": tag, "domains": []})
        e.setdefault("api", ent.get("api"))
        e.setdefault("proxy", ent.get("proxy"))
        e.setdefault("rules", ent.get("rules"))
        e.setdefault("name", ent.get("name"))
        S.merge_domains(e, ent.get("domains", []), origin="baseline")
    # 历史域名知识库兜底（上游挂掉时仍然有候选可探）
    for tag, doms in C.SEED_DOMAINS.items():
        e = pool.setdefault(tag, {"tag": tag, "domains": []})
        S.merge_domains(e, doms, origin="seed")
