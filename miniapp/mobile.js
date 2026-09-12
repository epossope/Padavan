/* Long-press sortable grid: floating card follows the pointer; neighbours FLIP. */
(()=>{
 let pending=null,drag=null,timer=null,frame=null,saving=false;
 const reduced=matchMedia('(prefers-reduced-motion: reduce)').matches;
 function animatePositions(before){for(const node of document.querySelectorAll('.grid>[data-widget]')){const old=before.get(node),now=node.getBoundingClientRect();if(old&&node!==drag?.node&&!reduced){node.getAnimations().forEach(a=>a.cancel());node.animate([{transform:`translate(${old.left-now.left}px,${old.top-now.top}px)`},{transform:'none'}],{duration:220,easing:'cubic-bezier(.2,.8,.2,1)'})}}}
 function beginDrag(node,sample){const rect=node.getBoundingClientRect(),ghost=node.cloneNode(true);ghost.removeAttribute('data-widget');ghost.removeAttribute('id');ghost.querySelectorAll('[id]').forEach(n=>n.removeAttribute('id'));ghost.querySelectorAll('button').forEach(n=>n.remove());ghost.className='widget-ghost';ghost.setAttribute('aria-hidden','true');ghost.style.width=rect.width+'px';ghost.style.height=rect.height+'px';document.body.append(ghost);drag={...sample,node,ghost,offsetX:sample.x-rect.left,offsetY:sample.y-rect.top,lastSwap:0};held=true;node.classList.add('widget-placeholder');document.body.classList.add('sorting');tg?.HapticFeedback?.impactOccurred('light');tick()}
 function tick(){if(!drag)return;const d=drag;d.ghost.style.transform=`translate3d(${d.x-d.offsetX}px,${d.y-d.offsetY}px,0) scale(1.025)`;
  const other=document.elementsFromPoint(d.x,d.y).map(n=>n.closest?.('.grid>[data-widget]')).find(n=>n&&n!==d.node);
  if(other&&performance.now()-d.lastSwap>170){const r=other.getBoundingClientRect();if(d.x>r.left+12&&d.x<r.right-12&&d.y>r.top+12&&d.y<r.bottom-12){const nodes=[...d.node.parentNode.children],before=new Map(nodes.map(n=>[n,n.getBoundingClientRect()]));if(nodes.indexOf(d.node)<nodes.indexOf(other))other.after(d.node);else other.before(d.node);animatePositions(before);d.lastSwap=performance.now()}}
  if(d.y<95)window.scrollBy(0,-6);else if(d.y>innerHeight-170)window.scrollBy(0,6);frame=requestAnimationFrame(tick);
 }
 async function finish(cancel=false){clearTimeout(timer);pending=null;if(!drag)return;cancelAnimationFrame(frame);const d=drag;drag=null;saving=true;
  const r=d.node.getBoundingClientRect();if(!reduced)await d.ghost.animate([{transform:d.ghost.style.transform},{transform:`translate3d(${r.left}px,${r.top}px,0) scale(1)`}],{duration:180,easing:'ease-out',fill:'forwards'}).finished.catch(()=>{});
  d.ghost.remove();d.node.classList.remove('widget-placeholder');document.body.classList.remove('sorting');held=true;setTimeout(()=>held=false,350);
  const widgets=[...document.querySelectorAll('.grid>[data-widget]')].map(n=>n.dataset.widget);
  try{if(cancel){render();return}if(!preview)await api('home_layout',{widgets});data.settings.home_widgets=widgets}
  catch(e){say('Не удалось сохранить порядок. Попробуй ещё раз.');render()}finally{saving=false}
 }
 document.addEventListener('pointerdown',e=>{const node=e.target.closest('.grid>[data-widget]');if(!node||busy||saving||e.button!==0||e.target.closest('.home-widget-hide'))return;pending={node,id:e.pointerId,x:e.clientX,y:e.clientY,widget:node.dataset.widget};if(window.homeEditing){beginDrag(node,pending);return}timer=setTimeout(()=>{if(!pending)return;pending=null;window.homeEditing=true;held=true;render();tg?.HapticFeedback?.impactOccurred('light');setTimeout(()=>held=false,350)},420)});
 document.addEventListener('pointermove',e=>{if(!pending)return;if(!drag){if(Math.hypot(e.clientX-pending.x,e.clientY-pending.y)>10){clearTimeout(timer);pending=null}return}e.preventDefault();drag.x=e.clientX;drag.y=e.clientY},{passive:false});
 document.addEventListener('pointerup',()=>finish());document.addEventListener('pointercancel',()=>finish(true));document.addEventListener('touchmove',e=>{if(drag)e.preventDefault()},{passive:false});document.addEventListener('contextmenu',e=>{if(e.target.closest('[data-widget]'))e.preventDefault()});
 function viewport(){const v=window.visualViewport;document.documentElement.style.setProperty('--visible-height',(v?.height||innerHeight)+'px');document.documentElement.style.setProperty('--keyboard-inset',Math.max(0,innerHeight-(v?.height||innerHeight)-(v?.offsetTop||0))+'px')}
 window.visualViewport?.addEventListener('resize',viewport);window.visualViewport?.addEventListener('scroll',viewport);window.addEventListener('resize',viewport);viewport();

 /* iPhone-like edge swipe: a global, touch-only gesture that yields to vertical
    scrolling and horizontal controls before it acquires the gesture. */
 let edgeSwipe=null;
 function finishEdgeSwipe(commit){const shell=document.querySelector('#shell');if(!shell)return;const distance=commit?innerWidth:0;shell.classList.remove('edge-swipe-active');shell.classList.add('edge-swipe-settling');shell.style.setProperty('--edge-swipe-x',distance+'px');setTimeout(()=>{shell.classList.remove('edge-swipe-settling');shell.style.removeProperty('--edge-swipe-x');if(commit)back()},commit?170:180)}
 document.addEventListener('pointerdown',e=>{
  if(e.pointerType==='mouse'||!e.isPrimary||page==='home'||!navHistory.length||e.clientX>30||e.target.closest('input,textarea,select,dialog,.grid,.chip-row,.segments,.bars,[contenteditable=true]'))return;
  edgeSwipe={x:e.clientX,y:e.clientY,id:e.pointerId,axis:null,dx:0,dy:0,lastX:e.clientX,lastAt:performance.now(),velocity:0};
 },{passive:true});
 document.addEventListener('pointermove',e=>{
  if(!edgeSwipe||e.pointerId!==edgeSwipe.id)return;
  const now=performance.now();edgeSwipe.dx=e.clientX-edgeSwipe.x;edgeSwipe.dy=e.clientY-edgeSwipe.y;edgeSwipe.velocity=(e.clientX-edgeSwipe.lastX)/Math.max(1,now-edgeSwipe.lastAt);edgeSwipe.lastX=e.clientX;edgeSwipe.lastAt=now;
  if(!edgeSwipe.axis&&Math.hypot(edgeSwipe.dx,edgeSwipe.dy)>=9)edgeSwipe.axis=Math.abs(edgeSwipe.dx)>Math.abs(edgeSwipe.dy)*1.2&&edgeSwipe.dx>0?'x':'cancel';
  if(edgeSwipe.axis==='cancel'||edgeSwipe.dx<0){edgeSwipe=null;return}
  if(edgeSwipe.axis==='x'){e.preventDefault();const shell=document.querySelector('#shell');shell?.classList.add('edge-swipe-active');shell?.style.setProperty('--edge-swipe-x',Math.min(edgeSwipe.dx,innerWidth*.82)+'px')}
 },{passive:false});
 document.addEventListener('pointerup',e=>{
  if(!edgeSwipe||e.pointerId!==edgeSwipe.id)return;
  const {dx,axis,velocity}=edgeSwipe;edgeSwipe=null;
  if(axis!=='x')return;const commit=dx>=innerWidth*.22||(dx>34&&velocity>.48);if(commit)tg?.HapticFeedback?.impactOccurred('light');finishEdgeSwipe(commit)
 },{passive:true});
 document.addEventListener('pointercancel',()=>{if(edgeSwipe?.axis==='x')finishEdgeSwipe(false);edgeSwipe=null},{passive:true});
})();
