.class final Lcom/fongmi/android/tv/ui/activity/WebDetailActivity$2;
.super Ljava/lang/Object;
.implements Ljava/lang/Runnable;
.source "WebDetailActivity.java"


# instance fields
.field private final a:Landroid/webkit/WebView;


# direct methods
.method constructor <init>(Landroid/webkit/WebView;)V
    .locals 1

    invoke-direct {p0}, Ljava/lang/Object;-><init>()V

    iput-object p1, p0, Lcom/fongmi/android/tv/ui/activity/WebDetailActivity$2;->a:Landroid/webkit/WebView;

    return-void
.end method


# virtual methods
.method public run()V
    .locals 2

    :try_start_0
    iget-object v0, p0, Lcom/fongmi/android/tv/ui/activity/WebDetailActivity$2;->a:Landroid/webkit/WebView;

    invoke-virtual {v0}, Landroid/webkit/WebView;->reload()V
    :try_end_0
    .catch Ljava/lang/Throwable; {:try_start_0 .. :try_end_0} :catch_0

    return-void

    :catch_0
    move-exception v0

    return-void
.end method
