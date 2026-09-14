const {chromium}=require(process.env.PLAYWRIGHT_PATH||'C:/Users/tanke/Desktop/NoemaDiz/node_modules/playwright');
const http=require('http');
const fs=require('fs');
const path=require('path');

const root=path.resolve(__dirname,'..','miniapp');
const longHistory=Array.from({length:70},(_,index)=>({message_id:index+1,role:index%2?'assistant':'user',content:`Сообщение ${index+1}. ${'Текст для проверки длинной истории. '.repeat(3)}`,created_at:`2026-09-14T10:${String(index%60).padStart(2,'0')}:00+00:00`}));
const shortHistory=[{message_id:101,role:'user',content:'Короткий вопрос',created_at:'2026-09-14T11:00:00+00:00'},{message_id:102,role:'assistant',content:'Короткий ответ',created_at:'2026-09-14T11:00:01+00:00'}];
function state(history=longHistory){return {day:'2026-09-14',plan:{tasks:[],reminders:[]},tasks:[],reminders:[],notes:[],people:[],expenses:[],files:[],history,rules:[],settings:{mode:'text',timezone:'Europe/Moscow',home_widgets:[],experimental_realtime:false,experimental_realtime_available:false,experimental_wake_enabled:false,telemetry_enabled:false,voice_runtime:{tts_provider:'edge',tts_fallback_provider:'browser',tts_male_voice:'male',tts_female_voice:'female'},voice_preferences:{gender:'female',voice:'female',speed:1,pitch:1,volume:1,supports_pitch:true,supports_volume:true},briefing:{enabled:false,time:'08:30',topics:'',city:''}}}}
function sendJson(response,data){response.writeHead(200,{'Content-Type':'application/json','Cache-Control':'no-store'});response.end(JSON.stringify({ok:true,data}))}
function stream(response,events){response.writeHead(200,{'Content-Type':'application/x-ndjson; charset=utf-8','Cache-Control':'no-store'});let index=0;const next=()=>{if(index===events.length)return response.end();const [delay,event]=events[index++];setTimeout(()=>{response.write(JSON.stringify(event)+'\n');next()},delay)};next()}
const server=http.createServer((request,response)=>{
 const url=new URL(request.url,'http://127.0.0.1');
 if(url.pathname==='/app'||url.pathname==='/app/')return fs.createReadStream(path.join(root,'index.html')).pipe(response);
 if(url.pathname.startsWith('/app/assets/')){const target=path.join(root,url.pathname.slice('/app/assets/'.length));const type=target.endsWith('.js')?'application/javascript':target.endsWith('.css')?'text/css':target.endsWith('.json')?'application/json':target.endsWith('.ttf')?'font/ttf':'application/octet-stream';response.writeHead(200,{'Content-Type':type});return fs.createReadStream(target).pipe(response)}
 if(url.pathname==='/api/v1/miniapp/chat-stream'){
  let body='';request.on('data',chunk=>body+=chunk);request.on('end',()=>{const text=JSON.parse(body).text;
   if(text==='tail')return stream(response,[[0,{type:'job',job_id:'tail'}],[0,{type:'state',state:'REQUESTING',text:'Думаю…'}],[20,{type:'delta',text:'Начало'}],[10,{type:'done',text:'Начало и восстановленный хвост',canonical_user_message_id:201,canonical_message_id:202}]]);
   if(text==='status')return stream(response,[[0,{type:'state',state:'REQUESTING',text:'Думаю…'}],[100,{type:'delta',text:'Первый токен'}],[20,{type:'done',text:'Первый токен',canonical_user_message_id:203,canonical_message_id:204}]]);
   if(text==='tool')return stream(response,[[0,{type:'state',state:'REQUESTING',text:'Думаю…'}],[40,{type:'tool',name:'get_today_plan',ok:true}],[0,{type:'state',state:'TOOL',text:'Проверяю задачи…',tool:'get_today_plan'}],[80,{type:'delta',text:'Задач нет'}],[10,{type:'done',text:'Задач нет',canonical_user_message_id:205,canonical_message_id:206}]]);
   return stream(response,[[0,{type:'state',state:'REQUESTING',text:'Думаю…'}],[10,{type:'delta',text:'Поток '}],[20,{type:'delta',text:'продолжается'}],[10,{type:'done',text:'Поток продолжается',canonical_user_message_id:207,canonical_message_id:208}]]);
  });return;
 }
 if(url.pathname==='/api/v1/miniapp'){
  let body='';request.on('data',chunk=>body+=chunk);request.on('end',()=>{const payload=JSON.parse(body||'{}');if(payload.action==='state')return sendJson(response,state(longHistory));if(payload.action==='budget')return sendJson(response,{items:[],currency_totals:{}});if(payload.action==='weather')return sendJson(response,{city:'',current:null});return sendJson(response,{ok:true})});return;
 }
 response.writeHead(404);response.end();
});

