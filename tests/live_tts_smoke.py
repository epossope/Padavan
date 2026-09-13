"""Opt-in neutral Edge TTS smoke; stores no audio or user text."""
import asyncio
import json
import os
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bot


def percentile(values, fraction):
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = int(position)
    upper = min(len(ordered) - 1, lower + 1)
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


async def main():
    count = max(1, min(20, int(os.getenv("NOEMA_TTS_SMOKE_COUNT", "20"))))
    values = []
    prefs = {"voice": "ru-RU-DmitryNeural", "speed": 1.0, "pitch": 1.0, "volume": 1.0}
    for _ in range(count):
        started = time.perf_counter()
        path = await bot.make_voice("Готово. Проверка голосового ответа Noema.", preferences=prefs)
        try:
            if Path(path).stat().st_size < 128:
                raise RuntimeError("TTS returned an empty audio file")
            values.append((time.perf_counter() - started) * 1000)
        finally:
            Path(path).unlink(missing_ok=True)
    print(json.dumps({
        "count": len(values), "avg": round(statistics.fmean(values), 1),
        "p50": round(percentile(values, .5), 1), "p95": round(percentile(values, .95), 1),
        "max": round(max(values), 1), "audio_retained": False,
    }))


if __name__ == "__main__":
    asyncio.run(main())
