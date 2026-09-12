/* Local wake/VAD + Voxtral realtime STT, with batch STT fallback.
 * Voice robustness: Silero is primary when it loads; conservative RMS gating
 * keeps the conversation safe on browsers where its WASM cannot load. */
(()=>{
 const cfg={
  listeningRms:.045,speakingRms:.08,minRms:.012,speakingMinRms:.018,
  listeningVadProbability:.66,speakingVadProbability:.82,
  endSilence:520,minSpeech:320,listenStableMs:120,bargeStableMs:250,
  ringMs:2000,sessionMs:25000,targetDelay:480,
 };
 const wakeWords=['но эмо','найма','наем'];
 const wakeAliases=['ноэма','ноема','наэма','наема','наэмо','ноэмо','но эмо','найма','наем'];
 const wakeGrammar=[...wakeWords,'[unk]'];
 const latencyMetrics=['wake_ms','stt_first_partial_ms','stt_final_ms','llm_ttft_ms','tts_first_start_ms','total_response_start_ms','total_ms'];
 const voiceRobustnessMetrics=['barge_in_reason_code','barge_in_duration_ms','barge_in_peak_rms','barge_in_rms','barge_in_vad_probability','audio_capture_sample_rate_hz','stt_stream_sample_rate_hz'];
 const metrics=window.NoemaVoiceMetrics=Array.isArray(window.NoemaVoiceMetrics)?window.NoemaVoiceMetrics:[];
 const diagnostics=window.NoemaVoiceDiagnostics=window.NoemaVoiceDiagnostics||{events:[],audio:null,vad:'loading'};
 const now=()=>performance.now();
 const publishTelemetry=(sample,allowed)=>{
  if(!window.NoemaTelemetryEnabled)return;
  const safe={};for(const name of allowed){const value=sample[name];if(Number.isFinite(value)&&value>=0&&value<=900000)safe[name]=value}
  if(!Object.keys(safe).length)return;
  fetch('/api/v1/miniapp',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({init_data:window.Telegram?.WebApp?.initData||'',action:'telemetry_record',args:{metrics:safe}})}).catch(()=>{});
 };
 const publishMetrics=sample=>publishTelemetry(sample,latencyMetrics);
 const publishVoiceDiagnostics=sample=>publishTelemetry(sample,voiceRobustnessMetrics);
 function sanitizeSpeechText(value){
  let text=String(value||'').replace(/```[\s\S]*?```/g,' ').replace(/`[^`]*`/g,' ');
  text=text.split(/\r?\n/).filter(line=>!/^\s*(?:tool(?:_call)?|debug|metadata|function|arguments?|trace|json)\s*[:=]/i.test(line)&&!/^\s*\|?(?:\s*:?-{3,}:?\s*\|)+\s*$/.test(line)).map(line=>line.replace(/^\s{0,3}#{1,6}\s*/,'').replace(/^\s*(?:[-*+]\s+|\d+[.)]\s+)/,'')).join(' ');
  return text.replace(/\[([^\]]+)\]\([^)]*\)/g,'$1').replace(/https?:\/\/\S+/gi,'ссылка').replace(/"[^"]+"\s*:/g,' ').replace(/[\*_~`|\[\]{}<>]+/g,' ').replace(/(^|\s)[—–-]{2,}(?=\s|$)/g,' ').replace(/\s+([,.!?;:])/g,'$1').replace(/\s{2,}/g,' ').trim();
 }
 const stripWake=text=>String(text||'').replace(new RegExp(`^\\s*(?:эй\\s+|слушай\\s+)?(?:${wakeAliases.join('|')})[,!.?\\s-]*`,'i'),'').trim();
 const bytes64=bytes=>{let s='';for(let i=0;i<bytes.length;i+=8192)s+=String.fromCharCode(...bytes.subarray(i,i+8192));return btoa(s)};
 function pcm16(input,inputRate){const count=Math.max(1,Math.round(input.length*16000/inputRate)),out=new Uint8Array(count*2),view=new DataView(out.buffer);for(let i=0;i<count;i++){const start=Math.floor(i*input.length/count),end=Math.max(start+1,Math.floor((i+1)*input.length/count));let sum=0;for(let j=start;j<end;j++)sum+=input[j];const sample=Math.max(-1,Math.min(1,sum/(end-start)));view.setInt16(i*2,sample<0?sample*32768:sample*32767,true)}return out}
 function appendScript(src,integrity){return new Promise((resolve,reject)=>{const old=[...document.scripts].find(s=>s.src===src);if(old)return old.dataset.loaded==='true'?resolve():old.addEventListener('load',resolve,{once:true});const script=document.createElement('script');script.src=src;script.integrity=integrity;script.crossOrigin='anonymous';script.onload=()=>{script.dataset.loaded='true';resolve()};script.onerror=()=>reject(Error('VAD runtime unavailable'));document.head.append(script)})}
 let voskRuntimePromise,sileroRuntimePromise;
 const loadVoskRuntime=()=>window.Vosk?Promise.resolve():voskRuntimePromise||(voskRuntimePromise=new Promise((resolve,reject)=>{const script=document.createElement('script');script.src='https://cdn.jsdelivr.net/npm/vosk-browser@0.0.8/dist/vosk.js';script.integrity='sha384-nqyY8clHf93uBYFkgkACShMTuvE3U57yXSJaf0Ws+XgzcoUe6OB/1BiOfHqKOWeg';script.crossOrigin='anonymous';script.onload=resolve;script.onerror=()=>reject(Error('Vosk runtime unavailable'));document.head.append(script)}));
 const loadSileroRuntime=()=>window.vad?.MicVAD?Promise.resolve(window.vad):sileroRuntimePromise||(sileroRuntimePromise=(async()=>{
  await appendScript('https://cdn.jsdelivr.net/npm/onnxruntime-web@1.19.2/dist/ort.min.js','sha256-bF6GlqeZPIr2u/dmaorPfzF5Flk52dukWVBqWcF4Bho=');
  window.ort.env.wasm.numThreads=1;
  await appendScript('https://cdn.jsdelivr.net/npm/@ricky0123/vad-web@0.0.30/dist/bundle.min.js','sha256-IGzyygus7mTxFeuhdp4zzmi9l5ybU74x5uDsDturn/g=');
  if(!window.vad?.MicVAD)throw Error('Silero VAD unavailable');return window.vad;
 })());
 class SentenceChunker{constructor(){this.buffer=''}feed(text){this.buffer+=text;const out=[];let m;while((m=this.buffer.match(/^.*?[.!?…](?:\s+|$)/s))){out.push(m[0].trim());this.buffer=this.buffer.slice(m[0].length)}return out}flush(){const x=this.buffer.trim();this.buffer='';return x?[x]:[]}}
 class ClientSpeechSynthesisProvider{
  constructor(session){this.session=session;this.queue=[];this.running=false}
  enqueue(text){const speechText=sanitizeSpeechText(text);if(speechText){this.queue.push(speechText);this.pump()}}
  pump(){if(this.running||!this.queue.length||!('speechSynthesis'in window))return;this.running=true;this.session.metric('tts_first_start_ms');this.session.metric('total_response_start_ms');this.session.setState('SPEAKING');const u=new SpeechSynthesisUtterance(this.queue.shift());u.lang='ru-RU';u.rate=.98;u.onend=u.onerror=()=>{this.running=false;if(this.queue.length)this.pump();else this.session.setState('LISTENING')};speechSynthesis.speak(u)}
  cancel(){this.queue=[];this.running=false;window.speechSynthesis?.cancel()}
 }
 class EnergyVADProvider{
  constructor(analyser){this.analyser=analyser;this.samples=new Uint8Array(analyser.fftSize)}
  sample(){this.analyser.getByteTimeDomainData(this.samples);let sum=0,peak=0;for(const value of this.samples){const normalized=(value-128)/128;sum+=normalized*normalized;peak=Math.max(peak,Math.abs(normalized))}return {rms:Math.sqrt(sum/this.samples.length),peak}}
 }
 class SileroVADProvider{
  constructor(){this.ready=false;this.latestProbability=null;this.updatedAt=0;this.instance=null}
  async start(stream,audioContext){const runtime=await loadSileroRuntime();this.instance=await runtime.MicVAD.new({
   model:'v5',audioContext,getStream:async()=>stream,pauseStream:async()=>{},resumeStream:async()=>stream,startOnLoad:false,processorType:'auto',
   baseAssetPath:'https://cdn.jsdelivr.net/npm/@ricky0123/vad-web@0.0.30/dist/',onnxWASMBasePath:'https://cdn.jsdelivr.net/npm/onnxruntime-web@1.19.2/dist/',
   positiveSpeechThreshold:cfg.listeningVadProbability,negativeSpeechThreshold:.5,redemptionMs:520,preSpeechPadMs:0,minSpeechMs:cfg.minSpeech,submitUserSpeechOnPause:false,
   onFrameProcessed:probabilities=>{const value=Number(probabilities?.isSpeech??probabilities?.speech);if(Number.isFinite(value)){this.latestProbability=Math.max(0,Math.min(1,value));this.updatedAt=now()}},
   onSpeechStart:()=>{},onSpeechRealStart:()=>{},onSpeechEnd:()=>{},onVADMisfire:()=>{},
  });await this.instance.start();this.ready=true}
  probability(){return this.ready&&now()-this.updatedAt<220?this.latestProbability:null}
  close(){this.ready=false;this.instance?.destroy?.().catch(()=>{});this.instance=null}
 }
 class WakeWordProvider{
  constructor(onWake){this.onWake=onWake;this.ready=false}
  async load(){await loadVoskRuntime();this.model=await Vosk.createModel('/app/assets/models/vosk-model-small-ru-0.22.tar.gz');this.recognizer=new this.model.KaldiRecognizer(16000,JSON.stringify(wakeGrammar));const read=event=>{const value=(event.result?.partial||event.result?.text||'').toLowerCase().trim();if(wakeWords.some(word=>value.includes(word)))this.onWake(value)};this.recognizer.on('partialresult',read);this.recognizer.on('result',read);this.ready=true}
  feed(floatSamples,sampleRate){if(this.ready)this.recognizer.acceptWaveformFloat(floatSamples,sampleRate)}
  close(){this.recognizer?.remove();this.model?.terminate();this.ready=false}
 }
 class VoxtralRealtimeSTTProvider{
  constructor(onPartial){this.onPartial=onPartial;this.queue=[];this.text='';this.ready=false;this.ended=false;this.done=false}
  async start(initial=[]){const response=await fetch('/api/v1/miniapp/voice/realtime-token',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({init_data:window.Telegram?.WebApp?.initData||''})});const body=await response.json();if(!response.ok||!body.ok)throw Error(body.code||'REALTIME_UNAVAILABLE');if(this.done)throw new DOMException('Cancelled','AbortError');const {token,model,url}=body.data;this.ws=new WebSocket(`${url}?model=${encodeURIComponent(model)}`,['realtime',token]);this.promise=new Promise((resolve,reject)=>{this.resolve=resolve;this.reject=reject});this.ws.onmessage=event=>this.message(event);this.ws.onerror=()=>this.fail(Error('REALTIME_UNAVAILABLE'));this.ws.onclose=()=>{if(!this.done)this.fail(Error('REALTIME_CLOSED'))};for(const chunk of initial)this.push(chunk);return this.promise}
  message(event){let msg;try{msg=JSON.parse(event.data)}catch{return}if(msg.type==='session.created'){this.ws.send(JSON.stringify({type:'session.update',session:{audio_format:{encoding:'pcm_s16le',sample_rate:16000},target_streaming_delay_ms:cfg.targetDelay}}));this.ready=true;this.flush()}else if(msg.type==='transcription.text.delta'){const delta=msg.text||msg.delta||'';this.text+=delta;this.onPartial?.(this.text)}else if(msg.type==='transcription.done'){this.done=true;this.resolve(this.text.trim());this.ws.close()}else if(msg.type==='error')this.fail(Error('REALTIME_ERROR'))}
  push(chunk){if(!chunk?.length||this.done)return;this.queue.push(chunk.slice());this.flush()}
  flush(){if(!this.ready||this.ws.readyState!==WebSocket.OPEN)return;while(this.queue.length)this.ws.send(JSON.stringify({type:'input_audio.append',audio:bytes64(this.queue.shift())}));if(this.ended){this.ws.send(JSON.stringify({type:'input_audio.flush'}));this.ws.send(JSON.stringify({type:'input_audio.end'}));this.ended=false}}
  finish(){this.ended=true;this.flush();return this.connection||this.promise}
  cancel(){this.done=true;this.ws?.close();this.reject?.(new DOMException('Cancelled','AbortError'))}
  fail(error){if(this.done)return;this.done=true;this.reject?.(error);this.ws?.close()}
 }
 class VoiceSession{
  constructor(){this.active=false;this.state='SLEEPING';this.ring=[];this.pcmRing=[];this.pcmBytes=0;this.utterance=[];this.speechAt=0;this.silentAt=0;this.listeningCandidate=null;this.bargeCandidate=null;this.busy=false;this.turn=0;this.tts=new ClientSpeechSynthesisProvider(this)}
  metric(name){if(!this.currentMetrics||this.currentMetrics[name]!==undefined)return;this.currentMetrics[name]=Math.round(now()-this.currentMetrics.started_at);if(name==='total_ms'){const sample={};for(const metricName of latencyMetrics){const value=this.currentMetrics[metricName];if(Number.isFinite(value))sample[metricName]=value}metrics.push(sample);if(metrics.length>200)metrics.splice(0,metrics.length-200);publishMetrics(sample)}}
  setState(state){this.state=state;window.NoemaMembrane?.setState({SLEEPING:'idle',LISTENING:'listening',THINKING:'thinking',SPEAKING:'responding'}[state]);document.querySelector('[data-conversation-status]')?.replaceChildren(document.createTextNode({SLEEPING:'',LISTENING:'Слушаю',THINKING:'Думаю',SPEAKING:'Говорю'}[state]))}
  async start(){if(this.active)return this.stop();const constraints={echoCancellation:true,noiseSuppression:true,autoGainControl:true,channelCount:1};this.stream=await navigator.mediaDevices.getUserMedia({audio:constraints,video:false});this.context=new AudioContext();const source=this.context.createMediaStreamSource(this.stream),analyser=this.context.createAnalyser();analyser.fftSize=512;source.connect(analyser);this.energy=new EnergyVADProvider(analyser);this.processor=this.context.createScriptProcessor(4096,1,1);const silent=this.context.createGain();silent.gain.value=0;source.connect(this.processor);this.processor.connect(silent);silent.connect(this.context.destination);this.processor.onaudioprocess=event=>this.audio(event.inputBuffer.getChannelData(0));const mime=['audio/webm;codecs=opus','audio/mp4'].find(value=>MediaRecorder.isTypeSupported(value));this.recorder=new MediaRecorder(this.stream,mime?{mimeType:mime}:{});this.recorder.ondataavailable=event=>{if(!event.data.size)return;this.ring.push(event.data);while(this.ring.length>Math.ceil(cfg.ringMs/250))this.ring.shift();if(this.speechAt)this.utterance.push(event.data)};this.recorder.start(250);const settings=this.stream.getAudioTracks()[0]?.getSettings?.()||{};diagnostics.audio={capture:{sampleRate:Number(settings.sampleRate||this.context.sampleRate),channelCount:Number(settings.channelCount||1),echoCancellation:Boolean(settings.echoCancellation),noiseSuppression:Boolean(settings.noiseSuppression),autoGainControl:Boolean(settings.autoGainControl)},fallbackCodec:this.recorder.mimeType||mime||'browser-default',realtime:{encoding:'pcm_s16le',sampleRate:16000,channels:1}};publishVoiceDiagnostics({audio_capture_sample_rate_hz:diagnostics.audio.capture.sampleRate,stt_stream_sample_rate_hz:16000});this.silero=new SileroVADProvider();this.silero.start(this.stream,this.context).then(()=>{diagnostics.vad='silero-v5'}).catch(()=>{diagnostics.vad='rms-fallback';this.silero=null});this.active=true;this.deadline=Date.now()+cfg.sessionMs;this.setState('SLEEPING');this.wake=new WakeWordProvider(()=>this.activateWake());this.wake.load().catch(()=>{if(this.active&&this.state==='SLEEPING')this.setState('LISTENING')});this.loop();this.drawButton()}
  audio(floatSamples){const pcm=pcm16(floatSamples,this.context.sampleRate);this.pcmRing.push(pcm);this.pcmBytes+=pcm.length;while(this.pcmBytes>16000*2*cfg.ringMs/1000){this.pcmBytes-=this.pcmRing.shift().length}if(this.state==='SLEEPING')this.wake?.feed(floatSamples,this.context.sampleRate);if(this.realtime)this.realtime.push(pcm)}
  signal(){const energy=this.energy.sample(),probability=this.silero?.probability(),speaking=this.state==='SPEAKING',rmsThreshold=speaking?cfg.speakingRms:cfg.listeningRms,minRms=speaking?cfg.speakingMinRms:cfg.minRms,vadThreshold=speaking?cfg.speakingVadProbability:cfg.listeningVadProbability,usesSilero=Number.isFinite(probability);return {...energy,probability:usesSilero?probability:null,usesSilero,speech:usesSilero?probability>=vadThreshold&&energy.rms>=minRms:energy.rms>=rmsThreshold}}
  activateWake(){if(!this.active||this.state!=='SLEEPING')return;const activated=now();this.currentMetrics={started_at:this.wakeSpeechAt||activated};this.metric('wake_ms');this.wakeSpeechAt=0;this.setState('LISTENING');this.speechAt=activated;this.utterance=[...this.ring];this.startRealtime()}
  startRealtime(){const provider=new VoxtralRealtimeSTTProvider(()=>this.metric('stt_first_partial_ms'));this.realtime=provider;provider.connection=provider.start([...this.pcmRing]);provider.connection.catch(()=>{});return provider}
  beginListeningSpeech(startedAt){this.listeningCandidate=null;this.currentMetrics={started_at:startedAt};this.speechAt=startedAt;this.utterance=[...this.ring];this.startRealtime()}
  recordBargeIn(candidate){const duration=Math.round(now()-candidate.startedAt),sample={barge_in_reason_code:1,barge_in_duration_ms:duration,barge_in_peak_rms:Number(candidate.peak.toFixed(4)),barge_in_rms:Number((candidate.rmsTotal/candidate.frames).toFixed(4)),barge_in_vad_probability:Number(candidate.probability.toFixed(4))};diagnostics.events.push(sample);if(diagnostics.events.length>100)diagnostics.events.splice(0,diagnostics.events.length-100);publishVoiceDiagnostics(sample)}
  bargeIn(candidate){this.recordBargeIn(candidate);this.turn++;this.aborter?.abort();this.tts.cancel();this.busy=false;this.setState('LISTENING');this.speechAt=candidate.startedAt;this.silentAt=0;this.utterance=[...this.ring];this.currentMetrics={started_at:candidate.startedAt,barge_in:0};this.bargeCandidate=null;this.startRealtime()}
  stop(){this.active=false;cancelAnimationFrame(this.frame);this.turn++;this.aborter?.abort();this.realtime?.cancel();this.tts.cancel();this.wake?.close();this.silero?.close();if(this.recorder?.state==='recording')this.recorder.stop();this.processor?.disconnect();this.stream?.getTracks().forEach(track=>track.stop());this.context?.close();this.setState('SLEEPING');this.drawButton()}
  drawButton(){const button=document.querySelector('[data-conversation]');if(button)button.firstElementChild.textContent=this.active?'Выйти':'Разговор'}
  updateBargeCandidate(t,signal){if(!signal.speech){this.bargeCandidate=null;return}const current=this.bargeCandidate||{startedAt:t,peak:0,rmsTotal:0,probability:0,frames:0};current.peak=Math.max(current.peak,signal.peak);current.rmsTotal+=signal.rms;current.probability=Math.max(current.probability,signal.probability||0);current.frames+=1;this.bargeCandidate=current;if(t-current.startedAt>=cfg.bargeStableMs)this.bargeIn(current)}
  updateListeningCandidate(t,signal){if(!signal.speech){this.listeningCandidate=null;return}const candidate=this.listeningCandidate||{startedAt:t};this.listeningCandidate=candidate;if(t-candidate.startedAt>=cfg.listenStableMs)this.beginListeningSpeech(candidate.startedAt)}
  loop(){if(!this.active)return;const t=now(),signal=this.signal();window.NoemaMembrane?.setAudioLevel(Math.min(1,signal.rms*8));if(this.state==='SLEEPING'){if(signal.speech)this.wakeSpeechAt=this.wakeSpeechAt||t;else this.wakeSpeechAt=0}else if(this.state==='SPEAKING'){this.updateBargeCandidate(t,signal)}else if(!this.busy&&this.state==='LISTENING'){if(!this.speechAt)this.updateListeningCandidate(t,signal);else if(signal.speech){this.silentAt=0;this.deadline=Date.now()+cfg.sessionMs}else{this.silentAt=this.silentAt||t;if(t-this.silentAt>=cfg.endSilence){const duration=t-this.speechAt;if(duration>=cfg.minSpeech)this.submit();else{this.speechAt=0;this.utterance=[];this.realtime?.cancel();this.realtime=null}}}}if(Date.now()>this.deadline&&!this.busy&&!this.speechAt)this.stop();else this.frame=requestAnimationFrame(()=>this.loop())}
  async batchTranscript(chunks){const body=new FormData();body.append('init_data',window.Telegram?.WebApp?.initData||'');body.append('audio',new Blob(chunks,{type:this.recorder.mimeType}),'conversation.'+(this.recorder.mimeType.includes('mp4')?'mp4':'webm'));const response=await fetch('/api/v1/miniapp/voice/transcribe',{method:'POST',body});const result=await response.json();if(!response.ok)throw Error(result.error||'Не удалось распознать реплику.');return result.data?.text||''}
  async submit(){const chunks=this.utterance.splice(0),provider=this.realtime;this.realtime=null;this.speechAt=0;this.silentAt=0;if(!chunks.length)return;const turn=++this.turn;this.busy=true;this.setState('THINKING');try{let transcript='',realtimeSucceeded=true;try{if(!provider)throw Error('REALTIME_UNAVAILABLE');transcript=await Promise.race([provider.finish(),new Promise((_,reject)=>setTimeout(()=>reject(Error('REALTIME_TIMEOUT')),8000))])}catch{realtimeSucceeded=false;provider?.cancel();transcript=await this.batchTranscript(chunks)}transcript=stripWake(transcript);if(!transcript)throw Error('Речь не распознана. Попробуй ещё раз.');if(realtimeSucceeded)this.metric('stt_final_ms');this.aborter=new AbortController();const chunker=new SentenceChunker();let gotDelta=false;await streamChat(transcript,{signal:this.aborter.signal,onDelta:displayText=>{if(!gotDelta){gotDelta=true;this.metric('llm_ttft_ms')}for(const sentence of chunker.feed(displayText))this.tts.enqueue(sentence)}});for(const tail of chunker.flush())this.tts.enqueue(tail);await load();this.deadline=Date.now()+cfg.sessionMs;this.metric('total_ms');if(!this.tts.running)this.setState('LISTENING')}catch(error){if(error.name!=='AbortError')say(error.message);if(turn===this.turn)this.setState('LISTENING')}finally{if(turn===this.turn)this.busy=false}}
 }
 const session=new VoiceSession();window.NoemaVoiceSession=session;window.NoemaVoiceInternals={stripWake,pcm16,wakeGrammar,wakeAliases,sanitizeSpeechText,voiceRobustnessMetrics};
 document.addEventListener('click',async event=>{if(!event.target.closest('[data-conversation]'))return;try{session.active?session.stop():await session.start()}catch{say('Не удалось включить разговор. Проверь разрешение микрофона.')}});
})();
