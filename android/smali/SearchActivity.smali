.class public Lcom/fongmi/android/tv/ui/activity/SearchActivity;
.super LoOoO0oO0O0O0oO0O/oOoOoOoOoOoOoO0o;
.source "SourceFile"

# interfaces
.implements LoOoOo0O0O0OoO0oO/OoOoOo0O0Oo0o0oO;


# static fields
.field public static final synthetic OoOo0o0oOoO0oOo0:I


# instance fields
.field public OoO0oOoO0o0OoOo0:Lcom/fongmi/android/tv/ui/adapter/oOoOoO0O0o0O0o0o;

.field public OoOoO0o0Oo0O0Oo0:Lcom/fongmi/android/tv/ui/adapter/oOoOoO0O0o0O0o0o;

.field public OoOoOo0oO0oOoO0O:Lcom/fongmi/android/tv/ui/adapter/oOoOoO0O0o0O0o0o;

.field public oOoOoOoOoO0oOo0o:LOoOoOo0OoO0o0o0o/oOoOoOoOoOoOoO0o;


# direct methods
.method public constructor <init>()V
    .locals 0

    .line 1
    invoke-direct {p0}, LOoOoO0O0o0oOoO0O/oOoOoO0oOoO0OoOo;-><init>()V

    .line 2
    .line 3
    .line 4
    return-void
.end method


# virtual methods
.method public final OoOo0O0oOo0Oo0o0()LOoOo0O0O0o0Oo0oO/oOoOoOoOoOoOoO0o;
    .locals 13

    .line 1
    invoke-virtual {p0}, Landroid/app/Activity;->getLayoutInflater()Landroid/view/LayoutInflater;

    .line 2
    .line 3
    .line 4
    move-result-object v0

    .line 5
    const/4 v1, 0x0

    .line 6
    const v2, 0x7f0e0024

    .line 7
    .line 8
    .line 9
    const/4 v3, 0x0

    .line 10
    invoke-virtual {v0, v2, v1, v3}, Landroid/view/LayoutInflater;->inflate(ILandroid/view/ViewGroup;Z)Landroid/view/View;

    .line 11
    .line 12
    .line 13
    move-result-object v0

    .line 14
    const v1, 0x7f0b01a4

    .line 15
    .line 16
    .line 17
    invoke-static {v1, v0}, LoOoO0oOo0Oo0oO0O/oOoOo0O0Oo0o0OoO;->oOoOo0o0OoO0Oo0o(ILandroid/view/View;)Landroid/view/View;

    .line 18
    .line 19
    .line 20
    move-result-object v2

    .line 21
    move-object v5, v2

    .line 22
    check-cast v5, Landroid/widget/TextView;

    .line 23
    .line 24
    if-eqz v5, :cond_0

    .line 25
    .line 26
    const v1, 0x7f0b01d1

    .line 27
    .line 28
    .line 29
    invoke-static {v1, v0}, LoOoO0oOo0Oo0oO0O/oOoOo0O0Oo0o0OoO;->oOoOo0o0OoO0Oo0o(ILandroid/view/View;)Landroid/view/View;

    .line 30
    .line 31
    .line 32
    move-result-object v2

    .line 33
    move-object v6, v2

    .line 34
    check-cast v6, Landroidx/recyclerview/widget/RecyclerView;

    .line 35
    .line 36
    if-eqz v6, :cond_0

    .line 37
    .line 38
    const v1, 0x7f0b01d2

    .line 39
    .line 40
    .line 41
    invoke-static {v1, v0}, LoOoO0oOo0Oo0oO0O/oOoOo0O0Oo0o0OoO;->oOoOo0o0OoO0Oo0o(ILandroid/view/View;)Landroid/view/View;

    .line 42
    .line 43
    .line 44
    move-result-object v2

    .line 45
    move-object v7, v2

    .line 46
    check-cast v7, Lcom/fongmi/android/tv/ui/custom/CustomSearchView;

    .line 47
    .line 48
    if-eqz v7, :cond_0

    .line 49
    .line 50
    const v1, 0x7f0b0234

    .line 51
    .line 52
    .line 53
    invoke-static {v1, v0}, LoOoO0oOo0Oo0oO0O/oOoOo0O0Oo0o0OoO;->oOoOo0o0OoO0Oo0o(ILandroid/view/View;)Landroid/view/View;

    .line 54
    .line 55
    .line 56
    move-result-object v2

    .line 57
    move-object v8, v2

    .line 58
    check-cast v8, Lcom/fongmi/android/tv/ui/custom/CustomMic;

    .line 59
    .line 60
    if-eqz v8, :cond_0

    .line 61
    .line 62
    const v1, 0x7f0b02c2

    .line 63
    .line 64
    .line 65
    invoke-static {v1, v0}, LoOoO0oOo0Oo0oO0O/oOoOo0O0Oo0o0OoO;->oOoOo0o0OoO0Oo0o(ILandroid/view/View;)Landroid/view/View;

    .line 66
    .line 67
    .line 68
    move-result-object v2

    .line 69
    move-object v9, v2

    .line 70
    check-cast v9, Landroid/widget/LinearLayout;

    .line 71
    .line 72
    if-eqz v9, :cond_0

    .line 73
    .line 74
    const v1, 0x7f0b02c3

    .line 75
    .line 76
    .line 77
    invoke-static {v1, v0}, LoOoO0oOo0Oo0oO0O/oOoOo0O0Oo0o0OoO;->oOoOo0o0OoO0Oo0o(ILandroid/view/View;)Landroid/view/View;

    .line 78
    .line 79
    .line 80
    move-result-object v2

    .line 81
    move-object v10, v2

    .line 82
    check-cast v10, Landroidx/recyclerview/widget/RecyclerView;

    .line 83
    .line 84
    if-eqz v10, :cond_0

    .line 85
    .line 86
    const v1, 0x7f0b0398

    .line 87
    .line 88
    .line 89
    invoke-static {v1, v0}, LoOoO0oOo0Oo0oO0O/oOoOo0O0Oo0o0OoO;->oOoOo0o0OoO0Oo0o(ILandroid/view/View;)Landroid/view/View;

    .line 90
    .line 91
    .line 92
    move-result-object v2

    .line 93
    move-object v11, v2

    .line 94
    check-cast v11, Landroidx/recyclerview/widget/RecyclerView;

    .line 95
    .line 96
    if-eqz v11, :cond_0

    .line 97
    .line 98
    const v1, 0x7f0b0399

    .line 99
    .line 100
    .line 101
    invoke-static {v1, v0}, LoOoO0oOo0Oo0oO0O/oOoOo0O0Oo0o0OoO;->oOoOo0o0OoO0Oo0o(ILandroid/view/View;)Landroid/view/View;

    .line 102
    .line 103
    .line 104
    move-result-object v2

    .line 105
    move-object v12, v2

    .line 106
    check-cast v12, Landroidx/recyclerview/widget/RecyclerView;

    .line 107
    .line 108
    if-eqz v12, :cond_0

    .line 109
    .line 110
    new-instance v1, LOoOoOo0OoO0o0o0o/oOoOoOoOoOoOoO0o;

    .line 111
    .line 112
    move-object v4, v0

    .line 113
    check-cast v4, Landroid/widget/LinearLayout;

    .line 114
    .line 115
    move-object v3, v1

    .line 116
    invoke-direct/range {v3 .. v12}, LOoOoOo0OoO0o0o0o/oOoOoOoOoOoOoO0o;-><init>(Landroid/widget/LinearLayout;Landroid/widget/TextView;Landroidx/recyclerview/widget/RecyclerView;Lcom/fongmi/android/tv/ui/custom/CustomSearchView;Lcom/fongmi/android/tv/ui/custom/CustomMic;Landroid/widget/LinearLayout;Landroidx/recyclerview/widget/RecyclerView;Landroidx/recyclerview/widget/RecyclerView;Landroidx/recyclerview/widget/RecyclerView;)V

    .line 117
    .line 118
    .line 119
    iput-object v1, p0, Lcom/fongmi/android/tv/ui/activity/SearchActivity;->oOoOoOoOoO0oOo0o:LOoOoOo0OoO0o0o0o/oOoOoOoOoOoOoO0o;

    .line 120
    .line 121
    return-object v1

    .line 122
    :cond_0
    invoke-virtual {v0}, Landroid/view/View;->getResources()Landroid/content/res/Resources;

    .line 123
    .line 124
    .line 125
    move-result-object v0

    .line 126
    invoke-virtual {v0, v1}, Landroid/content/res/Resources;->getResourceName(I)Ljava/lang/String;

    .line 127
    .line 128
    .line 129
    move-result-object v0

    .line 130
    new-instance v1, Ljava/lang/NullPointerException;

    .line 131
    .line 132
    const-string v2, "Missing required view with ID: "

    .line 133
    .line 134
    invoke-virtual {v2, v0}, Ljava/lang/String;->concat(Ljava/lang/String;)Ljava/lang/String;

    .line 135
    .line 136
    .line 137
    move-result-object v0

    .line 138
    invoke-direct {v1, v0}, Ljava/lang/NullPointerException;-><init>(Ljava/lang/String;)V

    .line 139
    .line 140
    .line 141
    throw v1
