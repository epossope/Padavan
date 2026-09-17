window.NoemaBoot?.assetReady?.('mobile.js','__NOEMA_APP_BUILD_ID__');
/* Long-press sortable grid: floating card follows the pointer; neighbours FLIP. */
(()=>{
 let pending=null,drag=null,timer=null,frame=null,saving=false,editExitClickUntil=0;
 const reduced=matchMedia('(prefers-reduced-motion: reduce)').matches;
 function animatePositions(before){for(const node of document.querySelectorAll('.grid>[data-widget]')){const old=before.get(node),now=node.getBoundingClientRect();if(old&&node!==drag?.node&&!reduced){node.getAnimations().forEach(a=>a.cancel());node.animate([{transform:`translate(${old.left-now.left}px,${old.top-now.top}px)`},{transform:'none'}],{duration:220,easing:'cubic-bezier(.2,.8,.2,1)'})}}}
 function beginDrag(node,sample){const rect=node.getBoundingClientRect(),ghost=node.cloneNode(true);ghost.removeAttribute('data-widget');ghost.removeAttribute('id');ghost.querySelectorAll('[id]').forEach(n=>n.removeAttribute('id'));ghost.querySelectorAll('button').forEach(n=>n.remove());ghost.className='widget-ghost';ghost.setAttribute('aria-hidden','true');ghost.style.width=rect.width+'px';ghost.style.height=rect.height+'px';document.body.append(ghost);drag={...sample,node,ghost,offsetX:sample.x-rect.left,offsetY:sample.y-rect.top,lastSwap:0};held=true;node.classList.add('widget-placeholder');document.body.classList.add('sorting');window.NoemaHaptics?.impact('medium');tick()}
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
 document.addEventListener('pointerdown',e=>{const node=e.target.closest('.grid>[data-widget]');if(!node||busy||saving||e.button!==0||e.target.closest('.home-widget-hide'))return;pending={node,id:e.pointerId,x:e.clientX,y:e.clientY,widget:node.dataset.widget};if(window.homeEditing){beginDrag(node,pending);return}timer=setTimeout(()=>{if(!pending)return;pending=null;window.homeEditing=true;held=true;render();window.NoemaHaptics?.impact('medium');setTimeout(()=>held=false,350)},420)});
 document.addEventListener('pointermove',e=>{if(!pending)return;if(!drag){if(Math.hypot(e.clientX-pending.x,e.clientY-pending.y)>10){clearTimeout(timer);pending=null}return}e.preventDefault();drag.x=e.clientX;drag.y=e.clientY},{passive:false});
 document.addEventListener('pointerup',()=>finish());document.addEventListener('pointercancel',()=>finish(true));document.addEventListener('touchmove',e=>{if(drag)e.preventDefault()},{passive:false});document.addEventListener('contextmenu',e=>{if(e.target.closest('[data-widget]'))e.preventDefault()});
 window.NoemaChatViewport?.bindBrowser?.();

 /* One iOS-style edge-back engine for every secondary screen. It takes over
    only after a horizontal intent; scrolling and every local control keep
    their native gestures. */
 const gestureConstants=Object.freeze({startZoneRatio:.60,intentPx:8,dominance:1.16,commitProgress:.25,velocity:.40,settleMin:150,settleMax:220});
 const pointerInput=typeof window.PointerEvent==='function';
 window.NoemaGestures={constants:gestureConstants,input:pointerInput?'pointer':'touch',startZone(width=window.visualViewport?.width||innerWidth){return width*gestureConstants.startZoneRatio},classify(dx,dy){if(Math.hypot(dx,dy)<gestureConstants.intentPx)return 'pending';return Math.abs(dx)>Math.abs(dy)*gestureConstants.dominance?'horizontal':'vertical'},shouldCommit(dx,width,velocity){return dx>=width*gestureConstants.commitProgress||(Math.abs(dx)>30&&Math.abs(velocity)>gestureConstants.velocity)},settleDuration(remaining,velocity){return reduced?1:Math.round(Math.min(gestureConstants.settleMax,Math.max(gestureConstants.settleMin,remaining/Math.max(.65,Math.abs(velocity)*1.45))))}};
 let edgeSwipe=null,edgeSwipeFinishing=false,edgeSwipeFrame=0,edgeSwipeTimer=0;
 function edgeSwipeStartZone(width){return width*gestureConstants.startZoneRatio}
 function canSwipeBack(){return page!=='home'||navHistory.length>0||[...document.querySelectorAll('dialog')].some(dialog=>dialog.open)}
 function hasTextSelection(){const selection=window.getSelection?.();return Boolean(selection&&selection.type==='Range'&&selection.toString())}
 function blocksEdgeSwipe(target){return hasTextSelection()||Boolean(target.closest('input,textarea,select,button,a,.chip-row,.segments,.voice-slider,[type=range],[contenteditable=true],[draggable=true],[data-no-swipe-back],.home-grid.editing'))}
 function clampedSwipeDistance(distance,width){const limit=width*.9,positive=Math.max(0,distance);return positive<=limit?positive:limit+(positive-limit)*.16}
 function renderEdgeSwipe(){edgeSwipeFrame=0;const swipe=edgeSwipe;if(!swipe||swipe.axis!=='x')return;swipe.rendered=clampedSwipeDistance(swipe.dx,swipe.width);swipe.shell.classList.add('edge-swipe-active');swipe.shell.style.setProperty('--edge-swipe-x',swipe.rendered+'px')}
 function scheduleEdgeSwipe(){if(!edgeSwipeFrame)edgeSwipeFrame=requestAnimationFrame(renderEdgeSwipe)}
 function clearEdgeSwipe(shell){shell.classList.remove('edge-swipe-active','edge-swipe-settling','edge-swipe-reset');shell.style.removeProperty('--edge-swipe-x');shell.style.removeProperty('--edge-swipe-progress');shell.style.removeProperty('--edge-swipe-duration')}
 function finishEdgeSwipe(commit,velocity=0){
  if(edgeSwipeFinishing)return;
  const swipe=edgeSwipe;if(!swipe)return;
  if(edgeSwipeFrame){cancelAnimationFrame(edgeSwipeFrame);edgeSwipeFrame=0;renderEdgeSwipe()}
  edgeSwipeFinishing=true;
  const current=Math.max(0,swipe.rendered||0),destination=commit?swipe.width:0,remaining=Math.abs(destination-current),duration=window.NoemaGestures.settleDuration(remaining,velocity),shell=swipe.shell;
  shell.style.setProperty('--edge-swipe-duration',duration+'ms');shell.classList.remove('edge-swipe-active');shell.classList.add('edge-swipe-settling');shell.style.setProperty('--edge-swipe-x',destination+'px');
  let settled=false;const complete=()=>{if(settled)return;settled=true;clearTimeout(edgeSwipeTimer);shell.removeEventListener('transitionend',onEnd);if(!commit){clearEdgeSwipe(shell);edgeSwipeFinishing=false;return}shell.classList.remove('edge-swipe-settling');shell.classList.add('edge-swipe-reset');window.NoemaBack?.();requestAnimationFrame(()=>{clearEdgeSwipe(shell);edgeSwipeFinishing=false})};
  const onEnd=event=>{if(event.target===shell&&event.propertyName==='transform')complete()};shell.addEventListener('transitionend',onEnd);edgeSwipeTimer=setTimeout(complete,duration+80)
 }
 function cancelEdgeSwipe(){if(edgeSwipe?.axis==='x')finishEdgeSwipe(false,edgeSwipe.velocity);else if(edgeSwipeFrame){cancelAnimationFrame(edgeSwipeFrame);edgeSwipeFrame=0}edgeSwipe=null}
 function beginEdgeSwipe(id,x,y,target,source){
  const shell=document.querySelector('#shell'),width=window.visualViewport?.width||innerWidth;
  if(!shell||edgeSwipe||edgeSwipeFinishing||document.body.classList.contains('sorting')||!canSwipeBack()||x>edgeSwipeStartZone(width)||blocksEdgeSwipe(target))return false;
  const now=performance.now();edgeSwipe={x,y,id,source,shell,width,axis:null,captured:false,dx:0,dy:0,lastX:x,lastAt:now,startedAt:now,velocity:0,rendered:0};return true
 }
 function moveEdgeSwipe(id,x,y,event){
  const swipe=edgeSwipe;if(!swipe||id!==swipe.id)return false;
  if(hasTextSelection()){cancelEdgeSwipe();return false}
  const now=performance.now(),instant=(x-swipe.lastX)/Math.max(1,now-swipe.lastAt);swipe.dx=x-swipe.x;swipe.dy=y-swipe.y;swipe.velocity=swipe.velocity*.7+instant*.3;swipe.lastX=x;swipe.lastAt=now;
  if(!swipe.axis){const intent=window.NoemaGestures.classify(swipe.dx,swipe.dy);if(intent==='horizontal')swipe.axis=swipe.dx>0?'x':'cancel';else if(intent==='vertical')swipe.axis='cancel'}
  if(swipe.axis==='cancel'||swipe.dx<0){cancelEdgeSwipe();return false}
  if(swipe.axis==='x'){event.preventDefault();scheduleEdgeSwipe();return true}return false
 }
 function endEdgeSwipe(id){
  const swipe=edgeSwipe;if(!swipe||id!==swipe.id)return false;
  const {dx,axis,velocity,startedAt,width}=swipe;
  if(axis!=='x'){edgeSwipe=null;return false}const averageVelocity=dx/Math.max(1,performance.now()-startedAt),releaseVelocity=Math.max(velocity,averageVelocity*.7),commit=window.NoemaGestures.shouldCommit(dx,width,releaseVelocity);if(commit)window.NoemaHaptics?.impact('light');finishEdgeSwipe(commit,releaseVelocity);edgeSwipe=null;return commit
 }
 function cancelInputSwipe(id){if(edgeSwipe?.id!==id)return;if(edgeSwipe.axis==='x')finishEdgeSwipe(false,0);edgeSwipe=null}
 if(pointerInput){
  document.addEventListener('pointerdown',e=>{if(e.pointerType==='mouse'||e.isPrimary===false)return;beginEdgeSwipe(`pointer:${e.pointerId}`,e.clientX,e.clientY,e.target,'pointer')},{passive:true,capture:true});
  document.addEventListener('pointermove',e=>{if(moveEdgeSwipe(`pointer:${e.pointerId}`,e.clientX,e.clientY,e)&&edgeSwipe&&!edgeSwipe.captured){try{e.target.setPointerCapture?.(e.pointerId)}catch{}edgeSwipe.captured=true}},{passive:false,capture:true});
  document.addEventListener('pointerup',e=>endEdgeSwipe(`pointer:${e.pointerId}`),{passive:true,capture:true});
  document.addEventListener('pointercancel',e=>cancelInputSwipe(`pointer:${e.pointerId}`),{passive:true,capture:true});
 }else{
  document.addEventListener('touchstart',e=>{if(edgeSwipe||e.touches.length!==1)return;const touch=e.touches[0];beginEdgeSwipe(`touch:${touch.identifier}`,touch.clientX,touch.clientY,e.target,'touch')},{passive:true,capture:true});
  document.addEventListener('touchmove',e=>{if(e.touches.length!==1){cancelEdgeSwipe();return}const touch=[...e.touches].find(item=>`touch:${item.identifier}`===edgeSwipe?.id);if(touch)moveEdgeSwipe(edgeSwipe.id,touch.clientX,touch.clientY,e)},{passive:false,capture:true});
  document.addEventListener('touchend',e=>{const touch=[...e.changedTouches].find(item=>`touch:${item.identifier}`===edgeSwipe?.id);if(touch)endEdgeSwipe(edgeSwipe.id)},{passive:true,capture:true});
  document.addEventListener('touchcancel',()=>cancelEdgeSwipe(),{passive:true,capture:true});
 }

})();
