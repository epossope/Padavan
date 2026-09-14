/* Long-press sortable grid: floating card follows the pointer; neighbours FLIP. */
(()=>{
 let pending=null,drag=null,timer=null,frame=null,saving=false,editExitClickUntil=0;
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
 function exitHomeEditMode(){if(!window.homeEditing||drag||saving)return false;clearTimeout(timer);pending=null;window.homeEditing=false;held=true;editExitClickUntil=performance.now()+360;render();setTimeout(()=>held=false,120);return true}
 window.NoemaHomeEdit={exit:exitHomeEditMode,isDragging:()=>Boolean(drag)};
 document.addEventListener('click',e=>{if(performance.now()<editExitClickUntil){e.preventDefault();e.stopImmediatePropagation()}},{capture:true});
 document.addEventListener('pointerdown',e=>{if(!window.homeEditing||drag||saving||e.button!==0)return;if(!e.target.closest('#content')||e.target.closest('.home-grid>[data-widget],.home-edit-head,.home-hidden,[data-home-done],[data-home-hide],[data-home-show]'))return;e.preventDefault();e.stopPropagation();exitHomeEditMode()},{capture:true});
 document.addEventListener('keydown',e=>{if(e.key==='Escape')exitHomeEditMode()});
 document.addEventListener('pointerdown',e=>{const node=e.target.closest('.grid>[data-widget]');if(!node||busy||saving||e.button!==0||e.target.closest('.home-widget-hide'))return;pending={node,id:e.pointerId,x:e.clientX,y:e.clientY,widget:node.dataset.widget};if(window.homeEditing){beginDrag(node,pending);return}timer=setTimeout(()=>{if(!pending)return;pending=null;window.homeEditing=true;held=true;render();tg?.HapticFeedback?.impactOccurred('light');setTimeout(()=>held=false,350)},420)});
 document.addEventListener('pointermove',e=>{if(!pending)return;if(!drag){if(Math.hypot(e.clientX-pending.x,e.clientY-pending.y)>10){clearTimeout(timer);pending=null}return}e.preventDefault();drag.x=e.clientX;drag.y=e.clientY},{passive:false});
 document.addEventListener('pointerup',()=>finish());document.addEventListener('pointercancel',()=>finish(true));document.addEventListener('touchmove',e=>{if(drag)e.preventDefault()},{passive:false});document.addEventListener('contextmenu',e=>{if(e.target.closest('[data-widget]'))e.preventDefault()});
 window.NoemaChatViewport?.bindBrowser?.();

 /* iPhone-like edge swipe: a global, touch-only gesture that yields to vertical
    scrolling and horizontal controls before it acquires the gesture. */
 let edgeSwipe=null,edgeSwipeFinishing=false,edgeSwipeFrame=0,edgeSwipeTimer=0;
 function edgeSwipeStartZone(){return innerWidth*.5}
 function canSwipeBack(){return page!=='home'||navHistory.length>0||[...document.querySelectorAll('dialog')].some(dialog=>dialog.open)}
 function blocksEdgeSwipe(target){return Boolean(target.closest('input,textarea,select,dialog form,.chip-row,.segments,.bars,.trend,.distribution-body,.voice-slider,[type=range],[contenteditable=true],[draggable=true],[data-no-swipe-back],.home-grid.editing'))}
 function clampedSwipeDistance(distance){const limit=innerWidth*.9,positive=Math.max(0,distance);return positive<=limit?positive:limit+(positive-limit)*.18}
 function renderEdgeSwipe(){edgeSwipeFrame=0;if(!edgeSwipe||edgeSwipe.axis!=='x')return;const shell=document.querySelector('#shell');if(!shell)return;edgeSwipe.rendered=clampedSwipeDistance(edgeSwipe.dx);shell.classList.add('edge-swipe-active');shell.style.setProperty('--edge-swipe-x',edgeSwipe.rendered+'px');shell.style.setProperty('--edge-swipe-progress',Math.min(1,edgeSwipe.rendered/innerWidth))}
 function scheduleEdgeSwipe(){if(!edgeSwipeFrame)edgeSwipeFrame=requestAnimationFrame(renderEdgeSwipe)}
 function finishEdgeSwipe(commit,velocity=0){
  if(edgeSwipeFinishing)return;
  const shell=document.querySelector('#shell');if(!shell)return;
  if(edgeSwipeFrame){cancelAnimationFrame(edgeSwipeFrame);edgeSwipeFrame=0;renderEdgeSwipe()}
  edgeSwipeFinishing=true;
  const current=Math.max(0,edgeSwipe?.rendered||0),distance=commit?innerWidth:0,remaining=Math.abs(distance-current);
  const projectedSpeed=Math.max(.55,Math.abs(velocity)*1.35),duration=Math.round(Math.min(commit?310:250,Math.max(commit?170:150,remaining/projectedSpeed)));
  shell.style.setProperty('--edge-swipe-duration',duration+'ms');shell.classList.remove('edge-swipe-active');shell.classList.add('edge-swipe-settling');shell.style.setProperty('--edge-swipe-x',distance+'px');shell.style.setProperty('--edge-swipe-progress',commit?1:0);
  let settled=false;const complete=()=>{if(settled)return;settled=true;clearTimeout(edgeSwipeTimer);shell.removeEventListener('transitionend',onEnd);shell.classList.remove('edge-swipe-settling');if(commit){shell.classList.add('edge-swipe-reset');window.NoemaBack?.();shell.style.removeProperty('--edge-swipe-x');shell.style.removeProperty('--edge-swipe-progress');void shell.offsetWidth;shell.classList.remove('edge-swipe-reset')}else{shell.style.removeProperty('--edge-swipe-x');shell.style.removeProperty('--edge-swipe-progress')}shell.style.removeProperty('--edge-swipe-duration');edgeSwipeFinishing=false};
  const onEnd=event=>{if(event.target===shell&&event.propertyName==='transform')complete()};shell.addEventListener('transitionend',onEnd);edgeSwipeTimer=setTimeout(complete,duration+80)
 }
 function cancelEdgeSwipe(){if(edgeSwipe?.axis==='x')finishEdgeSwipe(false,edgeSwipe.velocity);else if(edgeSwipeFrame){cancelAnimationFrame(edgeSwipeFrame);edgeSwipeFrame=0}edgeSwipe=null}
 document.addEventListener('pointerdown',e=>{
  if(edgeSwipeFinishing||document.body.classList.contains('sorting')||e.pointerType==='mouse'||!e.isPrimary||!canSwipeBack()||e.clientX>edgeSwipeStartZone()||blocksEdgeSwipe(e.target))return;
  edgeSwipe={x:e.clientX,y:e.clientY,id:e.pointerId,axis:null,captured:false,dx:0,dy:0,lastX:e.clientX,lastAt:performance.now(),startedAt:performance.now(),velocity:0,rendered:0};
 },{passive:true,capture:true});
 document.addEventListener('pointermove',e=>{
  if(!edgeSwipe||e.pointerId!==edgeSwipe.id)return;
  const now=performance.now(),instant=(e.clientX-edgeSwipe.lastX)/Math.max(1,now-edgeSwipe.lastAt);edgeSwipe.dx=e.clientX-edgeSwipe.x;edgeSwipe.dy=e.clientY-edgeSwipe.y;edgeSwipe.velocity=edgeSwipe.velocity*.68+instant*.32;edgeSwipe.lastX=e.clientX;edgeSwipe.lastAt=now;
  if(!edgeSwipe.axis&&Math.hypot(edgeSwipe.dx,edgeSwipe.dy)>=7)edgeSwipe.axis=Math.abs(edgeSwipe.dx)>Math.abs(edgeSwipe.dy)*1.2&&edgeSwipe.dx>0?'x':'cancel';
  if(edgeSwipe.axis==='cancel'||edgeSwipe.dx<0){cancelEdgeSwipe();return}
  if(edgeSwipe.axis==='x'){e.preventDefault();if(!edgeSwipe.captured){e.target.setPointerCapture?.(e.pointerId);edgeSwipe.captured=true}scheduleEdgeSwipe()}
 },{passive:false,capture:true});
 document.addEventListener('pointerup',e=>{
  if(!edgeSwipe||e.pointerId!==edgeSwipe.id)return;
  const {dx,axis,velocity,startedAt}=edgeSwipe;
  if(axis!=='x'){edgeSwipe=null;return}const averageVelocity=dx/Math.max(1,performance.now()-startedAt),releaseVelocity=Math.max(velocity,averageVelocity*.7),commit=dx>=innerWidth*.23||(dx>30&&releaseVelocity>.34);if(commit)tg?.HapticFeedback?.impactOccurred('light');finishEdgeSwipe(commit,releaseVelocity);edgeSwipe=null
 },{passive:true,capture:true});
 document.addEventListener('pointercancel',cancelEdgeSwipe,{passive:true,capture:true});
})();