.end method

.method public final OoOo0OoOo0oO0O0O()V
    .locals 3

    .line 1
    iget-object v0, p0, Lcom/fongmi/android/tv/ui/activity/SearchActivity;->oOoOoOoOoO0oOo0o:LOoOoOo0OoO0o0o0o/oOoOoOoOoOoOoO0o;

    .line 2
    .line 3
    iget-object v0, v0, LOoOoOo0OoO0o0o0o/oOoOoOoOoOoOoO0o;->oOoOoOo0oOo0o0oO:Landroid/widget/TextView;

    .line 4
    .line 5
    const v1, 0x7f1301c2

    .line 6
    .line 7
    .line 8
    invoke-virtual {v0, v1}, Landroid/widget/TextView;->setText(I)V

    .line 9
    .line 10
    .line 11
    iget-object v0, p0, Lcom/fongmi/android/tv/ui/activity/SearchActivity;->OoOoO0o0Oo0O0Oo0:Lcom/fongmi/android/tv/ui/adapter/oOoOoO0O0o0O0o0o;

    .line 12
    .line 13
    const-string v1, ""

    .line 14
    .line 15
    const-string v2, "hot"

    .line 16
    .line 17
    invoke-static {v2, v1}, Lcom/bumptech/glide/oOoO0o0oOo0oO0Oo;->OoO0oOo0Oo0oOo0o(Ljava/lang/String;Ljava/lang/String;)Ljava/lang/String;

    .line 18
    .line 19
    .line 20
    move-result-object v1

    .line 21
    invoke-static {v1}, Lcom/fongmi/android/tv/bean/Hot;->get(Ljava/lang/String;)Ljava/util/List;

    .line 22
    .line 23
    .line 24
    move-result-object v1

    .line 25
    iget-object v2, v0, Lcom/fongmi/android/tv/ui/adapter/oOoOoO0O0o0O0o0o;->oOoOoOo0oOo0o0oO:Ljava/util/List;

    .line 26
    .line 27
    check-cast v2, Ljava/util/ArrayList;

    .line 28
    .line 29
    invoke-virtual {v2}, Ljava/util/ArrayList;->clear()V

    .line 30
    .line 31
    .line 32
    invoke-virtual {v2, v1}, Ljava/util/ArrayList;->addAll(Ljava/util/Collection;)Z

    .line 33
    .line 34
    .line 35
    invoke-virtual {v0}, Landroidx/recyclerview/widget/oOoO0oO0oOo0o0o0;->notifyDataSetChanged()V

    .line 36
    .line 37
    .line 38
    iget-object v0, p0, Lcom/fongmi/android/tv/ui/activity/SearchActivity;->OoO0oOoO0o0OoOo0:Lcom/fongmi/android/tv/ui/adapter/oOoOoO0O0o0O0o0o;

    .line 39
    .line 40
    invoke-static {}, Ljava/util/Collections;->emptyList()Ljava/util/List;

    .line 41
    .line 42
    .line 43
    move-result-object v1

    .line 44
    iget-object v2, v0, Lcom/fongmi/android/tv/ui/adapter/oOoOoO0O0o0O0o0o;->oOoOoOo0oOo0o0oO:Ljava/util/List;

    .line 45
    .line 46
    check-cast v2, Ljava/util/ArrayList;

    .line 47
    .line 48
    invoke-virtual {v2}, Ljava/util/ArrayList;->clear()V

    .line 49
    .line 50
    .line 51
    invoke-virtual {v2, v1}, Ljava/util/ArrayList;->addAll(Ljava/util/Collection;)Z

    .line 52
    .line 53
    .line 54
    invoke-virtual {v0}, Landroidx/recyclerview/widget/oOoO0oO0oOo0o0o0;->notifyDataSetChanged()V

    .line 55
    .line 56
    .line 57
    const-string v0, "Referer"

    .line 58
    .line 59
    const-string v1, "https://www.360kan.com/rank/general"

    .line 60
    .line 61
    filled-new-array {v0, v1}, [Ljava/lang/String;

    .line 62
    .line 63
    .line 64
    move-result-object v0

    .line 65
    invoke-static {v0}, Lokhttp3/Headers;->of([Ljava/lang/String;)Lokhttp3/Headers;

    .line 66
    .line 67
    .line 68
    move-result-object v0

    .line 69
    const-string v1, "https://api.web.360kan.com/v1/rank?cat=1"

    .line 70
    .line 71
    invoke-static {v1, v0}, LOoO0oOo0Oo0o0Oo0/oOoOoOo0oOo0o0oO;->oOoOoOo0O0O0oO0o(Ljava/lang/String;Lokhttp3/Headers;)Lokhttp3/Call;

    .line 72
    .line 73
    .line 74
    move-result-object v0

    .line 75
    new-instance v1, Lcom/fongmi/android/tv/ui/activity/oOoO0Oo0OoOoOoOo;

    .line 76
    .line 77
    const/4 v2, 0x0

    .line 78
    invoke-direct {v1, p0, v2}, Lcom/fongmi/android/tv/ui/activity/oOoO0Oo0OoOoOoOo;-><init>(Lcom/fongmi/android/tv/ui/activity/SearchActivity;I)V

    .line 79
    .line 80
    .line 81
    invoke-interface {v0, v1}, Lokhttp3/Call;->enqueue(Lokhttp3/Callback;)V

    .line 82
    .line 83
    .line 84
    return-void
.end method

.method public final OoOoO0oOoO0O0OoO()V
    .locals 3

    .line 1
    iget-object v0, p0, Lcom/fongmi/android/tv/ui/activity/SearchActivity;->oOoOoOoOoO0oOo0o:LOoOoOo0OoO0o0o0o/oOoOoOoOoOoOoO0o;

    .line 2
    .line 3
    iget-object v0, v0, LOoOoOo0OoO0o0o0o/oOoOoOoOoOoOoO0o;->OoOoOo0O0o0oO0o0:Landroid/view/View;

    .line 4
    .line 5
    check-cast v0, Lcom/fongmi/android/tv/ui/custom/CustomSearchView;

    .line 6
    .line 7
    new-instance v1, Lcom/fongmi/android/tv/ui/activity/OoOoOo0o0OoO0oOo;

    .line 8
    .line 9
    const/4 v2, 0x0

    .line 10
    invoke-direct {v1, v2, p0}, Lcom/fongmi/android/tv/ui/activity/OoOoOo0o0OoO0oOo;-><init>(ILjava/lang/Object;)V

    .line 11
    .line 12
    .line 13
    invoke-virtual {v0, v1}, Landroid/widget/TextView;->setOnEditorActionListener(Landroid/widget/TextView$OnEditorActionListener;)V

    .line 14
    .line 15
    .line 16
    iget-object v0, p0, Lcom/fongmi/android/tv/ui/activity/SearchActivity;->oOoOoOoOoO0oOo0o:LOoOoOo0OoO0o0o0o/oOoOoOoOoOoOoO0o;

    .line 17
    .line 18
    iget-object v0, v0, LOoOoOo0OoO0o0o0o/oOoOoOoOoOoOoO0o;->OoOoOo0O0o0oO0o0:Landroid/view/View;

    .line 19
    .line 20
    check-cast v0, Lcom/fongmi/android/tv/ui/custom/CustomSearchView;

    .line 21
    .line 22
    new-instance v1, Lcom/fongmi/android/tv/ui/activity/OoOo0OoO0OoO0oO0;

    .line 23
    .line 24
    invoke-direct {v1, p0, v2}, Lcom/fongmi/android/tv/ui/activity/OoOo0OoO0OoO0oO0;-><init>(Lcom/fongmi/android/tv/ui/activity/SearchActivity;I)V

    .line 25
    .line 26
    .line 27
    invoke-virtual {v0, v1}, Landroid/widget/TextView;->addTextChangedListener(Landroid/text/TextWatcher;)V

    .line 28
    .line 29
    .line 30
    iget-object v0, p0, Lcom/fongmi/android/tv/ui/activity/SearchActivity;->oOoOoOoOoO0oOo0o:LOoOoOo0OoO0o0o0o/oOoOoOoOoOoOoO0o;

    .line 31
    .line 32
    iget-object v0, v0, LOoOoOo0OoO0o0o0o/oOoOoOoOoOoOoO0o;->oOoOo0O0Oo0o0OoO:Landroid/view/View;

    .line 33
    .line 34
    check-cast v0, Lcom/fongmi/android/tv/ui/custom/CustomMic;

    .line 35
    .line 36
    new-instance v1, Lcom/fongmi/android/tv/ui/activity/OoOo0OoO0OoO0oO0;

    .line 37
    .line 38
    const/4 v2, 0x1

    .line 39
    invoke-direct {v1, p0, v2}, Lcom/fongmi/android/tv/ui/activity/OoOo0OoO0OoO0oO0;-><init>(Lcom/fongmi/android/tv/ui/activity/SearchActivity;I)V

    .line 40
    .line 41
    .line 42
    iget-object v2, v0, Lcom/fongmi/android/tv/ui/custom/CustomMic;->oOoOoOo0O0O0oO0o:Landroid/speech/SpeechRecognizer;

    .line 43
    .line 44
    invoke-virtual {v2, v1}, Landroid/speech/SpeechRecognizer;->setRecognitionListener(Landroid/speech/RecognitionListener;)V

    .line 45
    .line 46
    .line 47
    iput-object p0, v0, Lcom/fongmi/android/tv/ui/custom/CustomMic;->oOoO0o0oOo0oO0Oo:Lcom/fongmi/android/tv/ui/activity/SearchActivity;

    .line 48
    .line 49
    return-void
.end method

.method public final OoOoOo0O0Oo0o0oO(Lcom/fongmi/android/tv/bean/Site;)V
    .locals 0

    .line 1
    return-void
.end method

.method public final dispatchKeyEvent(Landroid/view/KeyEvent;)Z
    .locals 2

    .line 1
    invoke-static {p1}, LoOoO0oO0o0o0oOo0/oOo0oO0o0O0O0Oo0;->OoOoOo0oO0oOoO0O(Landroid/view/KeyEvent;)Z

    .line 2
    .line 3
    .line 4
    move-result v0

    .line 5
    if-eqz v0, :cond_0

    .line 6
    .line 7
    new-instance v0, LOoOoO0o0oO0O0O0O/OoOo0oOoO0Oo0Oo0;

    .line 8
    .line 9
    const/4 v1, 0x3

    .line 10
    invoke-direct {v0, p0, v1}, LOoOoO0o0oO0O0O0O/OoOo0oOoO0Oo0Oo0;-><init>(Landroid/app/Activity;I)V

    .line 11
    .line 12
    .line 13
    const/4 v1, 0x1

    .line 14
    iput v1, v0, LOoOoO0o0oO0O0O0O/OoOo0oOoO0Oo0Oo0;->oOoOoOoOoOoOoO0o:I

    .line 15
    .line 16
    invoke-virtual {v0}, LOoOoO0o0oO0O0O0O/OoOo0oOoO0Oo0Oo0;->OoOo0o0Oo0oO0o0o()V

    .line 17
    .line 18
    .line 19
    :cond_0
    invoke-super {p0, p1}, LOoOoO0O0o0oOoO0O/oOoOoO0oOoO0OoOo;->dispatchKeyEvent(Landroid/view/KeyEvent;)Z

    .line 20
    .line 21
    .line 22
    move-result p1

    .line 23
    return p1
.end method

.method public final oOo0oOo0O0oOoO0o()V
    .locals 5

    .line 1
    iget-object v0, p0, Lcom/fongmi/android/tv/ui/activity/SearchActivity;->oOoOoOoOoO0oOo0o:LOoOoOo0OoO0o0o0o/oOoOoOoOoOoOoO0o;

    .line 2
    .line 3
    new-instance v1, Landroid/support/v4/media/session/OoOo0oOoO0Oo0Oo0;

    .line 4
    .line 5
    invoke-direct {v1, p0, v0}, Landroid/support/v4/media/session/OoOo0oOoO0Oo0Oo0;-><init>(Lcom/fongmi/android/tv/ui/activity/SearchActivity;LOoOoOo0OoO0o0o0o/oOoOoOoOoOoOoO0o;)V

    .line 6
    .line 7
    .line 8
    iget-object v2, v0, LOoOoOo0OoO0o0o0o/oOoOoOoOoOoOoO0o;->oOoO0o0oOo0oO0Oo:Landroid/view/View;

    .line 9
    .line 10
    check-cast v2, Landroidx/recyclerview/widget/RecyclerView;

    .line 11
    .line 12
    const/4 v3, 0x0

    .line 13
    invoke-virtual {v2, v3}, Landroidx/recyclerview/widget/RecyclerView;->setItemAnimator(Landroidx/recyclerview/widget/oOo0oOo0O0oOoO0o;)V

    .line 14
    .line 15
    .line 16
    iget-object v0, v0, LOoOoOo0OoO0o0o0o/oOoOoOoOoOoOoO0o;->oOoO0o0oOo0oO0Oo:Landroid/view/View;

    .line 17
    .line 18
    check-cast v0, Landroidx/recyclerview/widget/RecyclerView;

    .line 19
    .line 20
    const/4 v2, 0x0

    .line 21
    invoke-virtual {v0, v2}, Landroidx/recyclerview/widget/RecyclerView;->setHasFixedSize(Z)V

    .line 22
    .line 23
    .line 24
    new-instance v2, LOoO0oOoO0oO0o0o0/oOoO0oO0oOo0o0o0;

    .line 25
    .line 26
    const/4 v3, 0x6

    .line 27
    const/16 v4, 0x8

    .line 28
    .line 29
    invoke-direct {v2, v3, v4}, LOoO0oOoO0oO0o0o0/oOoO0oO0oOo0o0o0;-><init>(II)V

    .line 30
    .line 31
    .line 32
    invoke-virtual {v0, v2}, Landroidx/recyclerview/widget/RecyclerView;->OoOo0oO0o0o0oOo0(Landroidx/recyclerview/widget/oOoOo0oOoOo0O0oO;)V

    .line 33
    .line 34
    .line 35
    new-instance v2, Lcom/fongmi/android/tv/ui/adapter/oOoOoO0oOo0oO0O0;

    .line 36
    .line 37
    invoke-direct {v2, v1}, Lcom/fongmi/android/tv/ui/adapter/oOoOoO0oOo0oO0O0;-><init>(Landroid/support/v4/media/session/OoOo0oOoO0Oo0Oo0;)V

    .line 38
    .line 39
    .line 40
    iput-object v2, v1, Landroid/support/v4/media/session/OoOo0oOoO0Oo0Oo0;->oOoOoOo0O0O0oO0o:Ljava/lang/Object;

    .line 41
    .line 42
    invoke-virtual {v0, v2}, Landroidx/recyclerview/widget/RecyclerView;->setAdapter(Landroidx/recyclerview/widget/oOoO0oO0oOo0o0o0;)V

    .line 43
    .line 44
    .line 45
    iget-object v0, p0, Lcom/fongmi/android/tv/ui/activity/SearchActivity;->oOoOoOoOoO0oOo0o:LOoOoOo0OoO0o0o0o/oOoOoOoOoOoOoO0o;

    .line 46
    .line 47
    iget-object v0, v0, LOoOoOo0OoO0o0o0o/oOoOoOoOoOoOoO0o;->oOo0oO0o0O0O0Oo0:Landroid/view/View;

    .line 48
    .line 49
    check-cast v0, Landroidx/recyclerview/widget/RecyclerView;

    .line 50
    .line 51
    const/4 v1, 0x1

    .line 52
    invoke-virtual {v0, v1}, Landroidx/recyclerview/widget/RecyclerView;->setHasFixedSize(Z)V

    .line 53
    .line 54
    .line 55
    iget-object v0, p0, Lcom/fongmi/android/tv/ui/activity/SearchActivity;->oOoOoOoOoO0oOo0o:LOoOoOo0OoO0o0o0o/oOoOoOoOoOoOoO0o;

    .line 56
    .line 57
    iget-object v0, v0, LOoOoOo0OoO0o0o0o/oOoOoOoOoOoOoO0o;->oOo0oO0o0O0O0Oo0:Landroid/view/View;

    .line 58
    .line 59
    check-cast v0, Landroidx/recyclerview/widget/RecyclerView;

    .line 60
    .line 61
    new-instance v2, LOoO0oOoO0oO0o0o0/oOoO0oO0oOo0o0o0;

    .line 62
    .line 63
    const/16 v3, 0x10

    .line 64
    .line 65
    invoke-direct {v2, v1, v3}, LOoO0oOoO0oO0o0o0/oOoO0oO0oOo0o0o0;-><init>(II)V

    .line 66
    .line 67
    .line 68
    invoke-virtual {v0, v2}, Landroidx/recyclerview/widget/RecyclerView;->OoOo0oO0o0o0oOo0(Landroidx/recyclerview/widget/oOoOo0oOoOo0O0oO;)V

    .line 69
    .line 70
    .line 71
    iget-object v0, p0, Lcom/fongmi/android/tv/ui/activity/SearchActivity;->oOoOoOoOoO0oOo0o:LOoOoOo0OoO0o0o0o/oOoOoOoOoOoOoO0o;

    .line 72
    .line 73
    iget-object v0, v0, LOoOoOo0OoO0o0o0o/oOoOoOoOoOoOoO0o;->oOo0oO0o0O0O0Oo0:Landroid/view/View;

    .line 74
    .line 75
    check-cast v0, Landroidx/recyclerview/widget/RecyclerView;

    .line 76
    .line 77
    new-instance v2, Lcom/fongmi/android/tv/ui/adapter/oOoOoO0O0o0O0o0o;

    .line 78
    .line 79
    const/4 v4, 0x7

    .line 80
    invoke-direct {v2, p0, v4}, Lcom/fongmi/android/tv/ui/adapter/oOoOoO0O0o0O0o0o;-><init>(Lcom/fongmi/android/tv/ui/activity/SearchActivity;I)V

    .line 81
    .line 82
    .line 83
    iput-object v2, p0, Lcom/fongmi/android/tv/ui/activity/SearchActivity;->OoOoO0o0Oo0O0Oo0:Lcom/fongmi/android/tv/ui/adapter/oOoOoO0O0o0O0o0o;

    .line 84
    .line 85
    invoke-virtual {v0, v2}, Landroidx/recyclerview/widget/RecyclerView;->setAdapter(Landroidx/recyclerview/widget/oOoO0oO0oOo0o0o0;)V

    .line 86
    .line 87
    .line 88
    iget-object v0, p0, Lcom/fongmi/android/tv/ui/activity/SearchActivity;->oOoOoOoOoO0oOo0o:LOoOoOo0OoO0o0o0o/oOoOoOoOoOoOoO0o;

    .line 89
    .line 90
    iget-object v0, v0, LOoOoOo0OoO0o0o0o/oOoOoOoOoOoOoO0o;->OoOo0oO0o0o0oOo0:Landroid/view/View;

    .line 91
    .line 92
    check-cast v0, Landroidx/recyclerview/widget/RecyclerView;

    .line 93
    .line 94
    invoke-virtual {v0, v1}, Landroidx/recyclerview/widget/RecyclerView;->setHasFixedSize(Z)V

    .line 95
    .line 96
    .line 97
    iget-object v0, p0, Lcom/fongmi/android/tv/ui/activity/SearchActivity;->oOoOoOoOoO0oOo0o:LOoOoOo0OoO0o0o0o/oOoOoOoOoOoOoO0o;

    .line 98
    .line 99
    iget-object v0, v0, LOoOoOo0OoO0o0o0o/oOoOoOoOoOoOoO0o;->OoOo0oO0o0o0oOo0:Landroid/view/View;

    .line 100
    .line 101
    check-cast v0, Landroidx/recyclerview/widget/RecyclerView;

    .line 102
    .line 103
    new-instance v2, LOoO0oOoO0oO0o0o0/oOoO0oO0oOo0o0o0;

    .line 104
    .line 105
    invoke-direct {v2, v1, v3}, LOoO0oOoO0oO0o0o0/oOoO0oO0oOo0o0o0;-><init>(II)V

    .line 106
    .line 107
    .line 108
    invoke-virtual {v0, v2}, Landroidx/recyclerview/widget/RecyclerView;->OoOo0oO0o0o0oOo0(Landroidx/recyclerview/widget/oOoOo0oOoOo0O0oO;)V

    .line 109
    .line 110
    .line 111
    iget-object v0, p0, Lcom/fongmi/android/tv/ui/activity/SearchActivity;->oOoOoOoOoO0oOo0o:LOoOoOo0OoO0o0o0o/oOoOoOoOoOoOoO0o;

    .line 112
    .line 113
    iget-object v0, v0, LOoOoOo0OoO0o0o0o/oOoOoOoOoOoOoO0o;->OoOo0oO0o0o0oOo0:Landroid/view/View;

    .line 114
    .line 115
    check-cast v0, Landroidx/recyclerview/widget/RecyclerView;

    .line 116
    .line 117
    new-instance v2, Lcom/fongmi/android/tv/ui/adapter/oOoOoO0O0o0O0o0o;

    .line 118
    .line 119
    invoke-direct {v2, p0, v4}, Lcom/fongmi/android/tv/ui/adapter/oOoOoO0O0o0O0o0o;-><init>(Lcom/fongmi/android/tv/ui/activity/SearchActivity;I)V

    .line 120
    .line 121
    .line 122
    iput-object v2, p0, Lcom/fongmi/android/tv/ui/activity/SearchActivity;->OoO0oOoO0o0OoOo0:Lcom/fongmi/android/tv/ui/adapter/oOoOoO0O0o0O0o0o;

    .line 123
    .line 124
    invoke-virtual {v0, v2}, Landroidx/recyclerview/widget/RecyclerView;->setAdapter(Landroidx/recyclerview/widget/oOoO0oO0oOo0o0o0;)V

    .line 125
    .line 126
    .line 127
    iget-object v0, p0, Lcom/fongmi/android/tv/ui/activity/SearchActivity;->oOoOoOoOoO0oOo0o:LOoOoOo0OoO0o0o0o/oOoOoOoOoOoOoO0o;

    .line 128
    .line 129
    iget-object v0, v0, LOoOoOo0OoO0o0o0o/oOoOoOoOoOoOoO0o;->OoOoO0O0o0oOoO0O:Landroid/view/View;

    .line 130
    .line 131
    check-cast v0, Landroidx/recyclerview/widget/RecyclerView;

    .line 132
    .line 133
    invoke-virtual {v0, v1}, Landroidx/recyclerview/widget/RecyclerView;->setHasFixedSize(Z)V

    .line 134
    .line 135
    .line 136
    iget-object v0, p0, Lcom/fongmi/android/tv/ui/activity/SearchActivity;->oOoOoOoOoO0oOo0o:LOoOoOo0OoO0o0o0o/oOoOoOoOoOoOoO0o;

    .line 137
    .line 138
    iget-object v0, v0, LOoOoOo0OoO0o0o0o/oOoOoOoOoOoOoO0o;->OoOoO0O0o0oOoO0O:Landroid/view/View;

    .line 139
    .line 140
    check-cast v0, Landroidx/recyclerview/widget/RecyclerView;

    .line 141
    .line 142
    new-instance v2, LOoO0oOoO0oO0o0o0/oOoO0oO0oOo0o0o0;

    .line 143
    .line 144
    invoke-direct {v2, v1, v3}, LOoO0oOoO0oO0o0o0/oOoO0oO0oOo0o0o0;-><init>(II)V

    .line 145
    .line 146
    .line 147
    invoke-virtual {v0, v2}, Landroidx/recyclerview/widget/RecyclerView;->OoOo0oO0o0o0oOo0(Landroidx/recyclerview/widget/oOoOo0oOoOo0O0oO;)V

    .line 148
    .line 149
    .line 150
    iget-object v0, p0, Lcom/fongmi/android/tv/ui/activity/SearchActivity;->oOoOoOoOoO0oOo0o:LOoOoOo0OoO0o0o0o/oOoOoOoOoOoOoO0o;

    .line 151
    .line 152
    iget-object v0, v0, LOoOoOo0OoO0o0o0o/oOoOoOoOoOoOoO0o;->OoOoO0O0o0oOoO0O:Landroid/view/View;

    .line 153
    .line 154
    check-cast v0, Landroidx/recyclerview/widget/RecyclerView;

    .line 155
    .line 156
    new-instance v1, Lcom/fongmi/android/tv/ui/adapter/oOoOoO0O0o0O0o0o;

    .line 157
    .line 158
    const/4 v2, 0x0

    .line 159
    invoke-direct {v1, p0, v2}, Lcom/fongmi/android/tv/ui/adapter/oOoOoO0O0o0O0o0o;-><init>(Lcom/fongmi/android/tv/ui/activity/SearchActivity;I)V

    .line 160
    .line 161
    .line 162
    iput-object v1, p0, Lcom/fongmi/android/tv/ui/activity/SearchActivity;->OoOoOo0oO0oOoO0O:Lcom/fongmi/android/tv/ui/adapter/oOoOoO0O0o0O0o0o;

    .line 163
    .line 164
    invoke-virtual {v0, v1}, Landroidx/recyclerview/widget/RecyclerView;->setAdapter(Landroidx/recyclerview/widget/oOoO0oO0oOo0o0o0;)V

    .line 165
    .line 166
    .line 167
    invoke-virtual {p0}, Lcom/fongmi/android/tv/ui/activity/SearchActivity;->OoOo0OoOo0oO0O0O()V

    .line 168
    .line 169
    .line 170
    return-void
.end method

.method public final oOoOoO0oO0oOoOoO()V
    .locals 4

    .line 1
    iget-object v0, p0, Lcom/fongmi/android/tv/ui/activity/SearchActivity;->oOoOoOoOoO0oOo0o:LOoOoOo0OoO0o0o0o/oOoOoOoOoOoOoO0o;

    .line 2
    .line 3
    iget-object v0, v0, LOoOoOo0OoO0o0o0o/oOoOoOoOoOoOoO0o;->OoOoOo0O0o0oO0o0:Landroid/view/View;

    .line 4
    .line 5
    check-cast v0, Lcom/fongmi/android/tv/ui/custom/CustomSearchView;

    .line 6
    .line 7
    invoke-virtual {v0}, LOoOoO0o0oO0O0O0O/OoOo0o0Oo0oO0o0o;->getText()Landroid/text/Editable;

    .line 8
    .line 9
    .line 10
    move-result-object v0

    .line 11
    invoke-virtual {v0}, Ljava/lang/Object;->toString()Ljava/lang/String;

    .line 12
    .line 13
    .line 14
    move-result-object v0

    .line 15
    invoke-virtual {v0}, Ljava/lang/String;->trim()Ljava/lang/String;

    .line 16
    .line 17
    .line 18
    move-result-object v0

    .line 19
    iget-object v1, p0, Lcom/fongmi/android/tv/ui/activity/SearchActivity;->oOoOoOoOoO0oOo0o:LOoOoOo0OoO0o0o0o/oOoOoOoOoOoOoO0o;

    .line 20
    .line 21
    iget-object v1, v1, LOoOoOo0OoO0o0o0o/oOoOoOoOoOoOoO0o;->OoOoOo0O0o0oO0o0:Landroid/view/View;

    .line 22
    .line 23
    check-cast v1, Lcom/fongmi/android/tv/ui/custom/CustomSearchView;

    .line 24
    .line 25
    invoke-virtual {v1}, Landroid/widget/TextView;->length()I

    .line 26
    .line 27
    .line 28
    move-result v2

    .line 29
    invoke-virtual {v1, v2}, Landroid/widget/EditText;->setSelection(I)V

    .line 30
    .line 31
    .line 32
    iget-object v1, p0, Lcom/fongmi/android/tv/ui/activity/SearchActivity;->oOoOoOoOoO0oOo0o:LOoOoOo0OoO0o0o0o/oOoOoOoOoOoOoO0o;

    .line 33
    .line 34
    iget-object v1, v1, LOoOoOo0OoO0o0o0o/oOoOoOoOoOoOoO0o;->OoOoOo0O0o0oO0o0:Landroid/view/View;

    .line 35
    .line 36
    check-cast v1, Lcom/fongmi/android/tv/ui/custom/CustomSearchView;

    .line 37
    .line 38
    sget-object v2, Lcom/fongmi/android/tv/App;->OoOoOo0O0o0oO0o0:Lcom/fongmi/android/tv/App;

    .line 39
    .line 40
    const-string v3, "input_method"

    .line 41
    .line 42
    invoke-virtual {v2, v3}, Landroid/content/Context;->getSystemService(Ljava/lang/String;)Ljava/lang/Object;

    .line 43
    .line 44
    .line 45
    move-result-object v2

    .line 46
    check-cast v2, Landroid/view/inputmethod/InputMethodManager;

    .line 47
    .line 48
    invoke-virtual {v1}, Landroid/view/View;->getWindowToken()Landroid/os/IBinder;

    .line 49
    .line 50
    .line 51
    move-result-object v1

    .line 52
    const/4 v3, 0x0

    .line 53
    if-eqz v2, :cond_1

    .line 54
    .line 55
    if-nez v1, :cond_0

    .line 56
    .line 57
    goto :goto_0

    .line 58
    :cond_0
    invoke-virtual {v2, v1, v3}, Landroid/view/inputmethod/InputMethodManager;->hideSoftInputFromWindow(Landroid/os/IBinder;I)Z

    .line 59
    .line 60
    .line 61
    :cond_1
    :goto_0
    invoke-static {v0}, Landroid/text/TextUtils;->isEmpty(Ljava/lang/CharSequence;)Z

    .line 62
    .line 63
    .line 64
    move-result v1

    .line 65
    if-eqz v1, :cond_2

    .line 66
    .line 67
    return-void

    .line 68
    :cond_2
    invoke-static {p0, v0, v3}, Lcom/fongmi/android/tv/ui/activity/CollectActivity;->OoOo0OoOo0oO0O0O(LOoOoO0O0o0oOoO0O/oOoOoO0oOoO0OoOo;Ljava/lang/String;Z)V

    .line 69
    .line 70
    .line 71
    new-instance v1, LOoOo0o0oOoO0oOo0/oOoOoOo0oO0oO0o0;

    .line 72
    .line 73
    const/16 v2, 0xe

    .line 74
    .line 75
    invoke-direct {v1, p0, v0, v2}, LOoOo0o0oOoO0oOo0/oOoOoOo0oO0oO0o0;-><init>(Ljava/lang/Object;Ljava/lang/Object;I)V

    .line 76
    .line 77
    .line 78
    const-wide/16 v2, 0xfa

    .line 79
    .line 80
    invoke-static {v1, v2, v3}, Lcom/fongmi/android/tv/App;->oOoOoOo0oOo0o0oO(Ljava/lang/Runnable;J)V

    .line 81
    .line 82
    .line 83
    return-void
.end method

.method public final onResume()V
    .locals 3

    .line 1
    invoke-super {p0}, LOoOoO0O0o0oOoO0O/oOoOoO0oOoO0OoOo;->onResume()V

    .line 2
    .line 3
    .line 4
    iget-object v0, p0, Lcom/fongmi/android/tv/ui/activity/SearchActivity;->oOoOoOoOoO0oOo0o:LOoOoOo0OoO0o0o0o/oOoOoOoOoOoOoO0o;

    .line 5
    .line 6
    iget-object v0, v0, LOoOoOo0OoO0o0o0o/oOoOoOoOoOoOoO0o;->OoOoOo0O0o0oO0o0:Landroid/view/View;

    .line 7
    .line 8
    check-cast v0, Lcom/fongmi/android/tv/ui/custom/CustomSearchView;

    .line 9
    .line 10
    # 海哥影视：海报墙点「播放(网盘)/搜索」带 keyword 进来，这里预填并自动搜索。
    # 注意：TextWatcher 只拉联想词，真正的站点搜索走 oOoOoO0oO0oOoOoO()
    # （原版搜索按钮执行体：隐藏 IME -> CollectActivity 搜全站）。
    # 只在搜索框为空时才填，避免返回 onResume 时反复重置用户输入。
    invoke-virtual {p0}, Landroid/app/Activity;->getIntent()Landroid/content/Intent;
    move-result-object v1
    const-string v2, "keyword"
    invoke-virtual {v1, v2}, Landroid/content/Intent;->getStringExtra(Ljava/lang/String;)Ljava/lang/String;
    move-result-object v1
    if-eqz v1, :kw_skip
    invoke-virtual {v0}, Landroid/widget/TextView;->getText()Ljava/lang/CharSequence;
    move-result-object v2
    invoke-interface {v2}, Ljava/lang/CharSequence;->length()I
    move-result v2
    if-nez v2, :kw_skip
    invoke-virtual {v0, v1}, Landroid/widget/TextView;->setText(Ljava/lang/CharSequence;)V
    invoke-virtual {p0}, Lcom/fongmi/android/tv/ui/activity/SearchActivity;->oOoOoO0oO0oOoOoO()V
    :kw_skip

    invoke-virtual {v0}, Landroid/view/View;->requestFocus()Z

    .line 11
    .line 12
    .line 13
    return-void
.end method
