// Browser/WASM model-load smoke. Requires the same local preview server as miniapp_visual.cjs.
const {chromium}=require(process.env.PLAYWRIGHT_PATH||'C:/Users/tanke/Desktop/NoemaDiz/node_modules/playwright');
(async()=>{
 const browser=await chromium.launch({headless:true});
 const page=await browser.newPage();
 await page.goto('http://127.0.0.1:8091/app');
 const result=await page.evaluate(async()=>{
  await new Promise((resolve,reject)=>{const s=document.createElement('script');s.src='https://cdn.jsdelivr.net/npm/vosk-browser@0.0.8/dist/vosk.js';s.integrity='sha384-nqyY8clHf93uBYFkgkACShMTuvE3U57yXSJaf0Ws+XgzcoUe6OB/1BiOfHqKOWeg';s.crossOrigin='anonymous';s.onload=resolve;s.onerror=reject;document.head.append(s)});
  const model=await Vosk.createModel('/app/assets/models/vosk-model-small-ru-0.22.tar.gz');
  const recognizer=new model.KaldiRecognizer(16000,JSON.stringify(['но эмо','найма','наем','[unk]']));
  const ready=Boolean(model.ready&&recognizer);
  const command=window.NoemaVoiceInternals?.stripWake('Ноэма, какие у меня задачи сегодня?');
  const grammar=window.NoemaVoiceInternals?.wakeGrammar||[];
  const speech=window.NoemaVoiceInternals?.sanitizeSpeechText('## **Важно**\n- Открой [задачи](https://example.com)\n| поле | значение |\n| --- | --- |\ntool_call: {"debug": true}');
  recognizer.remove();model.terminate();return {ready,command,grammar,speech};
 });
 await browser.close();
 if(!result.ready)throw Error('Vosk browser model did not initialize');
 if(result.command!=='какие у меня задачи сегодня?')throw Error(`Wake stripping clipped command: ${result.command}`);
 if(JSON.stringify(result.grammar)!==JSON.stringify(['но эмо','найма','наем','[unk]']))throw Error(`Unexpected wake grammar: ${JSON.stringify(result.grammar)}`);
 if(/[\*_|\[\]{}]|https?:|tool_call|debug/.test(result.speech)||!result.speech.includes('Открой задачи'))throw Error(`Speech sanitizer leaked markup: ${result.speech}`);
 console.log('Vosk/WASM + wake command + speech sanitizer: passed');
})().catch(error=>{console.error(error);process.exitCode=1});
