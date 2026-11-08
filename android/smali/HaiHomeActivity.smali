.class public Lcom/fongmi/android/tv/ui/activity/HaiHomeActivity;
.super Landroid/app/Activity;
.source "HaiHomeActivity.java"


# direct methods
.method public constructor <init>()V
    .locals 0

    invoke-direct {p0}, Landroid/app/Activity;-><init>()V

    return-void
.end method


# virtual methods
.method protected onCreate(Landroid/os/Bundle;)V
    .locals 5

    invoke-super {p0, p1}, Landroid/app/Activity;->onCreate(Landroid/os/Bundle;)V

    # 海哥影视：Launcher 入口。先把原版 HomeActivity 压进栈底（返回键可回原生点播页），
    # 再叠一层 TMDBWall 海报墙，随后自己 finish，让墙成为首屏。
    new-instance v0, Landroid/content/Intent;

    const-class v1, Lcom/fongmi/android/tv/ui/activity/HomeActivity;

    invoke-direct {v0, p0, v1}, Landroid/content/Intent;-><init>(Landroid/content/Context;Ljava/lang/Class;)V

    invoke-virtual {p0, v0}, Landroid/app/Activity;->startActivity(Landroid/content/Intent;)V

    # 叠 TMDBWall 海报墙（WebDetailActivity.start(Activity, key, id, name, pic)）
    const-string v1, "TMDBWall"

    const-string v2, ""

    const-string v3, ""

    const-string v4, ""

    invoke-static {p0, v1, v2, v3, v4}, Lcom/fongmi/android/tv/ui/activity/WebDetailActivity;->start(Landroid/app/Activity;Ljava/lang/String;Ljava/lang/String;Ljava/lang/String;Ljava/lang/String;)V

    invoke-virtual {p0}, Landroid/app/Activity;->finish()V

    return-void
.end method
