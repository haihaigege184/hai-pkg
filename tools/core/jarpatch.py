# -*- coding: utf-8 -*-
"""PG 包 jar 补丁：把 Init.init 的包名白名单加上我们自己的包名。

为什么要有这一步
----------------
`pg.jar` 的 `com.github.catvod.spider.Init.init(Context)` 用一个**包名白名单**
判断"这是不是原版 App"：

    com.fongmi.android.tv / com.hisen.android.tv
    com.silent.android.webhtv / com.layaboxhmhz.gamehmhz.okys

不在名单里就弹「本接口完全免费。检测到您使用的不是原版"影视","OK影视"之一…」，
并且**降级网盘播放、不启动 allinone**（直播由此不可用）。

我们的二开包叫 `com.hai.tv`，必然中招。所以每次换包后要把白名单里
`com.hisen.android.tv` 换成 `com.hai.tv`（同为 20→10 字节，原地可容纳）。

怎么改
------
dex 里的字符串不是明文，是 XOR 加密后放在 `fill-array-data` payload 里：
    data[i] ^ key[i % key.len]
payload 头是 ident(2)=0x0300 + width(2) + size(4)。

这里不做完整 dex 解析（服务器上没 androguard），而是**扫全 dex 的 payload**：
拿 size 正好等于目标串长度的当 data、size 4~64 的当 key，两两试解，
命中明文就改写这个 data payload 的 size 与内容，最后重算 header 的
sha1(signature) 与 adler32(checksum)。

找不到就原样不动 —— 换了新加密方式也不会把包改坏。
"""
import hashlib
import io
import os
import struct
import zipfile
import zlib

WANT_OLD = "com.hisen.android.tv"   # 白名单里最像一个普通包名的那个
# 2026-10-30：APK 包名已定为 com.hisen.android.tv（白名单原生通过），
# NEW_NAME 与 WANT_OLD 相同 ⇒ 补丁恒为 no-op，仅留作包名再变更时的兜底机制。
NEW_NAME = "com.hisen.android.tv"

_IDENT = 0x0300


def _payloads(buf):
    """扫出所有可能的 fill-array-data payload"""
    out = []
    n = len(buf)
    i = 0
    while True:
        i = buf.find(b"\x00\x03", i)
        if i < 0 or i + 8 > n:
            break
        ident, width, size = struct.unpack_from("<HHI", buf, i)
        if (ident == _IDENT and width in (1, 2, 4, 8)
                and 0 < size <= 4096 and i + 8 + size * width <= n):
            out.append((i, width, size, bytes(buf[i + 8: i + 8 + size * width])))
        i += 2
    return out


def _find_hit(buf, want):
    """返回 (payload_pos, key_bytes, old_size) 或 None"""
    pl = _payloads(buf)
    tb = want.encode("utf-8")
    cands = [p for p in pl if p[1] == 1 and p[2] == len(tb)]
    keys = []
    seen = set()
    for p in pl:
        if p[1] != 1 or not (4 <= p[2] <= 64):
            continue
        d = p[3]
        if d in seen:
            continue
        seen.add(d)
        keys.append(d)
    for pos, _w, size, data in cands:
        if data == tb:              # 已经是明文（极少见）
            return pos, b"\x00", size
        for k in keys:
            kl = len(k)
            if all((data[i] ^ k[i % kl]) == tb[i] for i in range(size)):
                return pos, k, size
    return None


def patch_dex(dex_bytes, want=WANT_OLD, new=NEW_NAME):
    """返回 (新dex, 是否改动)"""
    buf = bytearray(dex_bytes)
    hit = _find_hit(buf, want)
    if not hit:
        return bytes(buf), False
    pos, key, size = hit
    enc = bytes(new.encode("utf-8")[i] ^ key[i % len(key)]
                for i in range(len(new.encode("utf-8"))))
    if len(enc) > size:
        return bytes(buf), False
    struct.pack_into("<I", buf, pos + 4, len(enc))          # size 改小
    struct.pack_into("<%ds" % size, buf, pos + 8,
                     enc + b"\x00" * (size - len(enc)))     # 余下补零，不会被读到
    buf[12:32] = hashlib.sha1(bytes(buf[32:])).digest()     # signature
    struct.pack_into("<I", buf, 8, zlib.adler32(bytes(buf[12:])) & 0xFFFFFFFF)
    return bytes(buf), True


