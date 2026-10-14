"""上游同步：从多个公开镜像拉取最新接口配置，自动发现新域名并并入域名池

设计动机
  这些网盘类接口的站点域名会不停漂移（换域名、被墙、跑路）。
  单靠包里写死的地址必然失效；因此把「配置」与「地址」解耦：
      上游配置 -> 抽取候选域名 -> 入池 -> 健康检查 -> 选主 -> 重新生成配置
"""
import json
import re
import time

import requests
import urllib3

from . import config as C
from . import health
from . import sources as S
from . import store

urllib3.disable_warnings()


def _fetch(url, timeout=15):
    try:
        r = requests.get(url, timeout=timeout, verify=False,
                         headers={"User-Agent": C.UA})
        if r.status_code == 200 and len(r.content) > 200:
            return r.text
    except Exception:
        pass
    return None


def fetch_upstream_jsm():
    """按镜像优先级拉取上游 jsm.json，返回 (text, url) 或 (None, None)"""
    feeds = fetch_all_upstream()
    return feeds[0] if feeds else (None, None)


def fetch_all_upstream():
    """拉取**所有**可达的上游配置（不只第一个）。

    不同社区仓库收录的域名池并不一致，全部合并才能最大化候选域名覆盖面。
    返回 [(text, url), ...]
    """
    out = []
    for item in C.UPSTREAM_REPOS:
        spec, _, path = item.partition(":")
        repo, _, branch = spec.partition("@")
        branch = branch or "main"
        path = path or "jsm.json"
        for tpl in C.UPSTREAM_JSM_MIRRORS:
            url = tpl.replace("@main/", "@" + branch + "/").format(repo=repo, path=path)
            txt = _fetch(url)
            if not txt:
                continue
            try:
                json.loads(txt)
            except Exception:
                continue
            out.append((txt, url))
            break          # 该仓库已拿到一份，换下一个仓库
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
    """
    added_total = 0
    for txt, used in feeds:
        try:
            jsm = json.loads(txt)
        except Exception:
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


def sync_pool(pool, sources_registry):
    """执行一次完整同步：拉取所有上游 -> 补域名入池 -> 登记新 TAG
    返回统计 dict
    """
    feeds = fetch_all_upstream()
    stat = {"ok": False, "sources": [u for _, u in feeds], "new_domains": 0,
            "new_tags": [], "tags": 0, "harvested": 0}
    if not feeds:
        store.log_event("sync", "上游配置全部不可达，仅执行域名采集")
        stat["harvested"] = harvest_domains(pool)
        health.check_all(pool, only_dead=True)
        store.set_state(last_sync=int(time.time()), last_sync_source="仅采集")
        stat["ok"] = bool(stat["harvested"])
        return stat

    merged_tags = {}
    for txt, used in feeds:
        try:
            jsm = json.loads(txt)
        except Exception as e:
            store.log_event("sync", f"解析失败 {used}: {e}")
            continue
        for tag, doms in extract_from_jsm(jsm).items():
            bucket = merged_tags.setdefault(tag, [])
            for d in doms:
                if d not in bucket:
                    bucket.append(d)

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

    # 通用域名采集（补上游覆盖不到的站点）
    stat["harvested"] = harvest_domains(pool)
    # 按站名认领（域名漂移最有效的兜底通道）
    stat["harvested"] += harvest_by_name(pool, feeds)

    # 新并入的域名立刻探测一次
    if stat["new_domains"] or stat["harvested"]:
        health.check_all(pool, only_dead=True)

    store.set_state(last_sync=int(time.time()),
                    last_sync_source=f"{len(feeds)} 个上游源")
    store.log_event("sync", f"同步完成：{len(feeds)} 个上游源，覆盖 {stat['tags']} 个 TAG，"
                            f"新增域名 {stat['new_domains']} 个，采集 {stat['harvested']} 个")
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
