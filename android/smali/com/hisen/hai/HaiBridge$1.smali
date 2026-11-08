.class Lcom/hisen/hai/HaiBridge$1;
.super Ljava/lang/Object;
.source "HaiBridge.java"

# interfaces
.implements Ljava/lang/Runnable;


# annotations
.annotation system Ldalvik/annotation/EnclosingMethod;
    value = Lcom/hisen/hai/HaiBridge;->start(Landroid/content/Context;)V
.end annotation

.annotation system Ldalvik/annotation/InnerClass;
    accessFlags = 0x0
    name = null
.end annotation


# instance fields
.field final synthetic val$appCtx:Landroid/content/Context;


# direct methods
.method constructor <init>(Landroid/content/Context;)V
    .locals 0
    .annotation system Ldalvik/annotation/Signature;
        value = {
            "()V"
        }
    .end annotation

    .line 49
    iput-object p1, p0, Lcom/hisen/hai/HaiBridge$1;->val$appCtx:Landroid/content/Context;

    invoke-direct {p0}, Ljava/lang/Object;-><init>()V

    return-void
.end method


# virtual methods
.method public run()V
    .locals 3

    .line 51
    const/4 v0, 0x0

    :goto_0
    const/16 v1, 0x3c

    if-ge v0, v1, :cond_2

    .line 52
    const-wide/16 v1, 0x7d0

    :try_start_0
    invoke-static {v1, v2}, Ljava/lang/Thread;->sleep(J)V
    :try_end_0
    .catch Ljava/lang/InterruptedException; {:try_start_0 .. :try_end_0} :catch_0

    .line 53
    invoke-static {}, Lcom/hisen/hai/HaiBridge;->access$000()Z

    move-result v1

    if-eqz v1, :cond_0

    return-void

    .line 54
    :cond_0
    iget-object v1, p0, Lcom/hisen/hai/HaiBridge$1;->val$appCtx:Landroid/content/Context;

    invoke-static {v1}, Lcom/hisen/hai/HaiBridge;->access$100(Landroid/content/Context;)Z

    move-result v1

    if-eqz v1, :cond_1

    return-void

    .line 51
    :cond_1
    add-int/lit8 v0, v0, 0x1

    goto :goto_0

    .line 52
    :catch_0
    move-exception v0

    return-void

    .line 56
    :cond_2
    const-string v0, "HaiBridge"

    const-string v1, "\u8f6e\u8be2 60 \u6b21\u4ecd\u672a\u627e\u5230 pg.jar\uff0c\u653e\u5f03\uff08\u4fdd\u7559\u539f\u751f\u515c\u5e95\uff09"

    invoke-static {v0, v1}, Landroid/util/Log;->i(Ljava/lang/String;Ljava/lang/String;)I

    .line 57
    return-void
.end method
