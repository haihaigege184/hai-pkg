(function(){
  // 自愈：页面几乎是空的（WebView 被系统回收后重载失败 / 网关抽风）就自己刷一次。
  // 只在真的空页时触发，最多两次，正常页面永远不刷新。
  setTimeout(function(){
    try{
      var txt = document.body ? (document.body.innerText || '') : '';
      var h = document.body ? document.body.scrollHeight : 0;
      if (txt.length < 12 && h < 320) {
        var n = +(sessionStorage.getItem('hai_nr') || 0);
        if (n < 2) { sessionStorage.setItem('hai_nr', n + 1); location.reload(); }
      } else {
        sessionStorage.removeItem('hai_nr');
      }
    }catch(e){}
  }, 3500);
})();