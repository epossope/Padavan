const {chromium}=require(process.env.PLAYWRIGHT_PATH||'C:/Users/tanke/Desktop/NoemaDiz/node_modules/playwright');
const appUrl=process.env.NOEMA_TEST_URL||'http://127.0.0.1:8095/app';

(async()=>{
  const browser=await chromium.launch({headless:true});
  const page=await browser.newPage();
  await page.goto(appUrl);
  const result=await page.evaluate(async()=>{
    const {SpeechQueue,SentenceChunker}=window.NoemaVoiceInternals||{};
    if(!SpeechQueue||!SentenceChunker)throw Error('Production TTS internals unavailable');
    const originalFetch=window.fetch,originalAudio=window.Audio;
    const originalCreate=URL.createObjectURL,originalRevoke=URL.revokeObjectURL;
    const playback=[],voices=[],metrics=[],revoked=[];
    let concurrent=0,maxConcurrent=0;
    window.fetch=async(url,options)=>{
      if(!String(url).includes('/api/v1/miniapp/speech'))return originalFetch(url,options);
      const body=JSON.parse(options.body),sequence=Number((body.text.match(/segment (\d+)/)||[])[1]||0);
      voices.push(body.preferences.voice);concurrent++;maxConcurrent=Math.max(maxConcurrent,concurrent);
      await new Promise(resolve=>setTimeout(resolve,sequence===0?18:2));concurrent--;
      const blob=new Blob(['audio']);blob.noemaSequence=sequence;
      return {ok:true,blob:async()=>blob};
    };
    URL.createObjectURL=blob=>`mock:${blob.noemaSequence}`;
    URL.revokeObjectURL=url=>revoked.push(url);
    window.Audio=class{
      constructor(url){this.sequence=Number(url.split(':')[1]);this.paused=false}
      play(){setTimeout(()=>{if(this.paused)return;playback.push(this.sequence);this.onplaying?.();setTimeout(()=>{if(!this.paused)this.onended?.()},4)},0);return Promise.resolve()}
      pause(){this.paused=true}
    };
    try{
      for(let turn=0;turn<20;turn++){
        const queue=new SpeechQueue((name,value,sample)=>metrics.push({name,value,sample}));
        queue.beginResponse(performance.now());
        queue.enqueue('segment 0. Это первая осмысленная фраза для проверки очереди.');
        queue.enqueue('segment 1. Это второй фрагмент, подготовленный заранее.');
        queue.enqueue('segment 2. Это обязательный хвост ответа.');
        queue.complete();
        await queue.whenIdle();
      }
      const chunker=new SentenceChunker();
      const early=chunker.feed('Да. Хорошо. И вот более длинное законченное предложение, которое уже можно произнести. ');
      const tail=chunker.flush();
      const playedDiagnostics=(window.NoemaTTSChunkDiagnostics||[]).slice(-60);
      const pausedQueue=new SpeechQueue((name,value,sample)=>metrics.push({name,value,sample}));
      pausedQueue.beginResponse(performance.now());pausedQueue.pause();
      pausedQueue.enqueue('segment 0. Очередь сохраняется на паузе.');
      pausedQueue.enqueue('segment 1. Воспроизведение продолжается по порядку.');
      pausedQueue.complete();
      await new Promise(resolve=>setTimeout(resolve,30));
      const pausedSnapshot={items:pausedQueue.items.length,next:pausedQueue.nextSequence,playback:playback.length};
      pausedQueue.resume();await pausedQueue.whenIdle();
      const resumedSnapshot={items:pausedQueue.items.length,next:pausedQueue.nextSequence,playback:playback.slice(-2)};
      const cancelledQueue=new SpeechQueue(()=>{});cancelledQueue.beginResponse(performance.now());cancelledQueue.pause();
      cancelledQueue.enqueue('segment 0. Этот фрагмент должен быть отменён.');cancelledQueue.enqueue('segment 1. И этот тоже.');
      cancelledQueue.complete();cancelledQueue.cancel();await new Promise(resolve=>setTimeout(resolve,30));
      const cancelledSnapshot={items:cancelledQueue.items.length,next:cancelledQueue.nextSequence,playing:cancelledQueue.playing,preparing:cancelledQueue.preparing};
      return {playback,voices,metrics,revoked,maxConcurrent,early,tail,playedDiagnostics,pausedSnapshot,resumedSnapshot,cancelledSnapshot};
    }finally{window.fetch=originalFetch;window.Audio=originalAudio;URL.createObjectURL=originalCreate;URL.revokeObjectURL=originalRevoke}
  });
  await browser.close();
  const expected=Array.from({length:20},()=>[0,1,2]).flat().concat([0,1]);
  if(JSON.stringify(result.playback)!==JSON.stringify(expected))throw Error(`Playback order/loss: ${JSON.stringify(result.playback)}`);
  if(new Set(result.voices).size!==1)throw Error(`Voice changed within responses: ${JSON.stringify([...new Set(result.voices)])}`);
  if(result.maxConcurrent!==2)throw Error(`Expected two-chunk prefetch, got ${result.maxConcurrent}`);
  if(result.revoked.length!==62)throw Error(`Object URLs not released: ${result.revoked.length}/62`);
  if(result.playedDiagnostics.length!==60||result.playedDiagnostics.some(item=>item.status!=='played'))throw Error('Chunk lifecycle did not finish as played');
  if(JSON.stringify(result.pausedSnapshot)!==JSON.stringify({items:2,next:0,playback:60}))throw Error(`Pause lost or played queue data: ${JSON.stringify(result.pausedSnapshot)}`);
  if(JSON.stringify(result.resumedSnapshot.playback)!==JSON.stringify([0,1])||result.resumedSnapshot.next!==2)throw Error(`Resume order failed: ${JSON.stringify(result.resumedSnapshot)}`);
  if(result.cancelledSnapshot.items||result.cancelledSnapshot.playing||result.cancelledSnapshot.preparing)throw Error(`Cancel retained stale queue state: ${JSON.stringify(result.cancelledSnapshot)}`);
  if(!result.metrics.some(item=>item.name==='tts_first_start_ms')||!result.metrics.some(item=>item.name==='tts_playback_gap_ms'))throw Error('Required playback metrics missing');
  if(result.early.length!==1||result.tail.length)throw Error(`Speech buffering regression: ${JSON.stringify({early:result.early,tail:result.tail})}`);
  console.log('20 replies × 3 chunks plus pause/resume/cancel: strict order, zero loss, fixed voice, prefetch=2: passed');
})().catch(error=>{console.error(error);process.exitCode=1});
