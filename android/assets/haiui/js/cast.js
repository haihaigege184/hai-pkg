(function(){
  var els = [].slice.call(document.querySelectorAll('#castSec .citem[data-nm]'));
  if (!els.length) return;
  var x = new XMLHttpRequest();
  x.open('GET', '/hai/poster/cast?vid=%VID%', true);
  x.onreadystatechange = function(){
    if (x.readyState !== 4) return;
    var r; try { r = JSON.parse(x.responseText); } catch(e) { return; }
    if (!r || !r.people) return;
    var map = {}, i;
    for (i = 0; i < r.people.length; i++) { map[r.people[i].name] = r.people[i]; }
    for (i = 0; i < els.length; i++) {
      var el = els[i], p = map[el.getAttribute('data-nm')];
      if (!p) continue;
      if (p.cid) { el.setAttribute('href', '/hai/poster/p/' + encodeURIComponent(p.cid)); }
      if (p.avatar) {
        var f = el.querySelector('.cface');
        if (f) {
          // 首字母去掉引号/尖括号再进属性，别把 data-ch 截断（见 _JS_FACE 注释）
          var ch = String((p.name || '?').charAt(0)).replace(/["'<>&]/g, '');
          f.className = 'cface';
          f.innerHTML = '<img src="' + p.avatar + '" alt="" data-ch="' + ch
            + '" onerror="haiFaceMiss(this)">';
        }
      }
    }
  };
  x.send();
})();