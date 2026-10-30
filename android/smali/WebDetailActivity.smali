.class public Lcom/fongmi/android/tv/ui/activity/WebDetailActivity;
.super Landroid/app/Activity;
.source "WebDetailActivity.java"


# instance fields
.field private a:Landroid/webkit/WebView;

.field private b:Ljava/lang/String;

.field private c:Ljava/lang/String;

.field private d:Ljava/lang/String;

.field private e:Ljava/lang/String;


# direct methods
.method public constructor <init>()V
    .locals 0

    invoke-direct {p0}, Landroid/app/Activity;-><init>()V

    return-void
.end method

.method public static start(Landroid/app/Activity;Ljava/lang/String;Ljava/lang/String;Ljava/lang/String;Ljava/lang/String;)V
    .locals 2

    new-instance v0, Landroid/content/Intent;

    const-class v1, Lcom/fongmi/android/tv/ui/activity/WebDetailActivity;

    invoke-direct {v0, p0, v1}, Landroid/content/Intent;-><init>(Landroid/content/Context;Ljava/lang/Class;)V

    const-string v1, "key"

    invoke-virtual {v0, v1, p1}, Landroid/content/Intent;->putExtra(Ljava/lang/String;Ljava/lang/String;)Landroid/content/Intent;

    const-string v1, "id"

    invoke-virtual {v0, v1, p2}, Landroid/content/Intent;->putExtra(Ljava/lang/String;Ljava/lang/String;)Landroid/content/Intent;

    const-string v1, "name"

    invoke-virtual {v0, v1, p3}, Landroid/content/Intent;->putExtra(Ljava/lang/String;Ljava/lang/String;)Landroid/content/Intent;

    const-string v1, "pic"

    invoke-virtual {v0, v1, p4}, Landroid/content/Intent;->putExtra(Ljava/lang/String;Ljava/lang/String;)Landroid/content/Intent;

    invoke-virtual {p0, v0}, Landroid/app/Activity;->startActivity(Landroid/content/Intent;)V

    return-void
.end method


