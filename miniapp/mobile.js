/* Long-press sortable grid: floating card follows the pointer; neighbours FLIP. */
(()=>{
 let pending=null,drag=null,timer=null,frame=null,saving=false;
 const reduced=matchMedia('(prefers-reduced-motion: reduce)').matches;
 function animatePositions(before){for(const node of document.querySelectorAll('.grid>[data-widget]')){const old=before.get(node),now=node.getBoundingClientRect();if(old&&node!==drag?.node&&!reduced){node.getAnimations().forEach(a=>a.cancel());node.animate([{transform:`translate(${old.left-now.left}px,${old.top-now.top}px)`},{transform:'none'}],{duration:220,easing:'cubic-bezier(.2,.8,.2,1)'})}}}
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
 document.addEventListener('pointerdown',e=>{const node=e.target.closest('.grid>[data-widget]');if(!node||busy||saving||e.button!==0)return;pending={node,id:e.pointerId,x:e.clientX,y:e.clientY};timer=setTimeout(()=>{if(!pending)return;const rect=node.getBoundingClientRect(),ghost=node.cloneNode(true);ghost.removeAttribute('data-widget');ghost.removeAttribute('id');ghost.querySelectorAll('[id]').forEach(n=>n.removeAttribute('id'));ghost.className='widget-ghost';ghost.setAttribute('aria-hidden','true');ghost.style.width=rect.width+'px';ghost.style.height=rect.height+'px';document.body.append(ghost);drag={...pending,ghost,offsetX:pending.x-rect.left,offsetY:pending.y-rect.top,lastSwap:0};held=true;node.classList.add('widget-placeholder');document.body.classList.add('sorting');node.setPointerCapture?.(drag.id);tg?.HapticFeedback?.impactOccurred('light');tick()},420)});
 document.addEventListener('pointermove',e=>{if(!pending)return;if(!drag){if(Math.hypot(e.clientX-pending.x,e.clientY-pending.y)>10){clearTimeout(timer);pending=null}return}e.preventDefault();drag.x=e.clientX;drag.y=e.clientY},{passive:false});
 document.addEventListener('pointerup',()=>finish());document.addEventListener('pointercancel',()=>finish(true));document.addEventListener('touchmove',e=>{if(drag)e.preventDefault()},{passive:false});document.addEventListener('contextmenu',e=>{if(e.target.closest('[data-widget]'))e.preventDefault()});
 function viewport(){const v=window.visualViewport;document.documentElement.style.setProperty('--visible-height',(v?.height||innerHeight)+'px');document.documentElement.style.setProperty('--keyboard-inset',Math.max(0,innerHeight-(v?.height||innerHeight)-(v?.offsetTop||0))+'px')}
 window.visualViewport?.addEventListener('resize',viewport);window.visualViewport?.addEventListener('scroll',viewport);window.addEventListener('resize',viewport);viewport();

 /* Deliberate edge swipe returns to the preceding screen without stealing ordinary scrolls. */
 let edgeSwipe=null;
 document.addEventListener('pointerdown',e=>{if(e.pointerType==='mouse'||page==='home'||e.clientX>28||e.target.closest('input,textarea,select,dialog,.grid'))return;edgeSwipe={x:e.clientX,y:e.clientY,id:e.pointerId}}, {passive:true});
 document.addEventListener('pointerup',e=>{if(!edgeSwipe||e.pointerId!==edgeSwipe.id)return;const dx=e.clientX-edgeSwipe.x,dy=e.clientY-edgeSwipe.y;edgeSwipe=null;if(dx>92&&Math.abs(dy)<Math.abs(dx)*.55){tg?.HapticFeedback?.impactOccurred('light');back()}}, {passive:true});
 document.addEventListener('pointercancel',()=>edgeSwipe=null,{passive:true});
})();
