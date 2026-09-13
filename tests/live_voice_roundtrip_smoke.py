"""Opt-in neutral Edge TTS -> production batch STT smoke; retains no audio/text."""
import asyncio
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bot


async def main():
    workspace = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
    bot.DB = Path(workspace.name) / "roundtrip.db"
    bot.TELEMETRY_ENABLED = True
    bot.init_db()
    bot.reset_runtime_metric_series()
    succeeded = 0
    for _ in range(3):
        audio = await bot.make_voice(
            "Эма, какие у меня задачи сегодня?",
            preferences={"voice": "ru-RU-DmitryNeural", "speed": 1.0, "pitch": 1.0, "volume": 1.0},
        )
        try:
            if bot.transcribe(990000002, audio):
                succeeded += 1
        finally:
            Path(audio).unlink(missing_ok=True)
    metric = bot.runtime_metric_export()["metrics"]["stt_final_ms"]
    print(json.dumps({**metric, "recognized": succeeded, "audio_retained": False}))


if __name__ == "__main__":
    asyncio.run(main())
