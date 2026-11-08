.class public final Lcom/hisen/hai/HaiBridge;
.super Ljava/lang/Object;
.source "HaiBridge.java"


# static fields
.field private static final HAI_CORE:Ljava/lang/String; = "com.hisen.hai.HaiCore"

.field private static final POLL_MAX:I = 0x3c

.field private static final POLL_MS:J = 0x7d0L

.field private static final TAG:Ljava/lang/String; = "HaiBridge"

.field private static volatile sStarted:Z

.field private static volatile sTried:Z


# direct methods
.method private constructor <init>()V
    .locals 0

    .line 39
    invoke-direct {p0}, Ljava/lang/Object;-><init>()V

    return-void
.end method

.method static synthetic access$000()Z
    .locals 1

    .line 30
    sget-boolean v0, Lcom/hisen/hai/HaiBridge;->sStarted:Z

    return v0
.end method

.method static synthetic access$100(Landroid/content/Context;)Z
    .locals 0

    .line 30
    invoke-static {p0}, Lcom/hisen/hai/HaiBridge;->tryStart(Landroid/content/Context;)Z

    move-result p0

    return p0
.end method

.method private static findJar(Landroid/content/Context;)Ljava/io/File;
    .locals 6

    .line 94
    invoke-virtual {p0}, Landroid/content/Context;->getFilesDir()Ljava/io/File;

    move-result-object v0

    .line 95
    const-string v1, "spider.jar"

    const-string v2, "ok.jar"

    const-string v3, "pg.jar"

    filled-new-array {v3, v1, v2}, [Ljava/lang/String;

    move-result-object v1

    .line 96
    const/4 v2, 0x0

    const/4 v3, 0x0

    :goto_0
    const/4 v4, 0x3

    if-ge v3, v4, :cond_1

    aget-object v4, v1, v3

    .line 97
    new-instance v5, Ljava/io/File;

    invoke-direct {v5, v0, v4}, Ljava/io/File;-><init>(Ljava/io/File;Ljava/lang/String;)V

    .line 98
    invoke-virtual {v5}, Ljava/io/File;->exists()Z

    move-result v4

    if-eqz v4, :cond_0

    return-object v5

    .line 96
    :cond_0
    add-int/lit8 v3, v3, 0x1

    goto :goto_0

    .line 100
    :cond_1
    invoke-virtual {v0}, Ljava/io/File;->listFiles()[Ljava/io/File;

    move-result-object v0

    .line 101
    if-eqz v0, :cond_3

    .line 102
    array-length v1, v0

    :goto_1
    if-ge v2, v1, :cond_3

    aget-object v3, v0, v2

    .line 103
    invoke-virtual {v3}, Ljava/io/File;->isFile()Z

    move-result v4

    if-eqz v4, :cond_2

    invoke-virtual {v3}, Ljava/io/File;->getName()Ljava/lang/String;

    move-result-object v4

    const-string v5, ".jar"

    invoke-virtual {v4, v5}, Ljava/lang/String;->endsWith(Ljava/lang/String;)Z

    move-result v4

    if-eqz v4, :cond_2

    return-object v3

    .line 102
    :cond_2
    add-int/lit8 v2, v2, 0x1

    goto :goto_1

    .line 107
    :cond_3
    new-instance v0, Ljava/io/File;

    new-instance v1, Ljava/lang/StringBuilder;

    invoke-direct {v1}, Ljava/lang/StringBuilder;-><init>()V

    const-string v2, "/data/data/"

    invoke-virtual {v1, v2}, Ljava/lang/StringBuilder;->append(Ljava/lang/String;)Ljava/lang/StringBuilder;

    move-result-object v1

    invoke-virtual {p0}, Landroid/content/Context;->getPackageName()Ljava/lang/String;

    move-result-object p0

    invoke-virtual {v1, p0}, Ljava/lang/StringBuilder;->append(Ljava/lang/String;)Ljava/lang/StringBuilder;

    move-result-object p0

    const-string v1, "/files/pg.jar"

    invoke-virtual {p0, v1}, Ljava/lang/StringBuilder;->append(Ljava/lang/String;)Ljava/lang/StringBuilder;

    move-result-object p0

    invoke-virtual {p0}, Ljava/lang/StringBuilder;->toString()Ljava/lang/String;

    move-result-object p0

    invoke-direct {v0, p0}, Ljava/io/File;-><init>(Ljava/lang/String;)V

    .line 108
    invoke-virtual {v0}, Ljava/io/File;->exists()Z

    move-result p0

    if-eqz p0, :cond_4

    goto :goto_2

    :cond_4
    const/4 v0, 0x0

    :goto_2
    return-object v0
.end method

.method private static safeOptDir(Landroid/content/Context;)Ljava/io/File;
    .locals 1

    .line 87
    :try_start_0
    invoke-virtual {p0}, Landroid/content/Context;->getCodeCacheDir()Ljava/io/File;

    move-result-object p0
    :try_end_0
    .catchall {:try_start_0 .. :try_end_0} :catchall_0

    return-object p0

    :catchall_0
    move-exception v0

    .line 88
    :try_start_1
    invoke-virtual {p0}, Landroid/content/Context;->getCacheDir()Ljava/io/File;

    move-result-object p0
    :try_end_1
    .catchall {:try_start_1 .. :try_end_1} :catchall_1

    return-object p0

    :catchall_1
    move-exception v0

    .line 89
    invoke-virtual {p0}, Landroid/content/Context;->getFilesDir()Ljava/io/File;

    move-result-object p0

    return-object p0
.end method

.method public static start(Landroid/content/Context;)V
    .locals 3

    .line 42
    sget-boolean v0, Lcom/hisen/hai/HaiBridge;->sStarted:Z

    if-nez v0, :cond_3

    sget-boolean v0, Lcom/hisen/hai/HaiBridge;->sTried:Z

    if-eqz v0, :cond_0

    goto :goto_0

    .line 43
    :cond_0
    const/4 v0, 0x1

    sput-boolean v0, Lcom/hisen/hai/HaiBridge;->sTried:Z

    .line 44
    if-nez p0, :cond_1

    return-void

    .line 46
    :cond_1
    invoke-static {p0}, Lcom/hisen/hai/HaiBridge;->tryStart(Landroid/content/Context;)Z

    move-result v1

    if-eqz v1, :cond_2

    return-void

    .line 48
    :cond_2
    invoke-virtual {p0}, Landroid/content/Context;->getApplicationContext()Landroid/content/Context;

    move-result-object p0

    .line 49
    new-instance v1, Ljava/lang/Thread;

    new-instance v2, Lcom/hisen/hai/HaiBridge$1;

    invoke-direct {v2, p0}, Lcom/hisen/hai/HaiBridge$1;-><init>(Landroid/content/Context;)V

    const-string p0, "hai-bridge-poll"

    invoke-direct {v1, v2, p0}, Ljava/lang/Thread;-><init>(Ljava/lang/Runnable;Ljava/lang/String;)V

    .line 59
    invoke-virtual {v1, v0}, Ljava/lang/Thread;->setDaemon(Z)V

    .line 60
    invoke-virtual {v1}, Ljava/lang/Thread;->start()V

    .line 61
    return-void

    .line 42
    :cond_3
    :goto_0
    return-void
.end method

.method private static tryStart(Landroid/content/Context;)Z
    .locals 9

    .line 65
    const-string v0, "HaiBridge"

    sget-boolean v1, Lcom/hisen/hai/HaiBridge;->sStarted:Z

    const/4 v2, 0x1

    if-eqz v1, :cond_0

    return v2

    .line 67
    :cond_0
    const/4 v1, 0x0

    :try_start_0
    invoke-static {p0}, Lcom/hisen/hai/HaiBridge;->findJar(Landroid/content/Context;)Ljava/io/File;

    move-result-object v3

    .line 68
    if-eqz v3, :cond_2

    invoke-virtual {v3}, Ljava/io/File;->exists()Z

    move-result v4

    if-nez v4, :cond_1

    goto :goto_0

    .line 69
    :cond_1
    invoke-static {p0}, Lcom/hisen/hai/HaiBridge;->safeOptDir(Landroid/content/Context;)Ljava/io/File;

    move-result-object v4

    .line 70
    new-instance v5, Ldalvik/system/DexClassLoader;

    .line 71
    invoke-virtual {v3}, Ljava/io/File;->getAbsolutePath()Ljava/lang/String;

    move-result-object v6

    invoke-virtual {v4}, Ljava/io/File;->getAbsolutePath()Ljava/lang/String;

    move-result-object v4

    .line 72
    invoke-virtual {p0}, Landroid/content/Context;->getClassLoader()Ljava/lang/ClassLoader;

    move-result-object v7

    const/4 v8, 0x0

    invoke-direct {v5, v6, v4, v8, v7}, Ldalvik/system/DexClassLoader;-><init>(Ljava/lang/String;Ljava/lang/String;Ljava/lang/String;Ljava/lang/ClassLoader;)V

    .line 73
    const-string v4, "com.hisen.hai.HaiCore"

    invoke-virtual {v5, v4}, Ldalvik/system/DexClassLoader;->loadClass(Ljava/lang/String;)Ljava/lang/Class;

    move-result-object v4

    .line 74
    const-string v5, "start"

    new-array v6, v2, [Ljava/lang/Class;

    const-class v7, Landroid/content/Context;

    aput-object v7, v6, v1

    invoke-virtual {v4, v5, v6}, Ljava/lang/Class;->getMethod(Ljava/lang/String;[Ljava/lang/Class;)Ljava/lang/reflect/Method;

    move-result-object v4

    .line 75
    new-array v5, v2, [Ljava/lang/Object;

    aput-object p0, v5, v1

    invoke-virtual {v4, v8, v5}, Ljava/lang/reflect/Method;->invoke(Ljava/lang/Object;[Ljava/lang/Object;)Ljava/lang/Object;

    .line 76
    sput-boolean v2, Lcom/hisen/hai/HaiBridge;->sStarted:Z

    .line 77
    new-instance p0, Ljava/lang/StringBuilder;

    invoke-direct {p0}, Ljava/lang/StringBuilder;-><init>()V

    const-string v4, "HaiCore.start \u5df2\u8c03\u7528  jar="

    invoke-virtual {p0, v4}, Ljava/lang/StringBuilder;->append(Ljava/lang/String;)Ljava/lang/StringBuilder;

    move-result-object p0

    invoke-virtual {v3}, Ljava/io/File;->getAbsolutePath()Ljava/lang/String;

    move-result-object v3

    invoke-virtual {p0, v3}, Ljava/lang/StringBuilder;->append(Ljava/lang/String;)Ljava/lang/StringBuilder;

    move-result-object p0

    invoke-virtual {p0}, Ljava/lang/StringBuilder;->toString()Ljava/lang/String;

    move-result-object p0

    invoke-static {v0, p0}, Landroid/util/Log;->i(Ljava/lang/String;Ljava/lang/String;)I
    :try_end_0
    .catchall {:try_start_0 .. :try_end_0} :catchall_0

    .line 78
    return v2

    .line 68
    :cond_2
    :goto_0
    return v1

    .line 79
    :catchall_0
    move-exception p0

    .line 80
    :try_start_1
    new-instance v2, Ljava/lang/StringBuilder;

    invoke-direct {v2}, Ljava/lang/StringBuilder;-><init>()V

    const-string v3, "HaiBridge \u542f\u52a8\u5931\u8d25\uff08\u5df2\u9759\u9ed8\u964d\u7ea7\uff09: "

    invoke-virtual {v2, v3}, Ljava/lang/StringBuilder;->append(Ljava/lang/String;)Ljava/lang/StringBuilder;

    move-result-object v2

    invoke-virtual {v2, p0}, Ljava/lang/StringBuilder;->append(Ljava/lang/Object;)Ljava/lang/StringBuilder;

    move-result-object p0

    invoke-virtual {p0}, Ljava/lang/StringBuilder;->toString()Ljava/lang/String;

    move-result-object p0

    invoke-static {v0, p0}, Landroid/util/Log;->w(Ljava/lang/String;Ljava/lang/String;)I
    :try_end_1
    .catchall {:try_start_1 .. :try_end_1} :catchall_1

    goto :goto_1

    :catchall_1
    move-exception p0

    .line 81
    :goto_1
    return v1
.end method
