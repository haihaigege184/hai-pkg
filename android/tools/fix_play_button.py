# -*- coding: utf-8 -*-
"""
离线快照收尾：让详情页「▶ 播放」只走 PG 原链路。

服务器原版详情页的内联脚本会给 #btnPlay 绑 click 监听，抢先执行自建取链
（改文案「本地取链…」+ 弹遮罩 + 等 /hai/poster/eps|play），快照里这些接口
已指向本地空对象，结果就是点了卡在加载中。这里在 </body> 前注入一段覆盖脚本：
用 cloneNode(true) 克隆按钮替换原节点 —— DOM 事件监听器不会被复制，
于是 href 里的 haiplay://search?q=片名 直接生效 → WebViewClient 拦截 →
PG 搜索 → 原版站源 + Go 取流。导航/特效等其它脚本完全不动。
"""
import os
import glob

HERE = os.path.dirname(os.path.abspath(__file__))
A = os.path.abspath(os.path.join(HERE, "..", "assets", "haiui"))

PATCH = (
    "<script>/* offline-snapshot: play -> PG original chain only */\n"
    "(function(){try{var b=document.getElementById('btnPlay');"
    "if(!b||!b.parentNode)return;var c=b.cloneNode(true);"
    "c.innerHTML='\\u25b6 \\u64ad\\u653e';c.removeAttribute('data-ready');"
    "b.parentNode.replaceChild(c,b);}catch(e){}})();\n</script>\n"
)


def main():
    n = 0
    for sub in ("detail", "pages"):
        for p in sorted(glob.glob(os.path.join(A, sub, "*.html"))):
            s = open(p, encoding="utf-8", errors="replace").read()
            if "offline-snapshot: play" in s:
                continue
            if "</body>" in s:
                s = s.replace("</body>", PATCH + "</body>", 1)
            else:
                s += PATCH
            open(p, "w", encoding="utf-8").write(s)
            n += 1
    print("已注入播放按钮覆盖脚本: %d 个页面" % n)
    # 自检
    p = os.path.join(A, "detail", "db1292052.html")
    if os.path.isfile(p):
        s = open(p, encoding="utf-8", errors="replace").read()
        print("自检 db1292052.html: 注入=%s | href_search=%s | 无服务器地址=%s" % (
            "offline-snapshot: play" in s,
            'href="haiplay://search?q=' in s,
            "10.0.0.11" not in s))


if __name__ == "__main__":
    main()