# virtual methods
# 点「▶ 播放」= 原版列表页点海报，一字不差。
#
# 这里**不能**自己拼 VideoActivity 的 Intent —— 原版真正的入口是 9 参静态方法
# VideoActivity.OoOoO0O0oOoO0O0O(Activity,String,String,String,String,String,ZZZ)，
# 它在 intent 里除了 key/id/name/pic 还要塞 mark(null) / cast(false) / collect(false)，
# 走 startActivityForResult(0x3e8)。少这几个 extra，播放器里的「网盘」「弹幕」
# 设置面板会打不开（实拍反馈），点播放也不再走 PG 的默认播放链路。
# 所以：只做参数搬运，其余全部交给原版方法自己处理。
.method public final a(Ljava/lang/String;)V
    .locals 9

    # 海哥影视（2026-10-30 分流定稿）：
    #   haiplay://play?site=<key>&id=<vodId>&name=&pic= → 跳过搜索页，直达该站源
    #     的 PG 原版选集页（9 参入口，extra 语义与原版点海报完全一致）。
    #   haiplay://search?q=片名                        → 原版聚合搜索。
    invoke-static {p1}, Landroid/net/Uri;->parse(Ljava/lang/String;)Landroid/net/Uri;
    move-result-object v0
    const-string v1, "site"
    invoke-virtual {v0, v1}, Landroid/net/Uri;->getQueryParameter(Ljava/lang/String;)Ljava/lang/String;
    move-result-object v1
    if-eqz v1, :cond_search

    # ---- 直达站源选集页 ----
    const-string v2, "id"
    invoke-virtual {v0, v2}, Landroid/net/Uri;->getQueryParameter(Ljava/lang/String;)Ljava/lang/String;
    move-result-object v2
    if-eqz v2, :cond_search

    const-string v3, "name"
    invoke-virtual {v0, v3}, Landroid/net/Uri;->getQueryParameter(Ljava/lang/String;)Ljava/lang/String;
    move-result-object v3
    if-nez v3, :cond_name_ok
    iget-object v3, p0, Lcom/fongmi/android/tv/ui/activity/WebDetailActivity;->d:Ljava/lang/String;
    :cond_name_ok

    const-string v4, "pic"
    invoke-virtual {v0, v4}, Landroid/net/Uri;->getQueryParameter(Ljava/lang/String;)Ljava/lang/String;
    move-result-object v4
    if-nez v4, :cond_pic_ok
    iget-object v4, p0, Lcom/fongmi/android/tv/ui/activity/WebDetailActivity;->e:Ljava/lang/String;
    :cond_pic_ok

    move-object v0, p0
    const/4 v5, 0x0
    const/4 v6, 0x0
    const/4 v7, 0x0
    const/4 v8, 0x0
    invoke-static/range {v0 .. v8}, Lcom/fongmi/android/tv/ui/activity/VideoActivity;->OoOoO0O0oOoO0O0O(Landroid/app/Activity;Ljava/lang/String;Ljava/lang/String;Ljava/lang/String;Ljava/lang/String;Ljava/lang/String;ZZZ)V
    return-void

    :cond_search
    # ---- 聚合搜索：SearchActivity.onResume 预填后调原版搜索执行体 ----
    const-string v1, "q"
    invoke-virtual {v0, v1}, Landroid/net/Uri;->getQueryParameter(Ljava/lang/String;)Ljava/lang/String;
    move-result-object v0
    if-eqz v0, :cond_search_skip
    new-instance v1, Landroid/content/Intent;
    const-class v2, Lcom/fongmi/android/tv/ui/activity/SearchActivity;
    invoke-direct {v1, p0, v2}, Landroid/content/Intent;-><init>(Landroid/content/Context;Ljava/lang/Class;)V
    const-string v2, "keyword"
    invoke-virtual {v1, v2, v0}, Landroid/content/Intent;->putExtra(Ljava/lang/String;Ljava/lang/String;)Landroid/content/Intent;
    invoke-virtual {p0, v1}, Landroid/app/Activity;->startActivity(Landroid/content/Intent;)V
    return-void
    :cond_search_skip

    # 兜底：既无 site 也无 q —— 按当前页面自带的 key/id 进原版选集页（历史行为）。
    move-object v0, p0

    iget-object v1, p0, Lcom/fongmi/android/tv/ui/activity/WebDetailActivity;->b:Ljava/lang/String;

    iget-object v2, p0, Lcom/fongmi/android/tv/ui/activity/WebDetailActivity;->c:Ljava/lang/String;

    iget-object v3, p0, Lcom/fongmi/android/tv/ui/activity/WebDetailActivity;->d:Ljava/lang/String;

    iget-object v4, p0, Lcom/fongmi/android/tv/ui/activity/WebDetailActivity;->e:Ljava/lang/String;

    const/4 v5, 0x0

    const/4 v6, 0x0

    const/4 v7, 0x0

    const/4 v8, 0x0

    invoke-static/range {v0 .. v8}, Lcom/fongmi/android/tv/ui/activity/VideoActivity;->OoOoO0O0oOoO0O0O(Landroid/app/Activity;Ljava/lang/String;Ljava/lang/String;Ljava/lang/String;Ljava/lang/String;Ljava/lang/String;ZZZ)V

    return-void
.end method

