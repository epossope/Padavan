# Latency diagnosis

## Voice robustness

The Mini App uses Silero VAD v5 in the browser as the primary speech classifier.
It is loaded only when a conversation starts. If the browser cannot load the
model or ONNX runtime, the same session falls back to a conservative local RMS
gate; it does not upload audio to obtain a VAD decision.

The gate is intentionally stricter while Noema is speaking:

| State | Silero speech probability | RMS floor | Stable duration |
| --- | ---: | ---: | ---: |
| Listening | 0.66 | 0.012 | 120 ms before starting a turn |
| Speaking / barge-in | 0.82 | 0.018 | 250 ms before cancelling TTS/LLM |
| RMS fallback (listening / speaking) | n/a | 0.045 / 0.080 | same durations |

This rejects clicks, taps and short rustles while retaining a 2-second PCM
ring buffer, so the beginning of an accepted reply is not lost.

Capture explicitly requests `echoCancellation`, `noiseSuppression`,
`autoGainControl`, and one channel. The actual granted track settings (without
device identifiers), fallback recorder MIME type and realtime contract are held
only in `window.NoemaVoiceDiagnostics` for the active page. Realtime STT always
receives 16 kHz mono PCM signed little-endian; batch fallback receives the
browser's actual WebM/MP4 recorder MIME type.

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
- actual capture sample rate and fixed 16 kHz STT stream rate.

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