function check(value,message){if(!value)throw new Error(message)}
const raf=page=>page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));

(async()=>{
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));const base=`http://127.0.0.1:${server.address().port}`;
 const browser=await chromium.launch({headless:true});const context=await browser.newContext({viewport:{width:390,height:844},deviceScaleFactor:1,hasTouch:true,isMobile:true});
 await context.route('**/telegram-web-app.js',route=>route.fulfill({contentType:'application/javascript',body:`
  window.__tgEvents={};window.__tgSequence=[];
  window.__vv={height:844,offsetTop:0,handlers:{},addEventListener(name,handler){(this.handlers[name]||(this.handlers[name]=[])).push(handler)},dispatch(name){for(const handler of this.handlers[name]||[])handler({type:name})}};
  Object.defineProperty(window,'visualViewport',{configurable:true,value:window.__vv});
  window.Telegram={WebApp:{initData:'signed',viewportHeight:844,isFullscreen:false,safeAreaInset:{top:18,right:0,bottom:8,left:0},contentSafeAreaInset:{top:26,right:0,bottom:12,left:0},ready(){window.__tgSequence.push('ready')},expand(){window.__tgSequence.push('expand')},requestFullscreen(){window.__tgSequence.push('requestFullscreen')},isVersionAtLeast(){return true},onEvent(name,handler){window.__tgEvents[name]=handler;window.__tgSequence.push('listen:'+name)},setHeaderColor(){},setBackgroundColor(){},BackButton:{show(){},hide(){},onClick(handler){this.handler=handler}},HapticFeedback:{impactOccurred(){}}}};
 `}));
 const page=await context.newPage();await page.goto(`${base}/app?screen=chat`,{waitUntil:'domcontentloaded'});await page.locator('#splash.hidden').waitFor();

 const opened=await page.evaluate(()=>{const chat=document.querySelector('.chat');return {ready:chat.classList.contains('chat-scroll-ready'),visible:getComputedStyle(chat).visibility,bottom:chat.scrollHeight-chat.scrollTop-chat.clientHeight,keys:[...document.querySelectorAll('.bubble')].map(node=>node.dataset.messageKey),sequence:window.__tgSequence,diagnostics:window.NoemaViewportDiagnostics}});
 check(opened.ready&&opened.visible==='visible'&&opened.bottom<=1,'A: long chat was exposed before settling at latest');
 check(opened.keys[0]==='message-1'&&opened.keys.at(-1)==='message-70','G: history did not use stable canonical IDs');
 check(opened.sequence.indexOf('listen:fullscreenChanged')<opened.sequence.indexOf('requestFullscreen'),'O: fullscreen listeners were registered too late');

 const short=await page.evaluate(async history=>{data.history=history;render();await window.NoemaChatScroll.whenSettled();const chat=document.querySelector('.chat'),first=document.querySelector('.bubble'),last=[...document.querySelectorAll('.bubble')].at(-1);return {firstTop:first.getBoundingClientRect().top,chatTop:chat.getBoundingClientRect().top,lastBottom:last.getBoundingClientRect().bottom,chatBottom:chat.getBoundingClientRect().bottom}},shortHistory);
 check(short.firstTop-short.chatTop>100&&short.chatBottom-short.lastBottom<30,'B: short chat is not bottom-aligned');

 await page.evaluate(async history=>{data.history=history;render();await window.NoemaChatScroll.whenSettled()},longHistory);await raf(page);
 await page.evaluate(()=>streamChat('normal'));await raf(page);
 check(await page.evaluate(()=>document.querySelector('.chat').scrollHeight-document.querySelector('.chat').scrollTop-document.querySelector('.chat').clientHeight<=2),'C: bottom-following stream drifted');

 const reading=await page.evaluate(async()=>{const chat=document.querySelector('.chat');chat.scrollTop=0;chat.dispatchEvent(new Event('scroll'));await new Promise(resolve=>requestAnimationFrame(resolve));const before=chat.scrollTop;await streamChat('normal');await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));return {before,after:chat.scrollTop,state:window.NoemaChatScroll.state}});
 check(Math.abs(reading.after-reading.before)<=1&&reading.state==='READING_HISTORY','D: streaming forced a reader back to latest');

 const keyboardBottom=await page.evaluate(async()=>{const chat=document.querySelector('.chat');window.NoemaChatScroll.scrollToBottom();await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));const pre={state:window.NoemaChatScroll.state,gap:chat.scrollHeight-chat.scrollTop-chat.clientHeight};window.__vv.height=560;window.__vv.dispatch('resize');await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));const open=chat.scrollHeight-chat.scrollTop-chat.clientHeight,debug={pre,state:window.NoemaChatScroll.state,scrollTop:chat.scrollTop,scrollHeight:chat.scrollHeight,clientHeight:chat.clientHeight,visible:getComputedStyle(document.documentElement).getPropertyValue('--visible-height')};window.__vv.height=844;window.__vv.dispatch('resize');await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));return {open,closed:chat.scrollHeight-chat.scrollTop-chat.clientHeight,debug}});
 check(keyboardBottom.open<=2&&keyboardBottom.closed<=2,`E: keyboard resize broke bottom following ${JSON.stringify(keyboardBottom)}`);

 const keyboardHistory=await page.evaluate(async()=>{const chat=document.querySelector('.chat');chat.scrollTop=Math.max(1,chat.scrollHeight*.25);chat.dispatchEvent(new Event('scroll'));await new Promise(resolve=>requestAnimationFrame(resolve));const snap=window.NoemaChatScroll.snapshot(),node=document.querySelector(`[data-message-key="${snap.anchorKey}"]`),before=node.getBoundingClientRect().top-chat.getBoundingClientRect().top;window.__vv.height=570;window.__vv.dispatch('resize');await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));const same=document.querySelector(`[data-message-key="${snap.anchorKey}"]`),opened=same.getBoundingClientRect().top-chat.getBoundingClientRect().top;window.__vv.height=844;window.__vv.dispatch('resize');await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));const closed=same.getBoundingClientRect().top-chat.getBoundingClientRect().top;return {key:snap.anchorKey,before,opened,closed,state:window.NoemaChatScroll.state}});
 check(keyboardHistory.key&&Math.abs(keyboardHistory.before-keyboardHistory.opened)<=2&&Math.abs(keyboardHistory.before-keyboardHistory.closed)<=2&&keyboardHistory.state==='READING_HISTORY','F: keyboard resize lost the reading anchor');

 await page.evaluate(()=>window.NoemaChatScroll.scrollToBottom());await raf(page);
 const tail=await page.evaluate(async()=>{const before=document.querySelectorAll('.bubble.assistant').length,answer=await streamChat('tail',{userText:'tail'}),after=document.querySelectorAll('.bubble.assistant').length,node=document.querySelector('[data-message-key="message-202"]');return {answer,before,after,text:node?.textContent,key:node?.dataset.messageKey}});
 check(tail.answer==='Начало и восстановленный хвост'&&tail.text===tail.answer&&tail.after===tail.before+1&&tail.key==='message-202','H: done.text did not repair one existing bubble');

 await page.evaluate(()=>{window.__statusPromise=streamChat('status',{userText:'status'})});await page.waitForFunction(()=>document.querySelector('#composer-status')?.textContent==='Думаю…');await page.waitForFunction(()=>document.querySelector('.bubble.streaming')?.textContent.includes('Первый токен'));const firstTokenCleared=await page.evaluate(()=>document.querySelector('#composer-status').hidden&&!document.querySelector('#composer-status').textContent);await page.evaluate(()=>window.__statusPromise);check(firstTokenCleared,'I: first visible token did not clear requesting status');

 const states=[];await page.exposeFunction('captureRuntime',detail=>states.push(detail));await page.evaluate(()=>window.addEventListener('noema:runtime-state',event=>window.captureRuntime(event.detail)));await page.evaluate(()=>streamChat('tool',{userText:'tool'}));check(states.some(item=>item.state==='TOOL'&&item.tool==='get_today_plan'),'J: actual tool state was not shown');const normalStates=[];await page.exposeFunction('captureNormal',detail=>normalStates.push(detail));await page.evaluate(()=>{const handler=event=>window.captureNormal(event.detail);window.addEventListener('noema:runtime-state',handler,{once:false});return streamChat('normal',{userText:'normal'}).finally(()=>window.removeEventListener('noema:runtime-state',handler))});check(!normalStates.some(item=>item.state==='TOOL'||item.state==='MEMORY'),'J: tool status appeared without a tool');

 const fullscreen=await page.evaluate(async()=>{window.Telegram.WebApp.safeAreaInset.top=31;window.Telegram.WebApp.contentSafeAreaInset.top=44;window.Telegram.WebApp.isFullscreen=true;window.__tgEvents.fullscreenChanged({is_fullscreen:true});await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));return {safe:getComputedStyle(document.documentElement).getPropertyValue('--app-safe-top').trim(),diagnostics:window.NoemaViewportDiagnostics}});
 check(fullscreen.safe==='44px'&&fullscreen.diagnostics.fullscreen===true&&fullscreen.diagnostics.safe_top===31&&fullscreen.diagnostics.content_safe_top===44,'O: fullscreenChanged did not resync safe area diagnostics');

 console.log(JSON.stringify({BROWSER_QA:'PASS',opened:{bottom:opened.bottom,diagnostics:opened.diagnostics},short,reading,keyboardBottom,keyboardHistory,tail,firstTokenCleared,toolStates:states,fullscreen},null,2));
 await browser.close();server.close();
})().catch(error=>{console.error(error);server.close();process.exitCode=1});
