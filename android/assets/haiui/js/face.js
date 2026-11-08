window.haiFaceMiss=function(img){
  if(!img||!img.parentNode){return;}
  if(!img.getAttribute('data-r')){
    img.setAttribute('data-r','1');
    var s=img.getAttribute('src')||'';
    img.setAttribute('src',s+(s.indexOf('?')<0?'?':'&')+'r='+((new Date()).getTime()));
    return;
  }
  var p=img.parentNode;
  p.className='cface noimg';
  p.textContent=img.getAttribute('data-ch')||'?';
};