.method protected onCreate(Landroid/os/Bundle;)V
    .locals 5

    invoke-super {p0, p1}, Landroid/app/Activity;->onCreate(Landroid/os/Bundle;)V

    invoke-virtual {p0}, Landroid/app/Activity;->getIntent()Landroid/content/Intent;

    move-result-object v0

    const-string v1, "key"

    invoke-virtual {v0, v1}, Landroid/content/Intent;->getStringExtra(Ljava/lang/String;)Ljava/lang/String;

    move-result-object v1

    iput-object v1, p0, Lcom/fongmi/android/tv/ui/activity/WebDetailActivity;->b:Ljava/lang/String;

    const-string v1, "id"

    invoke-virtual {v0, v1}, Landroid/content/Intent;->getStringExtra(Ljava/lang/String;)Ljava/lang/String;

    move-result-object v1

    iput-object v1, p0, Lcom/fongmi/android/tv/ui/activity/WebDetailActivity;->c:Ljava/lang/String;

    const-string v1, "name"

    invoke-virtual {v0, v1}, Landroid/content/Intent;->getStringExtra(Ljava/lang/String;)Ljava/lang/String;

    move-result-object v1

    iput-object v1, p0, Lcom/fongmi/android/tv/ui/activity/WebDetailActivity;->d:Ljava/lang/String;

    const-string v1, "pic"

    invoke-virtual {v0, v1}, Landroid/content/Intent;->getStringExtra(Ljava/lang/String;)Ljava/lang/String;

    move-result-object v1

    iput-object v1, p0, Lcom/fongmi/android/tv/ui/activity/WebDetailActivity;->e:Ljava/lang/String;

    new-instance v0, Landroid/webkit/WebView;

    invoke-direct {v0, p0}, Landroid/webkit/WebView;-><init>(Landroid/content/Context;)V

    iput-object v0, p0, Lcom/fongmi/android/tv/ui/activity/WebDetailActivity;->a:Landroid/webkit/WebView;

    invoke-virtual {v0}, Landroid/webkit/WebView;->getSettings()Landroid/webkit/WebSettings;

    move-result-object v1

    const/4 v2, 0x1

    invoke-virtual {v1, v2}, Landroid/webkit/WebSettings;->setJavaScriptEnabled(Z)V

    invoke-virtual {v1, v2}, Landroid/webkit/WebSettings;->setDomStorageEnabled(Z)V

    invoke-virtual {v1, v2}, Landroid/webkit/WebSettings;->setUseWideViewPort(Z)V

    invoke-virtual {v1, v2}, Landroid/webkit/WebSettings;->setLoadWithOverviewMode(Z)V

    const/4 v3, 0x2

    invoke-virtual {v1, v3}, Landroid/webkit/WebSettings;->setCacheMode(I)V

    const v3, -0x1000000

    invoke-virtual {v0, v3}, Landroid/webkit/WebView;->setBackgroundColor(I)V

    const/4 v3, 0x0

    invoke-virtual {v0, v3}, Landroid/webkit/WebView;->setVerticalScrollBarEnabled(Z)V

    invoke-virtual {v0, v3}, Landroid/webkit/WebView;->setHorizontalScrollBarEnabled(Z)V

    new-instance v1, Lcom/fongmi/android/tv/ui/activity/WebDetailActivity$1;

    invoke-direct {v1, p0}, Lcom/fongmi/android/tv/ui/activity/WebDetailActivity$1;-><init>(Lcom/fongmi/android/tv/ui/activity/WebDetailActivity;)V

    invoke-virtual {v0, v1}, Landroid/webkit/WebView;->setWebViewClient(Landroid/webkit/WebViewClient;)V

    invoke-virtual {p0, v0}, Landroid/app/Activity;->setContentView(Landroid/view/View;)V

    invoke-virtual {v0}, Landroid/webkit/WebView;->requestFocus()Z

    new-instance v1, Ljava/lang/StringBuilder;

    invoke-direct {v1}, Ljava/lang/StringBuilder;-><init>()V

    const-string v2, "http://10.0.0.11:8099/hai/poster/d/"

    invoke-virtual {v1, v2}, Ljava/lang/StringBuilder;->append(Ljava/lang/String;)Ljava/lang/StringBuilder;

    iget-object v2, p0, Lcom/fongmi/android/tv/ui/activity/WebDetailActivity;->c:Ljava/lang/String;

    invoke-virtual {v1, v2}, Ljava/lang/StringBuilder;->append(Ljava/lang/String;)Ljava/lang/StringBuilder;

    invoke-virtual {v1}, Ljava/lang/StringBuilder;->toString()Ljava/lang/String;

    move-result-object v1

    invoke-virtual {v0, v1}, Landroid/webkit/WebView;->loadUrl(Ljava/lang/String;)V

    return-void
.end method

.method protected onDestroy()V
    .locals 1

    iget-object v0, p0, Lcom/fongmi/android/tv/ui/activity/WebDetailActivity;->a:Landroid/webkit/WebView;

    if-eqz v0, :cond_0

    invoke-virtual {v0}, Landroid/webkit/WebView;->destroy()V

    :cond_0
    invoke-super {p0}, Landroid/app/Activity;->onDestroy()V

    return-void
.end method

.method public onKeyDown(ILandroid/view/KeyEvent;)Z
    .locals 1

    const/4 v0, 0x4

    if-ne p1, v0, :cond_0

    iget-object v0, p0, Lcom/fongmi/android/tv/ui/activity/WebDetailActivity;->a:Landroid/webkit/WebView;

    invoke-virtual {v0}, Landroid/webkit/WebView;->canGoBack()Z

    move-result v0

    if-eqz v0, :cond_0

    iget-object v0, p0, Lcom/fongmi/android/tv/ui/activity/WebDetailActivity;->a:Landroid/webkit/WebView;

    invoke-virtual {v0}, Landroid/webkit/WebView;->goBack()V

    const/4 v0, 0x1

    return v0

    :cond_0
    invoke-super {p0, p1, p2}, Landroid/app/Activity;->onKeyDown(ILandroid/view/KeyEvent;)Z

    move-result v0

    return v0
.end method
