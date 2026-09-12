"""Reproducible vocabulary and speech probe for Noema's official Vosk wake model."""
from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import tempfile
import wave
from pathlib import Path

import edge_tts
import imageio_ffmpeg
from vosk import KaldiRecognizer, Model, SetLogLevel


WAKE_VARIANTS = [
    "Эма",
    "Эмма",
    "Эма, ты тут?",
    "Эма, какие у меня задачи?",
]
FALLBACK_VARIANTS = ["слушай", "привет", "помощник"]
RECOGNIZED_WAKE_ALIASES = ["эма", "эмма"]


async def synthesize(text: str, output: Path) -> None:
    mp3 = output.with_suffix(".mp3")
    await edge_tts.Communicate(text, "ru-RU-DmitryNeural").save(str(mp3))
    subprocess.run(
        [imageio_ffmpeg.get_ffmpeg_exe(), "-loglevel", "error", "-y", "-i", str(mp3),
         "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(output)],
        check=True,
    )


def recognize(model: Model, path: Path, grammar: list[str] | None = None) -> dict[str, object]:
    with wave.open(str(path), "rb") as audio:
        recognizer = KaldiRecognizer(model, 16000, json.dumps(grammar, ensure_ascii=False)) if grammar else KaldiRecognizer(model, 16000)
        detected_at_ms = None
        aliases = set(RECOGNIZED_WAKE_ALIASES)
        while chunk := audio.readframes(4000):
            recognizer.AcceptWaveform(chunk)
            partial = json.loads(recognizer.PartialResult()).get("partial", "")
            if detected_at_ms is None and any(alias in partial.split() for alias in aliases):
                detected_at_ms = round(audio.tell() * 1000 / audio.getframerate())
        text = json.loads(recognizer.FinalResult()).get("text", "")
        if detected_at_ms is None and any(alias in text.split() for alias in aliases):
            detected_at_ms = round(audio.tell() * 1000 / audio.getframerate())
        return {"text": text, "wake_detected_at_audio_ms": detected_at_ms}


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("model", type=Path)
    args = parser.parse_args()
    SetLogLevel(-1)
    model = Model(str(args.model))
    vocabulary = {word: model.vosk_model_find_word(word) >= 0 for word in ["эма", "эмма", "ты", "тут", "какие", "у", "меня", "задачи"] + FALLBACK_VARIANTS}
    restricted_grammar = [phrase for phrase in RECOGNIZED_WAKE_ALIASES if all(model.vosk_model_find_word(word) >= 0 for word in phrase.split())] + ["[unk]"]
    results = {}
    with tempfile.TemporaryDirectory(prefix="noema-vosk-probe-") as directory:
        root = Path(directory)
        for index, text in enumerate(WAKE_VARIANTS + FALLBACK_VARIANTS):
            wav = root / f"sample-{index}.wav"
            await synthesize(text, wav)
            results[text] = {
                "unrestricted": recognize(model, wav),
                "restricted": recognize(model, wav, restricted_grammar),
            }
    report = {"model": args.model.name, "vocabulary": vocabulary, "restricted_grammar": restricted_grammar, "recognition": results}
    # ASCII escapes keep CI/PowerShell logs lossless; JSON readers recover Cyrillic.
    print(json.dumps(report, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
