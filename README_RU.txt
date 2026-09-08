NOEMA TELEGRAM PROTOTYPE v8 CLEAN

This archive is rebuilt from scratch. It does NOT inherit v5/v6 bot.py.

WHAT IS INCLUDED
- Telegram text + voice
- Voxtral STT
- Edge TTS
- OpenRouter LLM + fallback models
- persistent SQLite
- people + structured facts + interactions
- expenses + update last expense
- tasks
- reminders
- notes
- today plan
- morning briefing
- live weather via Open-Meteo
- live exchange rate
- live news/web search
- multi-market product search:
  Ozon / Wildberries / Yandex Market / Megamarket / Avito
- semantic filter for "ваза" so Lada/ВАЗ/autoparts are rejected
- /status shows BUILD, PID and exact project PATH

IMPORTANT MIGRATION
To keep your old data:
1. STOP the old bot.
2. Copy old noema_test.sqlite3 into this folder.
3. Copy/fill .env.
4. Run SETUP.bat ONCE.
5. Run CHECK_BUILD.bat.
6. Run RUN.bat.

EXPECTED STARTUP CONSOLE:
NOEMA STARTED
BUILD: v8-CLEAN-2026-09-03
PID: ...
PATH: C:\...\Noema_Telegram_Prototype_v8_CLEAN

EXPECTED STATUS BUTTON:
Build: v8-CLEAN-2026-09-03
PID: ...
Path: ...
Weather: Open-Meteo
Shopping: multi-market

TESTS
- "Какая погода сейчас в Санкт-Петербурге?"
- "Курс доллара к рублю сейчас"
- "Свежие новости по ИИ"
- "Найди в интернете последние новости OpenRouter"
- "Хочу купить вазу для цветов до 2000 рублей"
- "200 рублей потратил на бензин"
- "а вчера 1000 в магазине"
- "одежда"
- "сколько я всего потратил?"
