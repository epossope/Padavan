const fs=require('fs');
const assert=require('assert');
const source=fs.readFileSync('miniapp/app.js','utf8');

assert.match(source,/if\(this\.state==='speaking'\)\{this\.speech\.pause\(\);this\.setState\('paused'\)/);
assert.match(source,/if\(this\.state==='paused'\)\{this\.speech\.resume\(\);this\.setState\('speaking'\)/);
assert.match(source,/async bargeIn\(\).*this\.speech\.cancel\(\).*this\.startRecording\(\)/);
assert.match(source,/if\(\['speaking','paused'\]\.includes\(this\.state\)\)\{this\.speech\.cancel\(\)/);
assert.match(source,/class SpeechStreamPolicy/);
assert.match(source,/this\.items=\[\].*this\.activeCancel\?\.\(\)/);
assert.doesNotMatch(source,/floating-player|playback-bar/);
console.log('playback contract ok');
