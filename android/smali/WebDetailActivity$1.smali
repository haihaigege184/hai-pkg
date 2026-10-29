.class final Lcom/fongmi/android/tv/ui/activity/WebDetailActivity$1;
.super Landroid/webkit/WebViewClient;
.source "WebDetailActivity.java"


# instance fields
.field final synthetic a:Lcom/fongmi/android/tv/ui/activity/WebDetailActivity;


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
