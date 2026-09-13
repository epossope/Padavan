// Deterministic local visual proof for the chat composer. No Telegram data is used.
const {chromium}=require(process.env.PLAYWRIGHT_PATH||'C:/Users/tanke/Desktop/NoemaDiz/node_modules/playwright');
const {execFileSync}=require('child_process');
const fs=require('fs');
const path=require('path');

const baseUrl=process.env.NOEMA_TEST_URL||'http://127.0.0.1:8091/app';
const proofDir=path.resolve('ui-proof','chat-composer-root-fix');
const baselineRef=process.env.NOEMA_VISUAL_BASE_REF||'HEAD^';

async function openChat(browser){
 const page=await browser.newPage({viewport:{width:375,height:812},deviceScaleFactor:1});
 await page.goto(baseUrl);
 await page.locator('#splash.hidden').waitFor();
 await page.evaluate(()=>go('chat'));
 await page.waitForTimeout(220);
 return page;
}

async function setInput(page,value){
 await page.locator('#chat-input').evaluate((input,text)=>{
  input.value=text;
  input.dispatchEvent(new Event('input',{bubbles:true}));
 },value);
 await page.waitForTimeout(100);
}

(async()=>{
 fs.mkdirSync(proofDir,{recursive:true});
 const browser=await chromium.launch({headless:true});

 const before=await openChat(browser);
 const previousCss=execFileSync('git',['show',`${baselineRef}:miniapp/design-match.css`],{encoding:'utf8'});
 await before.addStyleTag({content:previousCss});
 await before.screenshot({path:path.join(proofDir,'before-375-empty.png')});
 await setInput(before,'оченьдлинноесловобезпробелов'.repeat(12));
 await before.screenshot({path:path.join(proofDir,'before-375-long-unbroken.png')});
 await setInput(before,'Первая строка\nВторая строка\nТретья строка\nЧетвёртая строка\nПятая строка\nШестая строка');
 await before.screenshot({path:path.join(proofDir,'before-375-multiline.png')});
 await setInput(before,'');
 await before.locator('.chat-sphere-slot').screenshot({path:path.join(proofDir,'before-sphere-square.png')});
 await before.close();

 const after=await openChat(browser);
 await after.screenshot({path:path.join(proofDir,'after-375-empty.png')});
 await setInput(after,'оченьдлинноесловобезпробелов'.repeat(12));
 await after.screenshot({path:path.join(proofDir,'after-375-long-unbroken.png')});
 await setInput(after,'Первая строка\nВторая строка\nТретья строка\nЧетвёртая строка\nПятая строка\nШестая строка');
 await after.screenshot({path:path.join(proofDir,'after-375-multiline.png')});
 await setInput(after,'');
 await after.locator('.chat-sphere-slot').screenshot({path:path.join(proofDir,'after-sphere-transparent.png')});
 for(const state of ['idle','listening','thinking','responding']){
  await after.evaluate(value=>window.NoemaMembrane?.setState(value),state);
  await after.waitForTimeout(180);
  await after.locator('.chat-sphere-slot').screenshot({path:path.join(proofDir,`after-sphere-${state}.png`)});
 }

 const contract=await after.evaluate(()=>{
  const composer=document.querySelector('.chat-composer');
  const input=document.querySelector('.chat-input-area');
  const textarea=document.querySelector('#chat-input');
  const slot=document.querySelector('.chat-sphere-slot');
  const orb=document.querySelector('.chat-voice-orb');
  const canvas=orb.querySelector('canvas');
  const textareaStyle=getComputedStyle(textarea);
  return {
   composerWidth:composer.getBoundingClientRect().width,
   contentWidth:document.querySelector('#content').getBoundingClientRect().width,
   inputWidth:input.getBoundingClientRect().width,
   slotWidth:slot.getBoundingClientRect().width,
   grid:getComputedStyle(composer).gridTemplateColumns,
   textarea:{maxHeight:textareaStyle.maxHeight,overflowY:textareaStyle.overflowY,overflowWrap:textareaStyle.overflowWrap},
   orb:{background:getComputedStyle(orb).backgroundImage,filter:getComputedStyle(orb).filter},
   canvas:{background:getComputedStyle(canvas).backgroundImage,filter:getComputedStyle(canvas).filter}
  };
 });
 console.log(JSON.stringify(contract,null,2));
 await after.close();
 await browser.close();
})().catch(error=>{console.error(error);process.exitCode=1});
