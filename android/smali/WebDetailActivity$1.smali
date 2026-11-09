.class final Lcom/fongmi/android/tv/ui/activity/WebDetailActivity$1;
.super Landroid/webkit/WebViewClient;
.source "WebDetailActivity.java"


# instance fields
.field final synthetic a:Lcom/fongmi/android/tv/ui/activity/WebDetailActivity;

# 2026-11-09：本地墙页服务(127.0.0.1:10090)偶发晚于 WebView 首帧加载，
# 旧逻辑加载失败即永久黑屏。这里记重试次数，配合 $2 做延迟 reload 自愈。
.field private b:I


# direct methods
.method constructor <init>(Lcom/fongmi/android/tv/ui/activity/WebDetailActivity;)V
    .locals 0

    iput-object p1, p0, Lcom/fongmi/android/tv/ui/activity/WebDetailActivity$1;->a:Lcom/fongmi/android/tv/ui/activity/WebDetailActivity;

    invoke-direct {p0}, Landroid/webkit/WebViewClient;-><init>()V

    return-void
.end method


# virtual methods
.method public shouldOverrideUrlLoading(Landroid/webkit/WebView;Landroid/webkit/WebResourceRequest;)Z
    .locals 2

    invoke-interface {p2}, Landroid/webkit/WebResourceRequest;->getUrl()Landroid/net/Uri;

    move-result-object v0

    invoke-virtual {v0}, Landroid/net/Uri;->toString()Ljava/lang/String;

    move-result-object v0

    invoke-direct {p0, v0}, Lcom/fongmi/android/tv/ui/activity/WebDetailActivity$1;->a(Ljava/lang/String;)Z

    move-result v0

    return v0
.end method

.method public shouldOverrideUrlLoading(Landroid/webkit/WebView;Ljava/lang/String;)Z
    .locals 1

    invoke-direct {p0, p2}, Lcom/fongmi/android/tv/ui/activity/WebDetailActivity$1;->a(Ljava/lang/String;)Z

    move-result v0

    return v0
.end method

.method private a(Ljava/lang/String;)Z
    .locals 2

    invoke-static {p1}, Landroid/net/Uri;->parse(Ljava/lang/String;)Landroid/net/Uri;

    move-result-object v0

    invoke-virtual {v0}, Landroid/net/Uri;->getScheme()Ljava/lang/String;

    move-result-object v0

    if-nez v0, :cond_0

    const/4 v0, 0x0

    return v0

    :cond_0
    const-string v1, "haiplay"

    invoke-virtual {v1, v0}, Ljava/lang/String;->equalsIgnoreCase(Ljava/lang/String;)Z

    move-result v0

    if-eqz v0, :cond_1

    iget-object v0, p0, Lcom/fongmi/android/tv/ui/activity/WebDetailActivity$1;->a:Lcom/fongmi/android/tv/ui/activity/WebDetailActivity;

    invoke-virtual {v0, p1}, Lcom/fongmi/android/tv/ui/activity/WebDetailActivity;->a(Ljava/lang/String;)V

    const/4 v0, 0x1

    return v0

    :cond_1
    const/4 v0, 0x0

    return v0
.end method

# 2026-11-09 自愈：只对「本地墙页服务 10090」的主框架加载失败做延迟重试。
# WebViewClient 新签名(API23+)默认实现会转发到这个旧签名，故只需覆写一处。
# 最多 90 次 × 1s，覆盖 HaiBridge 的 120s jar 轮询窗口。
.method public onReceivedError(Landroid/webkit/WebView;ILjava/lang/String;Ljava/lang/String;)V
    .locals 4

    if-nez p4, :cond_skip

    const-string v0, "127.0.0.1:10090"

    invoke-virtual {p4, v0}, Ljava/lang/String;->contains(Ljava/lang/CharSequence;)Z

    move-result v0

    if-eqz v0, :cond_skip

    iget v0, p0, Lcom/fongmi/android/tv/ui/activity/WebDetailActivity$1;->b:I

    const/16 v1, 0x5a

    if-ge v0, v1, :cond_skip

    add-int/lit8 v0, v0, 0x1

    iput v0, p0, Lcom/fongmi/android/tv/ui/activity/WebDetailActivity$1;->b:I

    new-instance v0, Lcom/fongmi/android/tv/ui/activity/WebDetailActivity$2;

    invoke-direct {v0, p1}, Lcom/fongmi/android/tv/ui/activity/WebDetailActivity$2;-><init>(Landroid/webkit/WebView;)V

    const-wide/16 v1, 0x3e8

    invoke-virtual {p1, v0, v1, v2}, Landroid/view/View;->postDelayed(Ljava/lang/Runnable;J)Z

    :cond_skip
    return-void
.end method
