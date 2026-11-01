.class public Lcom/fongmi/android/tv/ui/activity/HaiAutoPlay;
.super Ljava/lang/Object;
.implements Ljava/lang/Runnable;

# 海哥影视（2026-11-01）：文件列表自动起播。
#
# 背景：详情页点了「▶ 播放」或「第 N 集」之后，app 会打开网盘文件列表
# （VideoActivity + push_agent 站点）。那是整个播放链里唯一 unavoidable 的
# 一层 —— 网盘转存和取直链全在 app/jar 内部，外部拿不到终址。所以「点完
# 就播」只能靠这里：数据到了之后自己点一下第 N 项。
#
# 为什么要连点 3 次（每 1.5 秒）：列表是异步拉的，第一次 post 时 adapter
# 常常还是空的，`setSelectedPosition` 会被 Leanback 直接忽略（不报错、也
# 不产生任何回调）。连点三次覆盖「快/慢」两种网络，点到之后播放器全屏盖
# 上来，后面的点击自然落在播放器上也就不再有效果。


.field private a:Lcom/fongmi/android/tv/ui/activity/VideoActivity;

.field private b:I

.field private c:I


.method public constructor <init>(Lcom/fongmi/android/tv/ui/activity/VideoActivity;I)V
    .locals 1

    invoke-direct {p0}, Ljava/lang/Object;-><init>()V

    iput-object p1, p0, Lcom/fongmi/android/tv/ui/activity/HaiAutoPlay;->a:Lcom/fongmi/android/tv/ui/activity/VideoActivity;

    iput p2, p0, Lcom/fongmi/android/tv/ui/activity/HaiAutoPlay;->b:I

    const/4 v0, 0x0

    iput v0, p0, Lcom/fongmi/android/tv/ui/activity/HaiAutoPlay;->c:I

    return-void
.end method


.method public run()V
    .locals 8

    iget-object v0, p0, Lcom/fongmi/android/tv/ui/activity/HaiAutoPlay;->a:Lcom/fongmi/android/tv/ui/activity/VideoActivity;

    if-nez v0, :cond_0

    return-void

    :cond_0
    # 留一条日志（2026-11-01）：真机上「到底有没有走到这里」一直是靠猜的。
    # adb logcat -s HaiAutoPlay 直接看。
    const-string v5, "run tick"

    invoke-static {v5, v5}, Landroid/util/Log;->e(Ljava/lang/String;Ljava/lang/String;)I

    iget v1, p0, Lcom/fongmi/android/tv/ui/activity/HaiAutoPlay;->b:I

    iget v2, p0, Lcom/fongmi/android/tv/ui/activity/HaiAutoPlay;->c:I

    # 4 次 × 2 秒 ≈ 8 秒窗口（首发 5 秒后）。不要贪多：起来之后再按
    # CENTER 会把播放器控制条反复呼出/收起，反而晃眼。
    # 网盘列表（或者中枢算直链）慢一点就整段错过，表现就是「停在选集页」。
    const/4 v3, 0x4

    if-lt v2, v3, :cond_1

    return-void

    :cond_1
    # 两个拦截标志（2026-11-01 反编译实锤）：
    #   oOo0oO0oO0o0OoOo(I) 一进来先 setSelectedPosition，然后
    #   `if (OoOoOo0O0Oo0O0Oo) return;  if (OoOo0o0OoO0oO0O0) return;`
    #   两个 public boolean 任一为真就直接返回，**不点也不报错**。
    const/4 v5, 0x0

    iput-boolean v5, v0, Lcom/fongmi/android/tv/ui/activity/VideoActivity;->OoOoOo0O0Oo0O0Oo:Z

    iput-boolean v5, v0, Lcom/fongmi/android/tv/ui/activity/VideoActivity;->OoOo0o0OoO0oO0O0:Z

    invoke-virtual {v0, v1}, Lcom/fongmi/android/tv/ui/activity/VideoActivity;->oOo0oO0oO0o0OoOo(I)V

    # 上面那步只是「选中第 N 项」；真正触发播放得靠一次确定键。
    # 2026-11-01 实测：上面调完（哪怕清零了两个标志）画面纹丝不动，
    # 而人手按遥控器「确定」立刻起播。所以这里直接把 KEYCODE_DPAD_CENTER
    # 的 DOWN/UP 派给 decorView，等价于遥控按下去。
    invoke-virtual {v0}, Landroid/app/Activity;->getWindow()Landroid/view/Window;

    move-result-object v5

    invoke-virtual {v5}, Landroid/view/Window;->getDecorView()Landroid/view/View;

    move-result-object v5

    const/16 v4, 0x17

    new-instance v6, Landroid/view/KeyEvent;

    const/4 v7, 0x0

    invoke-direct {v6, v7, v4}, Landroid/view/KeyEvent;-><init>(II)V

    invoke-virtual {v5, v6}, Landroid/view/View;->dispatchKeyEvent(Landroid/view/KeyEvent;)Z

    new-instance v6, Landroid/view/KeyEvent;

    const/4 v7, 0x1

    invoke-direct {v6, v7, v4}, Landroid/view/KeyEvent;-><init>(II)V

    invoke-virtual {v5, v6}, Landroid/view/View;->dispatchKeyEvent(Landroid/view/KeyEvent;)Z

    add-int/lit8 v2, v2, 0x1

    iput v2, p0, Lcom/fongmi/android/tv/ui/activity/HaiAutoPlay;->c:I

    if-ge v2, v3, :cond_2

    new-instance v4, Landroid/os/Handler;

    invoke-static {}, Landroid/os/Looper;->getMainLooper()Landroid/os/Looper;

    move-result-object v5

    invoke-direct {v4, v5}, Landroid/os/Handler;-><init>(Landroid/os/Looper;)V

    const-wide/16 v6, 0x7d0

    invoke-virtual {v4, p0, v6, v7}, Landroid/os/Handler;->postDelayed(Ljava/lang/Runnable;J)Z

    :cond_2
    return-void
.end method
