(function () {
  var imgs = %IMGS%;
  var INTERVAL = %INTERVAL%;      // 秒
  var EFFECT = '%EFFECT%';
  var A = document.getElementById('fxA'), B = document.getElementById('fxB');
  if (!A || !B || !imgs || imgs.length < 2) {
    if (A) { A.src = imgs ? imgs[0] : ''; A.style.opacity = 1; }
    return;
  }
  var cur = A, bak = B, i = 0;
  cur.src = imgs[0]; cur.style.opacity = 1;
  var FXS = ['fade', 'zoom', 'slide', 'ken'];
  var HIDE_TF = { zoom: 'scale(1.07)', slide: 'translateX(-3.5%)', ken: 'scale(1.12)' };
  function pick() { return EFFECT === 'random' ? FXS[Math.floor(Math.random() * FXS.length)] : EFFECT; }
  setInterval(function () {
    i = (i + 1) % imgs.length;
    var fx = pick();
    bak.src = imgs[i];
    bak.style.transition = 'none';
    bak.style.transform = HIDE_TF[fx] || 'none';
    bak.style.opacity = 0;
    void bak.offsetWidth;                 // 强制 reflow，让起始态生效
    bak.style.transition = '';
    bak.style.opacity = 1;
    bak.style.transform = 'none';
    cur.style.opacity = 0;
    if (fx === 'ken') { cur.style.transform = 'scale(1.1)'; }
    var old = cur; cur = bak; bak = old;
  }, INTERVAL * 1000);
})();