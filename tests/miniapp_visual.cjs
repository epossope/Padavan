// Local read-only UI regression checks. No Telegram session or real user data.
const {chromium}=require(process.env.PLAYWRIGHT_PATH||'C:/Users/tanke/Desktop/NoemaDiz/node_modules/playwright');
const path=require('path');
const visualFixture={
 data:{
  day:'2026-09-12',plan:{tasks:[],reminders:[]},
  tasks:[
   {id:1,text:'Подготовить отчёт Q2',due_date:'2026-09-12T21:00:00+03:00',priority:'high',status:'open'},
   {id:2,text:'Забрать посылку',due_date:'2026-09-12T18:00:00+03:00',priority:'high',status:'open'},
   {id:3,text:'Позвонить врачу',due_date:'2026-09-12T17:30:00+03:00',priority:'medium',status:'open'},
   {id:4,text:'Согласовать презентацию',due_date:'2026-09-13T11:00:00+03:00',priority:'medium',status:'open'},
   {id:5,text:'Оплатить сервисы',due_date:'2026-09-11',priority:'low',status:'done'}
  ],
  reminders:[
   {id:1,text:'Встреча по продуктовой стратегии',remind_at_utc:'2026-09-12T08:00:00Z',acknowledged:0},
   {id:2,text:'Позвонить врачу',remind_at_utc:'2026-09-12T14:30:00Z',acknowledged:0}
  ],
  notes:[
   {id:1,title:'Идеи для продукта',text:'ИИ-помощник для подготовки к встречам: анализ контекста, подсказки и вопросы.',created_at:'2026-09-12T06:41:00Z'},
   {id:2,title:'Что обсудить с Антоном',text:'Продуктовая стратегия Q2. Ресурсы на дизайн. Интеграции и API.',created_at:'2026-09-11T15:30:00Z'},
   {id:3,title:'Покупки домой',text:'Овощи и зелень, куриное филе, рис басмати, йогурт без сахара.',created_at:'2026-09-10T13:05:00Z'},
   {id:4,title:'Мысли по сайту',text:'Сделать упор на скорость и ясность. Минимализм, мягкие анимации.',created_at:'2026-09-09T19:14:00Z'}
  ],
  files:[
   {id:11,original_name:'Дашборд аналитики.png',mime_type:'image/png',kind:'image',summary:'Обновлённый дашборд с метриками за май.',created_at:'2026-09-11T06:41:00Z'},
   {id:12,original_name:'Стратегия Q2.pdf',mime_type:'application/pdf',kind:'document',summary:'План приоритетов и ключевые инициативы на второй квартал.',created_at:'2026-09-09T15:20:00Z'},
   {id:13,original_name:'Введение в дизайн-системы',mime_type:'text/html',kind:'web_resource',summary:'https://medium.com — статья о масштабируемости интерфейсов.',created_at:'2026-09-08T11:32:00Z'}
  ],
  people:[
   {id:1,name:'Антон',relationship:'друг',birthday:'17 октября',home_city:'Москва',current_location:'Санкт-Петербург',age:31,projects:'Omega, Интеграция Q2',notes:'Занимается поставщиками. Работаем вместе над Omega.',recent_interactions:[{interaction:'Прислал обновления по поставщикам.',interaction_date:'Сегодня, 11:24',interaction_type:'Omega'},{interaction:'Обсудили сроки по интеграции.',interaction_date:'Вчера, 16:45',interaction_type:'Omega'}]},
   {id:2,name:'Мария',relationship:'коллега',birthday:'3 ноября',home_city:'',current_location:'',projects:'Дизайн',notes:'Отвечает за визуальную систему.',recent_interactions:[]},
   {id:3,name:'Игорь',relationship:'коллега',birthday:'22 июня',home_city:'',current_location:'',projects:'Разработка',notes:'Backend и интеграции.',recent_interactions:[]}
  ],
  expenses:[],
  history:[
   {role:'user',content:'Какие задачи у меня сегодня?'},
   {role:'assistant',content:'Сегодня три задачи. Самая срочная — подготовить отчёт Q2 до 21:00.'},
   {role:'user',content:'Напомни про встречу.'},
   {role:'assistant',content:'Напомню перед встречей по продуктовой стратегии.'}
  ],
  rules:[{id:1,description:'Отвечать кратко и по существу'}],
  settings:{mode:'text',timezone:'Europe/Moscow',home_widgets:['tasks','next_event','notes','reminders','budget','recent_saved'],experimental_realtime:false,experimental_realtime_available:true,experimental_wake_enabled:false,effective_ai:{effective_model:{value:'deepseek/deepseek-v4-flash-0731',source:'USER'},fast_default:{value:'qwen/qwen3.5-flash-02-23',source:'ENV'},strong_fallback:{value:'deepseek/deepseek-v3.2',source:'ENV'},vision:{value:'qwen/qwen-vision',source:'ENV'},tts:{provider:'edge',provider_source:'ENV',voice:'ru-RU-DmitryNeural',voice_source:'ENV'}},admin_runtime_config:{updated_at:'2026-09-12T08:00:00Z',updated_by:42,fields:{fast_model:{value:'qwen/qwen3.5-flash-02-23',source:'ENV'},fast_model_providers:{value:['Alibaba'],source:'ENV'},fast_model_allow_provider_fallback:{value:false,source:'ENV'},strong_model:{value:'deepseek/deepseek-v3.2',source:'ENV'},strong_model_providers:{value:['StreamLake','DeepInfra'],source:'ENV'},strong_model_allow_provider_fallback:{value:true,source:'ENV'},vision_model:{value:'qwen/qwen-vision',source:'ENV'},vision_fallback_models:{value:[],source:'DEFAULT'},batch_stt_model:{value:'mistralai/voxtral-mini-transcribe',source:'ENV'},tts_provider:{value:'edge',source:'ENV'},tts_fallback_provider:{value:'browser',source:'ENV'},tts_voice:{value:'ru-RU-DmitryNeural',source:'ENV'},default_voice_reply_mode:{value:'text',source:'DEFAULT'},realtime_model:{value:'voxtral-realtime',source:'DEFAULT'},model_catalog:{value:['qwen/qwen3.5-flash-02-23','deepseek/deepseek-v3.2'],source:'ENV'}}},voice_runtime:{tts_provider:'edge',tts_fallback_provider:'browser',tts_voice:'ru-RU-DmitryNeural'},telemetry_enabled:false,briefing:{enabled:true,time:'08:30',topics:'главные новости мира',city:'Санкт-Петербург'}}
 },
 weather:{city:'Санкт-Петербург',current:{condition:'ясно',temperature:14,feels_like:11}},
 budget:{currency_totals:{RUB:{expense:5750,income:0,balance:-5750}},items:[
  {id:1,kind:'expense',amount:4200,currency:'RUB',category:'Транспорт',description:'Яндекс.Заправки',spent_at:'2026-09-12T15:10:00Z'},
  {id:2,kind:'expense',amount:1280,currency:'RUB',category:'Продукты',description:'Пятёрочка',spent_at:'2026-09-10T15:10:00Z'},
  {id:3,kind:'expense',amount:270,currency:'RUB',category:'Прочее',description:'Кофе',spent_at:'2026-09-08T10:20:00Z'}
 ]}
};
(async()=>{
 const browser=await chromium.launch({headless:true});const errors=[];
 for(const [width,height] of [[320,700],[360,800],[375,812],[390,844],[414,896],[430,932],[900,1100]]){
  const page=await browser.newPage({viewport:{width,height},deviceScaleFactor:1});
  const uniform={headers:[],searches:[],chips:[],cardRadii:[]};
  page.on('pageerror',e=>errors.push(e.message));
  await page.goto('http://127.0.0.1:8091/app');await page.locator('#splash.hidden').waitFor();await page.evaluate(fixture=>{data=fixture.data;weatherData=fixture.weather;budgetData=fixture.budget;render()},visualFixture);await page.waitForTimeout(500);
  const viewport=await page.locator('meta[name=viewport]').getAttribute('content');
  if(!/maximum-scale=1/.test(viewport)||!/user-scalable=no/.test(viewport)||!/viewport-fit=cover/.test(viewport))errors.push('Viewport zoom hardening missing');
  if(await page.evaluate(()=>window.visualViewport&&window.visualViewport.scale!==1))errors.push(`Unexpected initial zoom ${width}`);
  for(const route of ['home','chat','tasks','notes','archive','people','budget','settings','reminders']){
   await page.evaluate(route=>go(route),route);
   if(route==='tasks')await page.locator('.task-card details').first().evaluate(element=>element.open=true);
   if(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth))errors.push(`${route} overflows ${width}`);
   if(route!=='home'){
    if(await page.locator('#header [data-back]').count())errors.push(`${route} still exposes a header back arrow ${width}`);
    if(await page.locator('#header .utilities button').count())errors.push(`${route} still exposes header actions ${width}`);
    const header=await page.locator('#header').boundingBox();
    if(header.height>34)errors.push(`${route} header is visually oversized ${width}`);
    uniform.headers.push(header.height);
    if(await page.locator('.search-field').count())uniform.searches.push((await page.locator('.search-field').boundingBox()).height);
    if(await page.locator('.chip').count())uniform.chips.push((await page.locator('.chip').first().boundingBox()).height);
    if(route!=='chat'&&await page.locator('#content .card').count())uniform.cardRadii.push(Number.parseFloat(await page.locator('#content .card').first().evaluate(el=>getComputedStyle(el).borderRadius)));
   }
   if(route==='settings'&&await page.locator('#dock').isVisible())errors.push('Membrane visible in settings');
   if(route==='settings'){
    const sections=page.locator('.admin-ai .runtime-section');
    if(await sections.count()!==6||await page.locator('.admin-ai .runtime-section[open]').count()!==1)errors.push(`AI & Voice sections are not collapsed ${width}`);
    if(!await page.locator('.admin-ai').getByText('Сейчас для меня',{exact:true}).count())errors.push(`Effective user model summary missing ${width}`);
    const adminText=await page.locator('.admin-ai').innerText();
    if(!adminText.includes('deepseek/deepseek-v4-flash-0731')||!adminText.includes('USER'))errors.push(`Effective USER model is not distinct ${width}`);
   }
   if(width<=430&&['notes','archive','people'].includes(route)){
    const searchBox=await page.locator('.search-field').boundingBox();
    const chipBox=await page.locator('.chip').first().boundingBox();
    if(searchBox.height>34)errors.push(`${route} search is visually oversized ${width}`);
    if(chipBox.height>30)errors.push(`${route} chip is visually oversized ${width}`);
   }
   if(width<=430&&route==='tasks'){
    const segmentBox=await page.locator('.segments').boundingBox();
    const collapsedBox=await page.locator('.task-card').nth(1).boundingBox();
    if(segmentBox.height>44||collapsedBox.height>76)errors.push(`Task density regressed ${width}`);
   }
   if(width<=430&&route==='notes'&&(await page.locator('.note-card').first().boundingBox()).height>100)errors.push(`Note density regressed ${width}`);
   if(width<=430&&route==='archive'&&(await page.locator('.resource-card').first().boundingBox()).height>92)errors.push(`Archive density regressed ${width}`);
   if(width<=430&&route==='people'){
    const expanded=await page.locator('.person-profile').first().boundingBox(),collapsed=await page.locator('.person-profile').nth(1).boundingBox();
    if(expanded.height>350||collapsed.height>68)errors.push(`People density regressed ${width}`);
   }
   if(width<=430&&route==='budget'){
    const picker=await page.locator('.period-picker').boundingBox(),periods=await page.locator('.budget-controls .segments').boundingBox(),total=await page.locator('.budget-totals .card').first().boundingBox();
    const charts=await Promise.all([0,1].map(index=>page.locator('.budget-dashboard>.card').nth(index).boundingBox()));
    if(Math.abs(picker.y-periods.y)>3||picker.x<=periods.x||picker.height>36||periods.height>36||total.height>84||charts[1].y<charts[0].y+charts[0].height)errors.push(`Budget proportions regressed ${width}`);
    const barFill=await page.locator('.bar-group i').first().evaluate(el=>getComputedStyle(el).backgroundImage);
    if(!barFill.includes('repeating-linear-gradient'))errors.push(`Daily budget bars lost their hatched finish ${width}`);
    await page.locator('[data-currency-toggle]').click();
    if(await page.locator('.currency-widget-panel').count()!==1)errors.push(`Inline currency widget did not expand ${width}`);
   }
   if(width<=430&&route==='settings'&&(await page.locator('.settings-stack .setting').first().boundingBox()).height>46)errors.push(`Settings rows are visually oversized ${width}`);
   if(route==='chat'){
    const box=await page.locator('.composer').boundingBox();if(height-box.y-box.height>40)errors.push(`Composer not bottom anchored ${width}`);
    const sphere=await page.locator('.composer .chat-voice-orb').boundingBox();if(Math.abs((sphere.y+sphere.height)-(box.y+box.height))>4||Math.abs(sphere.x-(box.x+box.width))>3||sphere.x+sphere.width>width+2)errors.push(`Chat sphere is not the separate bottom-aligned composer end ${width}`);
    const sphereStyle=await page.locator('.composer .chat-voice-orb').evaluate(el=>({width:getComputedStyle(el).width,hasCanvas:Boolean(el.querySelector('canvas')),isLast:el===document.querySelector('.composer').lastElementChild,borderRight:getComputedStyle(el.parentElement).borderRightWidth}));
    if(Number.parseFloat(sphereStyle.width)<96||Number.parseFloat(sphereStyle.width)>106||box.height>62||!sphereStyle.hasCanvas||!sphereStyle.isLast||sphereStyle.borderRight!=='0px')errors.push(`Chat voice sphere/open composer contract failed ${width}: sphere=${sphereStyle.width}, composer=${box.height}`);
    if(await page.locator('#notice.visible').count())errors.push(`Chat uses a separate status strip ${width}`);
    await page.evaluate(()=>say('Слушаю…'));
    if(await page.locator('#composer-status:not([hidden])').count()!==1||await page.locator('#notice.visible').count())errors.push(`Chat status is not contained by composer ${width}`);
    await page.evaluate(()=>say(''));
    if(Number.parseFloat(await page.locator('#chat-input').evaluate(el=>getComputedStyle(el).fontSize))<16)errors.push(`Chat input can trigger iPhone zoom ${width}`);
   }
   if(route==='home'){
    if(await page.locator('#header .utilities button').count()!==1||await page.locator('#header [data-page="settings"]').count()!==1)errors.push(`Home header is not Noema plus Settings only ${width}`);
    const dockStyle=await page.locator('#dock').evaluate(el=>({position:getComputedStyle(el).position,background:getComputedStyle(el).backgroundImage,frame:getComputedStyle(el,'::before').display}));
    const boxes=await Promise.all(['.focus-pill','.orb-button','.keyboard-button'].map(selector=>page.locator(`#dock ${selector}`).boundingBox()));
    const centers=boxes.map(box=>box.y+box.height/2);if(dockStyle.position!=='fixed'||dockStyle.background==='none'||dockStyle.frame!=='none'||Math.max(...centers)-Math.min(...centers)>3)errors.push(`Dock overlay alignment failed ${width}`);
    if(width<=430){
     const tile=await page.locator('.home-grid>[data-widget]').first().boundingBox();
     const focusShape=await page.locator('#dock .focus-pill').evaluate(el=>({body:getComputedStyle(el).borderRadius,organic:getComputedStyle(el,'::before').backgroundImage}));
     await page.evaluate(()=>scrollTo(0,document.documentElement.scrollHeight));await page.waitForTimeout(50);
     const last=await page.locator('.home-grid>[data-widget]').last().boundingBox(),dock=await page.locator('#dock').boundingBox();
     if(tile.height>145||boxes[1].width>120||last.y+last.height>dock.y+2||focusShape.body!=='0px'||focusShape.organic==='none')errors.push(`Home density, organic focus, or dock safe-zone regressed ${width}`);
     await page.evaluate(()=>scrollTo(0,0));
    }
   }
   if(width===390){
    await page.screenshot({path:path.resolve('miniapp',`preview-${route}.png`),fullPage:false});
    if(['chat','archive','budget','notes','people'].includes(route)){
     await page.screenshot({path:path.resolve('ui-proof','ui-polish-v1.2',`after-${route}.png`),fullPage:false});
    }
   }
  }
  for(const [name,values,tolerance] of [['headers',uniform.headers,1],['searches',uniform.searches,1],['chips',uniform.chips,1],['card radii',uniform.cardRadii,1]]){
   if(values.length&&Math.max(...values)-Math.min(...values)>tolerance)errors.push(`Internal ${name} are inconsistent ${width}: ${values.join(',')}`);
  }
  await page.evaluate(()=>go('home'));
  const stable=await page.evaluate(()=>{const canvas=document.querySelector('#dock canvas');render();render();return canvas===document.querySelector('#dock canvas')});if(!stable)errors.push('Dock canvas recreated on render');
  if(await page.locator('.all-sections').count()||await page.locator('#header [data-layout]').count())errors.push('Removed layout controls still present');
  if(await page.locator('#header .utilities button').count()!==1||await page.locator('#header [data-page="settings"]').count()!==1)errors.push('Home header contains actions other than Settings');
  await page.locator('#dock [data-voice]').click();
  if(await page.evaluate(()=>page!=='home'))errors.push('Sphere navigated to chat');
  let first=await page.locator('[data-widget=tasks]').boundingBox();
  await page.mouse.move(first.x+first.width/2,first.y+first.height/2);await page.mouse.down();await page.waitForTimeout(500);await page.mouse.up();await page.waitForTimeout(100);
  if(!await page.locator('.home-grid.editing').count()||!await page.locator('[data-home-done]').count())errors.push(`Long press did not enter Home edit mode ${width}`);
  first=await page.locator('[data-widget=tasks]').boundingBox();const second=await page.locator('[data-widget=next_event]').boundingBox();
  await page.mouse.move(first.x+first.width/2,first.y+first.height/2);await page.mouse.down();await page.mouse.move(second.x+second.width/2,second.y+second.height/2,{steps:5});await page.mouse.up();await page.waitForTimeout(650);
  if(await page.locator('.grid>[data-widget]').first().getAttribute('data-widget')!=='next_event')errors.push(`Direct drag failed ${width}`);
  await page.locator('[data-home-hide="tasks"]').click();
  if(await page.locator('[data-widget="tasks"]').count()||await page.locator('[data-home-show="tasks"]').count()!==1)errors.push(`Home hide control failed ${width}`);
  await page.locator('[data-home-show="tasks"]').click();
  if(await page.locator('[data-widget="tasks"]').count()!==1)errors.push(`Home restore chip failed ${width}`);
  await page.locator('[data-home-done]').click();
  if(await page.locator('.home-grid.editing').count()||await page.locator('.home-widget-hide').count())errors.push(`Home Done did not leave edit mode ${width}`);
  await page.evaluate(()=>go('settings'));if(await page.getByRole('button',{name:'Настроить виджеты',exact:true}).count())errors.push('Widget settings remained in Settings');
  await page.close();
 }
 const touch=await browser.newPage({viewport:{width:390,height:844},hasTouch:true,isMobile:true});
 await touch.goto('http://127.0.0.1:8091/app');await touch.locator('#splash.hidden').waitFor();await touch.evaluate(fixture=>{data=fixture.data;weatherData=fixture.weather;budgetData=fixture.budget;render()},visualFixture);
 let a=await touch.locator('[data-widget=tasks]').boundingBox(),cdp=await touch.context().newCDPSession(touch);
 await cdp.send('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:[{x:a.x+a.width/2,y:a.y+a.height/2}]});await touch.waitForTimeout(500);await cdp.send('Input.dispatchTouchEvent',{type:'touchEnd',touchPoints:[]});await touch.waitForTimeout(100);
 if(!await touch.locator('.home-grid.editing').count())errors.push('Touch long press did not enter edit mode');
 a=await touch.locator('[data-widget=tasks]').boundingBox();const b=await touch.locator('[data-widget=next_event]').boundingBox();
 await cdp.send('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:[{x:a.x+a.width/2,y:a.y+a.height/2}]});
 await cdp.send('Input.dispatchTouchEvent',{type:'touchMove',touchPoints:[{x:b.x+b.width/2,y:b.y+b.height/2}]});
 await cdp.send('Input.dispatchTouchEvent',{type:'touchEnd',touchPoints:[]});await touch.waitForTimeout(400);
 if(await touch.locator('.grid>[data-widget]').first().getAttribute('data-widget')!=='next_event')errors.push('Touch drag failed');
 await touch.evaluate(()=>go('archive'));
 await cdp.send('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:[{x:5,y:220}]});
 await cdp.send('Input.dispatchTouchEvent',{type:'touchMove',touchPoints:[{x:105,y:226}]});
 await cdp.send('Input.dispatchTouchEvent',{type:'touchEnd',touchPoints:[]});await touch.waitForTimeout(100);
 if(await touch.evaluate(()=>page)!=='home')errors.push('Edge swipe did not return to the preceding screen');
 await touch.evaluate(()=>go('archive'));
 await cdp.send('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:[{x:5,y:220}]});
 await cdp.send('Input.dispatchTouchEvent',{type:'touchMove',touchPoints:[{x:12,y:350}]});
 await cdp.send('Input.dispatchTouchEvent',{type:'touchEnd',touchPoints:[]});await touch.waitForTimeout(100);
 if(await touch.evaluate(()=>page)!=='archive')errors.push('Vertical scroll was mistaken for an edge swipe');
 const fullscreen=await browser.newPage({viewport:{width:390,height:844}});
 await fullscreen.route('https://telegram.org/**',route=>route.abort());
 await fullscreen.addInitScript(()=>{
  window.__telegramBoot={ready:0,expand:0,fullscreen:0};
  window.Telegram={WebApp:{
   initData:'',safeAreaInset:{top:11,right:2,bottom:7,left:3},contentSafeAreaInset:{top:19,right:4,bottom:13,left:5},
   ready(){window.__telegramBoot.ready++},expand(){window.__telegramBoot.expand++},requestFullscreen(){window.__telegramBoot.fullscreen++},
   isVersionAtLeast(){return true},onEvent(){},setHeaderColor(){},setBackgroundColor(){},
   BackButton:{onClick(){},hide(){},show(){}}
  }}
 });
 await fullscreen.goto('http://127.0.0.1:8091/app');await fullscreen.locator('#splash.hidden').waitFor();
 const fullscreenBoot=await fullscreen.evaluate(()=>({calls:window.__telegramBoot,safeTop:getComputedStyle(document.documentElement).getPropertyValue('--app-safe-top').trim(),safeBottom:getComputedStyle(document.documentElement).getPropertyValue('--app-safe-bottom').trim()}));
 if(fullscreenBoot.calls.ready!==1||fullscreenBoot.calls.expand!==1||fullscreenBoot.calls.fullscreen!==1)errors.push('Telegram fullscreen boot sequence failed');
 if(fullscreenBoot.safeTop!=='19px'||fullscreenBoot.safeBottom!=='13px')errors.push('Telegram safe area was not applied');
 await fullscreen.close();
 const fallback=await browser.newPage({viewport:{width:390,height:844}});
 await fallback.route('https://telegram.org/**',route=>route.abort());
 await fallback.addInitScript(()=>{
  window.__telegramBoot={ready:0,expand:0};
  window.Telegram={WebApp:{initData:'',ready(){window.__telegramBoot.ready++},expand(){window.__telegramBoot.expand++},onEvent(){},setHeaderColor(){},setBackgroundColor(){},BackButton:{onClick(){},hide(){},show(){}}}}
 });
 await fallback.goto('http://127.0.0.1:8091/app');await fallback.locator('#splash.hidden').waitFor();
 const fallbackBoot=await fallback.evaluate(()=>window.__telegramBoot);
 if(fallbackBoot.ready!==1||fallbackBoot.expand!==1)errors.push('Telegram expand fallback failed');
 await fallback.close();
 await browser.close();if(errors.length)throw Error(errors.join('\n'));console.log('7 viewports × 9 screens + edge swipe + compact headers + Home edit mode + open composer: passed');
})().catch(e=>{console.error(e);process.exitCode=1});
