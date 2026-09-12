// Local read-only UI regression checks. No Telegram session or real user data.
const {chromium}=require(process.env.PLAYWRIGHT_PATH||'C:/Users/tanke/Desktop/NoemaDiz/node_modules/playwright');
const path=require('path');
(async()=>{
 const browser=await chromium.launch({headless:true});const errors=[];
 for(const [width,height] of [[390,844],[414,896],[412,915],[430,932],[375,667],[360,800]]){
  const page=await browser.newPage({viewport:{width,height},deviceScaleFactor:1});
  page.on('pageerror',e=>errors.push(e.message));
  await page.goto('http://127.0.0.1:8091/app');await page.locator('#splash.hidden').waitFor();await page.waitForTimeout(500);
  const viewport=await page.locator('meta[name=viewport]').getAttribute('content');
  if(!/maximum-scale=1/.test(viewport)||!/user-scalable=no/.test(viewport)||!/viewport-fit=cover/.test(viewport))errors.push('Viewport zoom hardening missing');
  if(await page.evaluate(()=>window.visualViewport&&window.visualViewport.scale!==1))errors.push(`Unexpected initial zoom ${width}`);
  for(const route of ['home','tasks','archive','people','settings','chat','budget','reminders']){
   await page.evaluate(route=>go(route),route);
   if(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth))errors.push(`${route} overflows ${width}`);
   if(route==='settings'&&await page.locator('#dock').isVisible())errors.push('Membrane visible in settings');
   if(route==='chat'){
    const box=await page.locator('.composer').boundingBox();if(height-box.y-box.height>40)errors.push(`Composer not bottom anchored ${width}`);
    const sphere=await page.locator('.composer .chat-voice-orb').boundingBox();if(sphere.x+sphere.width>box.x+box.width+1||sphere.x<sphere.width)errors.push(`Chat sphere is not docked on composer right ${width}`);
    const sphereStyle=await page.locator('.composer .chat-voice-orb').evaluate(el=>({width:getComputedStyle(el).width,hasCanvas:Boolean(el.querySelector('canvas')),isLast:el===document.querySelector('.composer').lastElementChild}));
    if(Number.parseFloat(sphereStyle.width)<52||!sphereStyle.hasCanvas||!sphereStyle.isLast)errors.push(`Chat voice sphere is not the compact right-side action ${width}`);
    if(await page.locator('#notice.visible').count())errors.push(`Chat uses a separate status strip ${width}`);
    await page.evaluate(()=>say('Слушаю…'));
    if(await page.locator('#composer-status:not([hidden])').count()!==1||await page.locator('#notice.visible').count())errors.push(`Chat status is not contained by composer ${width}`);
    await page.evaluate(()=>say(''));
    if(Number.parseFloat(await page.locator('#chat-input').evaluate(el=>getComputedStyle(el).fontSize))<16)errors.push(`Chat input can trigger iPhone zoom ${width}`);
   }
   if(route==='home'){
    const dockStyle=await page.locator('#dock').evaluate(el=>({position:getComputedStyle(el).position,background:getComputedStyle(el).backgroundImage,frame:getComputedStyle(el,'::before').display}));
    const boxes=await Promise.all(['.focus-pill','.orb-button','.keyboard-button'].map(selector=>page.locator(`#dock ${selector}`).boundingBox()));
    const centers=boxes.map(box=>box.y+box.height/2);if(dockStyle.position!=='fixed'||dockStyle.background==='none'||dockStyle.frame!=='none'||Math.max(...centers)-Math.min(...centers)>3)errors.push(`Dock overlay alignment failed ${width}`);
   }
   if(width===390)await page.screenshot({path:path.resolve('miniapp',`preview-${route}.png`),fullPage:true});
  }
  await page.evaluate(()=>go('home'));
  const stable=await page.evaluate(()=>{const canvas=document.querySelector('#dock canvas');render();render();return canvas===document.querySelector('#dock canvas')});if(!stable)errors.push('Dock canvas recreated on render');
  if(await page.locator('.all-sections').count()||await page.locator('#header [data-layout],#header [aria-label="Напоминания"]').count())errors.push('Removed controls still present');
  await page.locator('#dock [data-voice]').click();
  if(await page.evaluate(()=>page!=='home'))errors.push('Sphere navigated to chat');
  const first=await page.locator('[data-widget=tasks]').boundingBox(),second=await page.locator('[data-widget=next_event]').boundingBox();
  await page.mouse.move(first.x+first.width/2,first.y+first.height/2);await page.mouse.down();await page.waitForTimeout(500);await page.mouse.move(second.x+second.width/2,second.y+second.height/2,{steps:5});await page.mouse.up();await page.waitForTimeout(400);
  if(await page.locator('.grid>[data-widget]').first().getAttribute('data-widget')!=='next_event')errors.push(`Direct drag failed ${width}`);
  await page.evaluate(()=>go('settings'));if(await page.getByRole('button',{name:'Настроить виджеты',exact:true}).count())errors.push('Widget settings remained in Settings');
  await page.close();
 }
 const touch=await browser.newPage({viewport:{width:390,height:844},hasTouch:true,isMobile:true});
 await touch.goto('http://127.0.0.1:8091/app');await touch.locator('#splash.hidden').waitFor();
 const a=await touch.locator('[data-widget=tasks]').boundingBox(),b=await touch.locator('[data-widget=next_event]').boundingBox(),cdp=await touch.context().newCDPSession(touch);
 await cdp.send('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:[{x:a.x+a.width/2,y:a.y+a.height/2}]});await touch.waitForTimeout(500);
 await cdp.send('Input.dispatchTouchEvent',{type:'touchMove',touchPoints:[{x:b.x+b.width/2,y:b.y+b.height/2}]});
 await cdp.send('Input.dispatchTouchEvent',{type:'touchEnd',touchPoints:[]});await touch.waitForTimeout(400);
 if(await touch.locator('.grid>[data-widget]').first().getAttribute('data-widget')!=='next_event')errors.push('Touch drag failed');
 await browser.close();if(errors.length)throw Error(errors.join('\n'));console.log('6 viewports × 8 screens + zoom guard + overlay dock + compact composer sphere + direct mouse/touch sorting: passed');
})().catch(e=>{console.error(e);process.exitCode=1});
