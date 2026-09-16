// Deterministic mobile UI/UX regression proof. Preview mode only; no Telegram/user data.
const {chromium}=require(process.env.PLAYWRIGHT_PATH||'C:/Users/tanke/Desktop/NoemaDiz/node_modules/playwright');
const fs=require('fs');
const path=require('path');

const baseUrl=process.env.NOEMA_TEST_URL||'http://127.0.0.1:8091/app';
const proofDir=path.resolve('ui-proof','ui-ux-pass-v1.6');
const widths=[320,360,375,390,414,430];

function check(condition,message){if(!condition)throw new Error(message)}

async function open(browser,width=390){
 const page=await browser.newPage({viewport:{width,height:844},deviceScaleFactor:1,hasTouch:true,isMobile:true});
 await page.goto(baseUrl,{waitUntil:'domcontentloaded'});
 await page.locator('#splash.hidden').waitFor();
 return page;
}

(async()=>{
 fs.mkdirSync(proofDir,{recursive:true});
 const browser=await chromium.launch({headless:true});
 const viewportResults=[];
 for(const width of widths){
  const page=await open(browser,width);
  for(const screen of ['home','tasks','notes','archive','people','budget','settings','chat']){
   await page.evaluate(value=>{if(value==='home'){page='home';navHistory=[];render()}else go(value)},screen);
   await page.waitForTimeout(40);
   const layout=await page.evaluate(()=>({
    page:document.querySelector('#shell').dataset.page,
    overflow:Math.max(document.documentElement.scrollWidth,document.body.scrollWidth)-innerWidth,
    headerButtons:document.querySelectorAll('#header button').length,
    title:document.querySelector('#header h1')?.textContent||''
   }));
   check(layout.overflow<=1,`${width}px ${screen}: horizontal overflow ${layout.overflow}px`);
   check(layout.headerButtons===(screen==='home'?1:0),`${width}px ${screen}: unexpected header controls`);
  }
  viewportResults.push({width,ok:true});
  await page.close();
 }

 const page=await open(browser,390);
 await page.screenshot({path:path.join(proofDir,'home-390.png'),fullPage:true});

 await page.evaluate(()=>go('chat'));
 await page.waitForTimeout(80);
 const composer=await page.evaluate(()=>{
  const field=document.querySelector('.chat-input-area'),orb=document.querySelector('.chat-voice-orb'),canvas=orb.querySelector('canvas'),textarea=document.querySelector('#chat-input'),fieldRect=field.getBoundingClientRect(),orbRect=orb.getBoundingClientRect();
  return {borderRight:getComputedStyle(field).borderRightWidth,fieldRight:fieldRect.right,orbCenter:orbRect.left+orbRect.width/2,canvasBackground:getComputedStyle(canvas).backgroundImage,orbBackground:getComputedStyle(orb).backgroundImage,textareaRightPadding:getComputedStyle(textarea).paddingRight};
 });
 check(composer.borderRight==='0px','chat field must remain visually open at the sphere');
 check(composer.fieldRight<composer.orbCenter,'chat field border must stop before the sphere centre');
 check(composer.canvasBackground==='none'&&composer.orbBackground==='none','sphere wrapper/canvas must be transparent');

 const chatScroll=await page.evaluate(async()=>{
  const inner=document.querySelector('.chat-inner'),thread=document.querySelector('.chat');
  for(let index=0;index<35;index++){const bubble=document.createElement('div');bubble.className=`bubble ${index%2?'user':'assistant'}`;bubble.dataset.messageKey=`qa-${index}`;bubble.textContent=`Сообщение ${index+1} — проверка стабильного положения чата.`;inner.insertBefore(bubble,inner.lastElementChild)}
  window.NoemaChatScroll.followingBottom=true;window.NoemaChatScroll.incoming({newMessage:true});await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
  const followed=Math.abs(thread.scrollHeight-thread.clientHeight-thread.scrollTop)<=106;
  thread.scrollTop=0;thread.dispatchEvent(new Event('scroll'));await new Promise(resolve=>requestAnimationFrame(resolve));const before=thread.scrollTop;
  const next=document.createElement('div');next.className='bubble assistant';next.dataset.messageKey='qa-new';next.textContent='Новый ответ не должен вырывать пользователя из истории.';inner.insertBefore(next,inner.lastElementChild);window.NoemaChatScroll.incoming({newMessage:true});await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
  return {followed,historyBefore:before,historyAfter:thread.scrollTop,latestHidden:document.querySelector('[data-chat-latest]').hidden};
 });
 check(chatScroll.followed,'chat did not follow latest while already at bottom');
 check(Math.abs(chatScroll.historyAfter-chatScroll.historyBefore)<=1,'chat forced the reader away from history');
 check(!chatScroll.latestHidden,'latest affordance must be visible while reading history');
 await page.screenshot({path:path.join(proofDir,'chat-390.png')});

 for(const screen of ['budget','notes','archive']){
  await page.evaluate(value=>go(value),screen);await page.waitForTimeout(50);
  await page.screenshot({path:path.join(proofDir,`${screen}-390.png`),fullPage:true});
 }
 await page.evaluate(()=>go('tasks'));
 await page.locator('[data-form="task"]').click();
 await page.waitForTimeout(50);
 const modalFocus=await page.evaluate(()=>({tag:document.activeElement?.tagName,id:document.activeElement?.id,dialog:document.activeElement?.matches('dialog')}));
 check(modalFocus.dialog===true&&modalFocus.id!=='cancel','modal close control received unwanted autofocus');
 await page.screenshot({path:path.join(proofDir,'edit-task-390.png')});
 await page.evaluate(()=>document.querySelector('#editor').close());

 await page.evaluate(()=>{page='home';navHistory=[];render();window.homeEditing=true;render()});
 await page.locator('#content').dispatchEvent('pointerdown',{pointerType:'touch',isPrimary:true,pointerId:71,button:0,clientX:8,clientY:500});
 check(await page.evaluate(()=>window.homeEditing===false),'blank tap did not finish Home edit mode');

 await page.evaluate(()=>go('settings'));
 const voices=await page.locator('#voice-choice option').allTextContents();
 check(voices.some(value=>value.includes('Мужской'))&&voices.some(value=>value.includes('Женский')),'male/female voice choices are missing');

 await page.evaluate(()=>{page='tasks';navHistory=['home'];render()});
 await page.evaluate(()=>{
  const target=document.elementFromPoint(40,360);
  const fire=(type,x,y)=>target.dispatchEvent(new PointerEvent(type,{bubbles:true,cancelable:true,pointerType:'touch',isPrimary:true,pointerId:91,button:0,clientX:x,clientY:y}));
  fire('pointerdown',28,360);fire('pointermove',118,362);fire('pointermove',205,364);fire('pointerup',205,364);
 });
 await page.waitForTimeout(420);
 check(await page.evaluate(()=>page==='home'),'edge swipe did not invoke shared back action');

 const nativeContext=await browser.newContext({viewport:{width:390,height:844},deviceScaleFactor:1,hasTouch:true,isMobile:true});
 await nativeContext.route('**/telegram-web-app.js',route=>route.fulfill({contentType:'application/javascript',body:`
  window.__backCalls={show:0,hide:0};
  window.Telegram={WebApp:{initData:'',safeAreaInset:{top:52,right:0,bottom:0,left:0},contentSafeAreaInset:{top:60,right:0,bottom:0,left:0},
   BackButton:{show(){window.__backCalls.show++},hide(){window.__backCalls.hide++},onClick(handler){this.handler=handler}},
   HapticFeedback:{impactOccurred(){}},ready(){},expand(){},requestFullscreen(){},isVersionAtLeast(){return true},setHeaderColor(){},setBackgroundColor(){},onEvent(){}}};
 `}));
 const nativePage=await nativeContext.newPage();
 await nativePage.goto(baseUrl,{waitUntil:'domcontentloaded'});await nativePage.locator('#splash.hidden').waitFor();
 const safeTop=await nativePage.locator('#header').evaluate(node=>node.getBoundingClientRect().top);
 await nativePage.evaluate(()=>go('notes'));
 const backCalls=await nativePage.evaluate(()=>window.__backCalls);
 check(safeTop>=67,`Telegram safe area not respected: ${safeTop}px`);
 check(backCalls.show>0,'Telegram BackButton was not shown for internal history');
 await nativeContext.close();

 console.log(JSON.stringify({viewports:viewportResults,composer,chatScroll,modalFocus,voices,safeTop,backCalls},null,2));
 await page.close();
 await browser.close();
})().catch(error=>{console.error(error);process.exitCode=1});