def patch_jar(path, want=WANT_OLD, new=NEW_NAME):
    """就地给 pg.jar 打补丁，并同步同目录的 pg.jar.md5

    返回 {"patched": bool, "md5": str, "size": int, "reason": str}
    """
    if not os.path.isfile(path):
        return {"patched": False, "reason": "文件不存在"}
    try:
        zin = zipfile.ZipFile(path)
        names = zin.namelist()
        if "classes.dex" not in names:
            return {"patched": False, "reason": "jar 内没有 classes.dex"}
        new_dex, ok = patch_dex(zin.read("classes.dex"), want, new)
        if not ok:
            zin.close()
            return {"patched": False, "reason": "未命中包名白名单（可能换包或换了加密）"}

        out = io.BytesIO()
        zout = zipfile.ZipFile(out, "w")
        for it in zin.infolist():
            data = new_dex if it.filename == "classes.dex" else zin.read(it.filename)
            zi = zipfile.ZipInfo(it.filename, date_time=it.date_time)
            zi.compress_type = it.compress_type
            zi.external_attr = it.external_attr
            zout.writestr(zi, data)
        zout.close()
        zin.close()

        blob = out.getvalue()
        with open(path, "wb") as f:
            f.write(blob)
        md5 = hashlib.md5(blob).hexdigest()
        mf = path + ".md5"
        try:
            with open(mf, "w", encoding="utf-8") as f:
                f.write(md5)
        except Exception:
            pass
        return {"patched": True, "md5": md5, "size": len(blob), "reason": ""}
    except Exception as e:
        return {"patched": False, "reason": "%s: %s" % (type(e).__name__, e)}


def file_sha256(path, limit=16):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:limit]


def restore_whitelist(path, cur="com.hai.tv", want=WANT_OLD):
    """把已被改小的白名单槽位还原成 want（2026-10-30 包名定稿 hisen 用）。

    背景：旧补丁把 20 字节的 com.hisen 槽位改写成 10 字节的 com.hai.tv
    （size 字段 20→10，**20 字节的文件区域原样还在**）。APK 包名后来定为
    com.hisen.android.tv，需要把这条白名单改回来 —— 但 patch_jar 的原地
    改写只支持「新串不比旧串长」，10→20 装不下。
    这里做精确还原：用命中时拿到的同一把 key 重算 20 字节密文，
    把 size 字段改回 20、整段写回，最后重算 signature/checksum。
    """
    if not os.path.isfile(path):
        return {"patched": False, "reason": "文件不存在"}
    try:
        zin = zipfile.ZipFile(path)
        if "classes.dex" not in zin.namelist():
            return {"patched": False, "reason": "jar 内没有 classes.dex"}
        buf = bytearray(zin.read("classes.dex"))
        hit = _find_hit(buf, cur)
        if not hit:
            return {"patched": False, "reason": "没找到 %s（可能已还原）" % cur}
        pos, key, size = hit
        wb = want.encode("utf-8")
        if len(wb) < size:
            return {"patched": False, "reason": "目标串比现有槽位还短，请走 patch_jar"}
        if pos + 8 + len(wb) > len(buf):
            return {"patched": False, "reason": "原槽位越界，区域布局变了，拒绝动"}
        enc = bytes(wb[i] ^ key[i % len(key)] for i in range(len(wb)))
        struct.pack_into("<I", buf, pos + 4, len(wb))
        buf[pos + 8: pos + 8 + len(wb)] = enc
        buf[12:32] = hashlib.sha1(bytes(buf[32:])).digest()
        struct.pack_into("<I", buf, 8, zlib.adler32(bytes(buf[12:])) & 0xFFFFFFFF)

        out = io.BytesIO()
        zout = zipfile.ZipFile(out, "w")
        for it in zin.infolist():
            data = bytes(buf) if it.filename == "classes.dex" else zin.read(it.filename)
            zi = zipfile.ZipInfo(it.filename, date_time=it.date_time)
            zi.compress_type = it.compress_type
            zi.external_attr = it.external_attr
            zout.writestr(zi, data)
        zout.close()
        zin.close()

        blob = out.getvalue()
        with open(path, "wb") as f:
            f.write(blob)
        md5 = hashlib.md5(blob).hexdigest()
        try:
            with open(path + ".md5", "w", encoding="utf-8") as f:
                f.write(md5)
        except Exception:
            pass
        # 自检：还原后的 payload 解密必须等于 want
        dex2 = zipfile.ZipFile(path).read("classes.dex")
        ok = _find_hit(dex2, want) is not None and _find_hit(dex2, cur) is None
        return {"patched": ok, "md5": md5, "size": len(blob),
                "reason": "" if ok else "还原后自检未通过"}
    except Exception as e:
        return {"patched": False, "reason": "%s: %s" % (type(e).__name__, e)}
