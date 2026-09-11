# Noema wake-word verification

Date: 2026-09-11

Model: official `vosk-model-small-ru-0.22` (ZIP SHA-256
`961d5ff98a17f4aa6de69864d0aa71fa5bac682301d2b5d17a3f24c5c99a46d4`).

The probe used the native Vosk 0.3.45 recognizer at 16 kHz mono. Each phrase was
spoken by the same Russian synthetic voice and decoded both with the unrestricted
model and with the resulting restricted grammar.

| Spoken variant | Direct vocabulary entry | Unrestricted result | Restricted result |
| --- | --- | --- | --- |
| ноэма | no | но эмо | но эмо |
| ноема | no | но эмо | но эмо |
| наэма | no | найма | найма |
| наема | no | наем | наем |
| наэмо | no | найма | найма |
| ноэмо | no | найма | найма |

The deployed grammar is therefore exactly:

```json
["но эмо", "найма", "наем", "[unk]"]
```

Each normal word used in those three recognized aliases exists in the model and
all six spoken variants produced one of the aliases under the restricted grammar.

Phrase probe: `Ноэма, какие у меня задачи сегодня?`

- Unrestricted result: `но эмо какие у меня задача сегодня`.
- Restricted wake result: `но эмо`.
- At wake activation the browser forwards the preceding 2,000 ms PCM ring plus
  all subsequent samples into Voxtral, so the command following the wake word is
  not discarded while the realtime socket is opening.

This is a reproducible synthetic-audio check, not a claim about every microphone,
speaker, accent, or acoustic environment. The same probe should be repeated on
target phones during the live Telegram Mini App smoke test.
