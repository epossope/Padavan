# Latency diagnosis

## Voice robustness

The Mini App uses Silero VAD v5 in the browser as the primary speech classifier.
It is loaded only when a conversation starts. If the browser cannot load the
model or ONNX runtime, the same session falls back to a conservative local RMS
gate; it does not upload audio to obtain a VAD decision.

The gate is intentionally stricter while Noema is speaking:

| State | Silero speech probability | Adaptive RMS floor | Stable duration |
| --- | ---: | ---: | ---: |
| Listening | 0.66 | `max(0.012, noise floor × 2.2)` | 150 ms before starting a turn |
| Speaking / barge-in | 0.82 | `max(0.018, noise floor × 3.2)` | 250 ms before cancelling TTS/LLM |
| RMS fallback | n/a | same adaptive floor | same durations |

The noise floor adapts only slowly while speech is active, so ordinary speech
does not become its own new background. A 400 ms PCM pre-roll is attached to
each accepted turn and a 620 ms speech-end hangover protects the last word.
The whole current turn is also held locally until both realtime and fallback
recognition have finished; it is not exported as telemetry.

Capture explicitly requests `echoCancellation`, `noiseSuppression`,
`autoGainControl`, and one channel. The actual granted track settings (without
device identifiers), fallback recorder MIME type and realtime contract are held
only in `window.NoemaVoiceDiagnostics` for the active page. Realtime STT always
receives resampled 16 kHz mono PCM signed little-endian; batch fallback receives
the same locally accumulated browser WebM/MP4 chunks. The realtime connection is
opened during an active voice session and reused for a following turn when the
provider leaves it open; if it closes, the next turn reconnects normally.
Empty realtime final, timeout or a closed socket trigger transparent batch
recognition. The user sees a failure only when both paths produce no usable
transcript.

The Vosk wake grammar remains the experimentally verified restricted grammar:
`но эмо`, `найма`, `наем`, `[unk]`. The written aliases are removed only at the
start of an explicit wake/name phrase; ordinary names are never globally
rewritten. Voxtral Realtime does not document a hotword/context-bias parameter,
so none is sent. Its documented batch endpoint has context biasing separately.

### Telemetry

The `Voice robustness` section in `/telemetry_report` includes only bounded
numeric samples:

- `barge_in_reason_code` (`1` = sustained qualified speech);
- `barge_in_duration_ms`, `barge_in_peak_rms`, `barge_in_rms`, and
  `barge_in_vad_probability` for every accepted barge-in;
- `vad_engine` (`1` = Silero active, `0` = RMS fallback) and bounded fallback
  reason codes (`1` = runtime load failure, `2` = stale Silero probability,
  `3` = Silero still loading);
- `noise_floor_rms`, speech-start RMS/probability, actual capture sample rate,
  fixed 16 kHz STT stream rate and websocket connection time;
- aggregate counters for empty realtime finals and batch fallback attempts and
  successes.

No waveform, transcript, message content, device id, API key or user identity
is retained by this telemetry.

### Required live matrix

Run each case with `/telemetry_reset` before the series and inspect
`/telemetry_report` afterward:

1. quiet room;
2. music or street noise;
3. isolated tap/knock and short rustle;
4. distant voice;
5. normal nearby speech;
6. a real spoken interruption while Noema is answering.

Pass criteria: cases 1–5 must not create an unintended barge-in; case 6 must
create exactly one qualified barge-in after roughly 250 ms of sustained speech.
