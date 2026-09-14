// Boot reliability scenarios. The server is local; no Telegram identity or user data is used.
const {chromium}=require(process.env.PLAYWRIGHT_PATH||'C:/Users/tanke/Desktop/NoemaDiz/node_modules/playwright');
const http=require('http'),fs=require('fs'),path=require('path');
const root=path.resolve(__dirname,'..','miniapp'),attempts=new Map();
const state={day:'2026-09-14',plan:{tasks:[],reminders:[]},tasks:[],reminders:[],notes:[],people:[],expenses:[],files:[],history:[],rules:[],settings:{mode:'text',timezone:'Europe/Moscow',home_widgets:[],experimental_realtime:false,experimental_realtime_available:false,experimental_wake_enabled:false,telemetry_enabled:false,voice_preferences:{gender:'male',voice:'ru-RU-DmitryNeural',speed:1,pitch:1,volume:1},briefing:{enabled:false,time:'08:30',topics:'',city:''}}};
const telemetry=[];
function mode(request){try{return new URL(request.headers.referer||'http://x/?mode=normal').searchParams.get('mode')||'normal'}catch{return 'normal'}}
function json(response,status,payload){response.writeHead(status,{'Content-Type':'application/json','Cache-Control':'no-store'});response.end(JSON.stringify(payload))}
function asset(request,response){const current=mode(request),name=decodeURIComponent(new URL(request.url,'http://x').pathname.slice('/app/assets/'.length)),file=path.resolve(root,name);if((current==='main-404'&&name==='app.js')||!file.startsWith(root)||!fs.existsSync(file)){response.writeHead(404);return response.end()}if(current==='main-wrong-mime'&&name==='app.js'){response.writeHead(200,{'Content-Type':'text/html','X-Content-Type-Options':'nosniff'});return response.end('window.shouldNotRun=true')}if(current==='early-js-error'&&name==='app.js'){response.writeHead(200,{'Content-Type':'application/javascript'});return response.end('throw new Error("early boot test")')}const type=name.endsWith('.js')?'application/javascript':name.endsWith('.css')?'text/css':name.endsWith('.json')?'application/json':'application/octet-stream';response.writeHead(200,{'Content-Type':type,'X-Content-Type-Options':'nosniff'});fs.createReadStream(file).pipe(response)}
const server=http.createServer((request,response)=>{
 const url=new URL(request.url,'http://127.0.0.1');
 if(url.pathname==='/app'||url.pathname==='/app/')return fs.createReadStream(path.join(root,'index.html')).pipe(response);
 if(url.pathname.startsWith('/app/assets/'))return asset(request,response);
 if(url.pathname==='/api/v1/miniapp/boot-telemetry'){let body='';request.on('data',chunk=>body+=chunk);request.on('end',()=>{try{telemetry.push(JSON.parse(body))}catch{}json(response,200,{ok:true})});return}
 if(url.pathname==='/api/v1/miniapp'){let body='';request.on('data',chunk=>body+=chunk);request.on('end',()=>{const current=mode(request),payload=JSON.parse(body||'{}');if(payload.action==='state'){
   if(current==='timeout')return;
   if(current==='auth')return json(response,401,{ok:false});
   if(current==='retry'){const count=(attempts.get(current)||0)+1;attempts.set(current,count);if(count===1)return json(response,401,{ok:false})}
   return json(response,200,{ok:true,data:state});
  }if(current==='widget-failure'&&(payload.action==='budget'||payload.action==='weather'))return json(response,503,{ok:false});return json(response,200,{ok:true,data:{items:[],currency_totals:{},city:'',current:null}})});return}
 response.writeHead(404);response.end();
});
const check=(value,message)=>{if(!value)throw new Error(message)};
async function open(browser,base,modeName){
 const context=await browser.newContext({viewport:{width:390,height:844}});
 await context.route('https://telegram.org/js/telegram-web-app.js',route=>route.fulfill({contentType:'application/javascript',body:`
  const boot=()=>window.Telegram={WebApp:{initData:'signed',platform:'ios',version:'8.0',ready(){},expand(){},setHeaderColor(){},setBackgroundColor(){},onEvent(){},BackButton:{show(){},hide(){},onClick(){}}}};
  const mode=new URL(location.href).searchParams.get('mode');if(mode==='sdk-delayed')setTimeout(boot,250);else if(mode==='initdata-missing')window.Telegram={WebApp:{platform:'ios',version:'8.0'}};else if(mode!=='sdk-missing')boot();
 `}));
 const page=await context.newPage();page.setDefaultTimeout(12000);if(modeName==='optional-api-missing')await page.addInitScript(()=>{delete window.ResizeObserver;delete window.IntersectionObserver;delete window.visualViewport});await page.goto(`${base}/app?mode=${modeName}`,{waitUntil:'domcontentloaded'});return {context,page};
}
(async()=>{
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));const base=`http://noema.test:${server.address().port}`,browser=await chromium.launch({headless:true,args:['--host-resolver-rules=MAP noema.test 127.0.0.1']});
 try{
  let session=await open(browser,base,'normal');await session.page.locator('#splash.hidden').waitFor();check(await session.page.locator('#content .hero').count()===1,'A: normal boot did not render Home');check((await session.page.evaluate(()=>window.NoemaBootEvents.map(event=>event.stage))).includes('render_ready'),'A: render_ready telemetry missing');await session.context.close();
  session=await open(browser,base,'sdk-delayed');await session.page.locator('#splash.hidden').waitFor();check(await session.page.evaluate(()=>window.NoemaBootEvents.some(event=>event.stage==='telegram_ready')),'B: delayed SDK did not recover');await session.context.close();
  for(const failure of ['sdk-missing','main-404','main-wrong-mime','early-js-error','initdata-missing']){session=await open(browser,base,failure);await session.page.locator('#boot-recovery:not([hidden])').waitFor();check(await session.page.locator('#splash.hidden').count()===1,`${failure}: splash remained active`);await session.context.close()}
  session=await open(browser,base,'optional-api-missing');await session.page.locator('#splash.hidden').waitFor();check(await session.page.locator('#content .hero').count()===1,'G: optional APIs blocked Home');await session.context.close();
  session=await open(browser,base,'auth');await session.page.locator('#boot-recovery:not([hidden])').waitFor();check((await session.page.locator('[data-boot-message]').textContent()).includes('Сессия Telegram устарела'),'D: auth 401 has no session recovery');await session.context.close();
  session=await open(browser,base,'widget-failure');await session.page.locator('#splash.hidden').waitFor();check(await session.page.locator('#content .hero').count()===1,'E: a failed widget blocked Home');await session.context.close();
  attempts.clear();session=await open(browser,base,'retry');await session.page.locator('#boot-recovery:not([hidden])').waitFor();await session.page.locator('[data-boot-retry]').click();await session.page.locator('#splash.hidden').waitFor();check(await session.page.locator('#content .hero').count()===1,'I: Retry did not bootstrap successfully');await session.context.close();
  session=await open(browser,base,'normal');await session.page.locator('#splash.hidden').waitFor();await session.page.evaluate(()=>{void Promise.reject(new Error('boot test'));return true});await session.page.waitForTimeout(100);check(await session.page.evaluate(()=>window.NoemaBootEvents.some(event=>event.stage==='unhandledrejection')),'G: unhandled rejection telemetry missing');await session.page.evaluate(()=>window.NoemaBoot.assetReady('app.js','old-build'));await session.page.waitForURL(/__noema_build=/);await session.page.evaluate(()=>window.NoemaBoot.assetReady('app.js','old-build'));await session.page.locator('#boot-recovery:not([hidden])').waitFor();check(await session.page.evaluate(()=>sessionStorage.getItem('noema-build-reload:__NOEMA_APP_BUILD_ID__')==='1'),'F: mismatch reload was not limited');await session.context.close();
  session=await open(browser,base,'timeout');await session.page.locator('#boot-recovery:not([hidden])').waitFor({timeout:9500});check(await session.page.locator('#splash.hidden').count()===1,'C/J: timed out API boot left splash active');check(await session.page.evaluate(()=>window.NoemaBootEvents.some(event=>event.stage==='bootstrap_failed')),'C: API timeout was not recorded');await session.page.evaluate(()=>window.NoemaBoot.run(()=>Promise.reject(Object.assign(new Error('timeout'),{code:'BOOT_TIMEOUT'}))));await session.page.waitForFunction(()=>window.NoemaBootEvents.some(event=>event.stage==='boot_timeout'));check(await session.page.locator('#splash.hidden').count()===1,'H: hard boot timeout left splash active');await session.context.close();
  console.log(JSON.stringify({MINIAPP_BOOT_BROWSER:'PASS',telemetry_events:telemetry.length},null,2));
 }finally{await browser.close();server.close()}
})().catch(error=>{console.error(error);server.close();process.exitCode=1});
