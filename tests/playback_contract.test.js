const fs=require('fs');
const assert=require('assert');
const source=fs.readFileSync('miniapp/app.js','utf8');

assert.match(source,/if\(this\.state==='speaking'\)\{this\.speech\.pause\(\);this\.setState\('paused'\)/);
assert.match(source,/if\(this\.state==='paused'\)\{this\.speech\.resume\(\);this\.setState\('speaking'\)/);
assert.match(source,/async bargeIn\(\).*this\.speech\.cancel\(\).*this\.audioReleaseDelay\(\).*this\.startRecording\(\)/);
assert.match(source,/if\(\['speaking','paused'\]\.includes\(this\.state\)\)\{this\.speech\.cancel\(\)/);
assert.match(source,/session=\{id:captureId,stream,recorder,chunks:\[\],mime:/);
assert.match(source,/recorder\.onstop=\(\)=>this\.processRecording\(session\)/);
assert.match(source,/if\(session\.id!==this\.captureId\)return/);
assert.match(source,/for\(let attempt=0;attempt<2;attempt\+\+\).*\/voice\/transcribe/);
assert.match(source,/event\.type==='speech_delta'.*onSpeechDelta/);
assert.doesNotMatch(source,/class SpeechStreamPolicy|function sanitizeSpeechText/);
assert.match(source,/this\.items=\[\].*this\.activeCancel\?\.\(\)/);
assert.doesNotMatch(source,/floating-player|playback-bar/);
console.log('playback contract ok');
