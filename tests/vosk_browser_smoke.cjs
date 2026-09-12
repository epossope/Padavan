// Browser/WASM model-load smoke. Requires the same local preview server as miniapp_visual.cjs.
const {chromium}=require(process.env.PLAYWRIGHT_PATH||'C:/Users/tanke/Desktop/NoemaDiz/node_modules/playwright');
const appUrl=process.env.NOEMA_VOSK_APP_URL||'http://127.0.0.1:8091/app';
const modelUrl=process.env.NOEMA_VOSK_MODEL_URL||'/app/assets/models/vosk-model-small-ru-0.22.tar.gz';
const voiceUrl=process.env.NOEMA_VOSK_VOICE_URL||'/app/assets/voice-conversation.js';
(async()=>{
 const browser=await chromium.launch({headless:true});
 const page=await browser.newPage();
 await page.goto(appUrl);
 const result=await page.evaluate(async ({modelUrl,voiceUrl})=>{
  if(!window.NoemaVoiceInternals)await new Promise((resolve,reject)=>{const s=document.createElement('script');s.src=voiceUrl;s.onload=resolve;s.onerror=reject;document.head.append(s)});
  await new Promise((resolve,reject)=>{const s=document.createElement('script');s.src='https://cdn.jsdelivr.net/npm/vosk-browser@0.0.8/dist/vosk.js';s.integrity='sha384-nqyY8clHf93uBYFkgkACShMTuvE3U57yXSJaf0Ws+XgzcoUe6OB/1BiOfHqKOWeg';s.crossOrigin='anonymous';s.onload=resolve;s.onerror=reject;document.head.append(s)});
  const model=await Vosk.createModel(modelUrl);
  const recognizer=new model.KaldiRecognizer(16000,JSON.stringify(['эма','эмма','[unk]']));
  const ready=Boolean(model.ready&&recognizer);
  const command=window.NoemaVoiceInternals?.stripWake('Эма, какие у меня задачи сегодня?');
  const grammar=window.NoemaVoiceInternals?.wakeGrammar||[];
  const speech=window.NoemaVoiceInternals?.sanitizeSpeechText('## **Важно**\n- Открой [задачи](https://example.com)\n| поле | значение |\n| --- | --- |\ntool_call: {"debug": true}');
  recognizer.remove();model.terminate();return {ready,command,grammar,speech};
 },{modelUrl,voiceUrl});
 await browser.close();
 if(!result.ready)throw Error('Vosk browser model did not initialize');
 if(result.command!=='какие у меня задачи сегодня?')throw Error(`Wake stripping clipped command: ${result.command}`);
 if(JSON.stringify(result.grammar)!==JSON.stringify(['эма','эмма','[unk]']))throw Error(`Unexpected wake grammar: ${JSON.stringify(result.grammar)}`);
 if(/[\*_|\[\]{}]|https?:|tool_call|debug/.test(result.speech)||!result.speech.includes('Открой задачи'))throw Error(`Speech sanitizer leaked markup: ${result.speech}`);
 console.log('Vosk/WASM + wake command + speech sanitizer: passed');
})().catch(error=>{console.error(error);process.exitCode=1});
