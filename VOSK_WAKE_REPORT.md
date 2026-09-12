# Vosk wake-word verification

Date: 2026-09-12

Model: official `vosk-model-small-ru-0.22`, deployed as
`miniapp/models/vosk-model-small-ru-0.22.tar.gz` (46,202,360 bytes). The Mini
App serves that exact archive at
`/app/assets/models/vosk-model-small-ru-0.22.tar.gz`; the browser/WASM smoke
test loaded it successfully with `Vosk.createModel`.

The current restricted wake grammar is:

```json
["эма", "эмма", "[unk]"]
```

`Эма` is the primary trigger. `Ноэма` is deliberately not a required trigger
in this configuration.

## Reproducible recognition probe

Native Vosk 0.3.45 was run at 16 kHz mono against speech synthesized by the
same Russian voice. Both `эма` and `эмма`, and all words in the two longer
phrases, exist in the model vocabulary. The table reports restricted-grammar
output and the first audio timestamp at which Vosk produced a wake alias.

| Spoken phrase | Restricted result | Wake detected at audio time |
| --- | --- | ---: |
| `Эма` | `эма` | 1,000 ms |
| `Эмма` | `эмма` | 1,000 ms |
| `Эма, ты тут?` | `эма` | 1,000 ms |
| `Эма, какие у меня задачи?` | `эма` | 1,000 ms |

These numbers are recognizer output positions in the synthetic audio stream,
not wall-clock microphone latency. In a live Mini App session the actual
VAD-speech-start-to-Vosk-trigger duration is placed in
`window.NoemaVoiceDiagnostics.wake.lastDetectionMs` and recorded as existing
`wake_ms` numeric telemetry; no transcript or audio is stored.

## Runtime diagnostics

`window.NoemaVoiceDiagnostics.wake` contains no user content and exposes:

- `modelLoaded`;
- `recognizerReady`;
- `engine`: `vosk`, `fallback`, `loading`, or `stopped`;
- an explicit non-content failure reason such as `runtime_load_failed`,
  `model_load_failed`, or `recognizer_init_failed`;
- `lastDetectionMs` after a detected wake word.

Fallback is also emitted to the browser dev console with the same code. The
normal conversation then continues in its non-wake listening fallback rather
than claiming Vosk is active.

This probe is reproducible with:

```text
.venv\Scripts\python.exe tools\verify_vosk_wake.py <extracted-model-directory>
```

It verifies the model/runtime path and synthetic Russian audio only. It is not
a claim that every phone microphone, accent, or noisy environment will have the
same latency; live device testing remains the source of that result.
