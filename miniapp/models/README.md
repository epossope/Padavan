# Vosk wake model

`vosk-model-small-ru-0.22.tar.gz` is the official Apache-2.0 Russian small model
downloaded from `https://alphacephei.com/vosk/models/vosk-model-small-ru-0.22.zip`
and repacked as `tar.gz` for `vosk-browser` without modifying model contents.

Verified SHA-256 of the official ZIP:
`961d5ff98a17f4aa6de69864d0aa71fa5bac682301d2b5d17a3f24c5c99a46d4`.

Run the vocabulary and speech probe with:

```powershell
python tools/verify_vosk_wake.py C:\path\to\vosk-model-small-ru-0.22
```
