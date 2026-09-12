# Environment audit — production / Amvera

Аудит сделан по всем `os.getenv`/env reads в исходниках, инструментах и
тестах. Значения секретов не читаются и не записываются в этот документ.
Amvera должна содержать только переменные из `USED` (и временно из `LEGACY`,
если они уже развернуты).

## USED — production runtime

| Переменные | Назначение | Действие в Amvera |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | Токен Telegram-бота | Required secret |
| `OPENROUTER_API_KEY` | Общий ключ LLM и batch STT | Required secret |
| `QUICK_ACTIONS_BASE_URL` | Публичный HTTPS origin Mini App | Required, если Mini App включён |
| `ADMIN_CHAT_IDS` | Доступ к admin telemetry и Realtime Beta | Optional |
| `FAST_MODEL`, `FAST_MODEL_PROVIDERS`, `FAST_MODEL_ALLOW_PROVIDER_FALLBACK` | Bootstrap defaults для fast router | Optional; editable live admin |
| `STRONG_MODEL`, `STRONG_MODEL_PROVIDERS`, `STRONG_MODEL_ALLOW_PROVIDER_FALLBACK` | Bootstrap defaults для strong fallback | Optional; editable live admin |
| `MODEL_CATALOG`, `CHAT_MAX_TOKENS` | Allowed model catalogue и лимит | Optional; catalogue editable live admin |
| `VISION_MODEL`, `VISION_FALLBACK_MODELS` | Bootstrap defaults Vision | Optional; editable live admin |
| `BATCH_STT_MODEL`, `BATCH_STT_TIMEOUT_SEC` | Canonical production batch STT | Optional; defaults `mistralai/voxtral-mini-transcribe`, `180` |
| `MISTRAL_API_KEY`, `MISTRAL_REALTIME_MODEL`, `MISTRAL_CLIENT_SESSIONS_URL` | Только admin-only Experimental Realtime Beta | Key is secret; model editable live admin |
| `TTS_PROVIDER`, `TTS_FALLBACK_PROVIDER`, `EDGE_VOICE`, `VOICE_REPLY_MODE` | Bootstrap TTS and default reply mode | Optional; editable live admin |
| `TELEGRAM_DRAFT_STREAMING_ENABLED`, `TELEGRAM_DRAFT_MIN_INTERVAL`, `TELEGRAM_DRAFT_MAX_INTERVAL`, `TELEGRAM_DRAFT_MIN_CHARS` | Streaming drafts Telegram | Optional; defaults есть |
| `TELEGRAM_SEND_RETRIES`, `TELEGRAM_CONNECT_TIMEOUT`, `TELEGRAM_READ_TIMEOUT`, `TELEGRAM_WRITE_TIMEOUT`, `TELEGRAM_POOL_TIMEOUT`, `TELEGRAM_CONNECTION_POOL_SIZE` | Telegram HTTP reliability | Optional; defaults есть |
| `REMINDER_TICK_SECONDS` | Reminder scheduler | Optional; clamped 15–30 sec |
| `TELEMETRY_ENABLED`, `TELEMETRY_SERIES_LIMIT` | Bounded numeric telemetry | Optional; disabled by default |
| `TIMEZONE`, `DEFAULT_CITY`, `MAX_FILE_MB`, `DATA_DIR`, `NOEMA_QUICK_PORT` | Runtime defaults, storage and HTTP listener | Optional; Amvera default uses `/data`, port `8080` |
| `OPENROUTER_MANAGEMENT_API_KEY`, `USER_SECRETS_MASTER_KEY`, `NOEMA_USER_MONTHLY_LIMIT_USD` | Optional managed per-user OpenRouter keys | Set all needed values together, otherwise omit all |

## LEGACY — supported for one release

| Переменная | Замена | Runtime rule | Amvera action |
|---|---|---|---|
| `STT_MODEL` | `BATCH_STT_MODEL` | Used only when `BATCH_STT_MODEL` is absent | Move its value to `BATCH_STT_MODEL`; remove legacy key after the next release |
| `MODEL` | `FAST_MODEL` | Used only when `FAST_MODEL` is absent | Move its value to `FAST_MODEL`; remove after the next release |
| `FALLBACK_MODELS` | `STRONG_MODEL` | First value used only when `STRONG_MODEL` is absent | Move first value to `STRONG_MODEL`; remove after the next release |
| `AVAILABLE_MODELS` | `MODEL_CATALOG` | Used only when `MODEL_CATALOG` is absent | Move its value to `MODEL_CATALOG`; remove after the next release |
| `BOT_TOKEN` | `TELEGRAM_BOT_TOKEN` | Historical fallback is still read | Prefer canonical name; do not add it to new deployments |

## UNUSED — removed from runtime and `.env.example`

| Переменные | Why safe to remove | Amvera action |
|---|---|---|
| `VOICE_CONVERSATION_ENABLED`, `VOICE_MODE`, `VOICE_SESSION_TIMEOUT_SEC` | No production code path consumed them; realtime is controlled per admin in Mini App settings | Delete if present |
| `VAD_SPEECH_THRESHOLD`, `VAD_END_SILENCE_MS`, `VAD_MIN_SPEECH_MS` | Old server-side voice settings; current VAD is isolated to lazy-loaded browser Beta and does not read them | Delete if present |
| `STT_PROVIDER`, `STT_FALLBACK_MODEL`, `STT_REALTIME_MODEL` | No runtime reader exists; production is OpenRouter batch STT and Beta uses `MISTRAL_REALTIME_MODEL` | Delete if present |

## Development / test only — never set in Amvera

| Variables | Consumer |
|---|---|
| `PLAYWRIGHT_PATH` | `tests/miniapp_visual.cjs`, `tests/vosk_browser_smoke.cjs` |
| `NOEMA_VOSK_APP_URL`, `NOEMA_VOSK_MODEL_URL`, `NOEMA_VOSK_VOICE_URL` | Local Vosk browser smoke test |
| `TELEGRAM_TEST_CHAT_ID` | Explicit live retrieval test |

## Batch STT precedence

`BATCH_STT_MODEL` → legacy `STT_MODEL` → built-in default.

The canonical batch path is OpenRouter transcription with the shared
`OPENROUTER_API_KEY`; realtime Mistral variables never alter this path.

## Admin runtime config

`AI & Voice` in the Mini App settings is visible only to `ADMIN_CHAT_IDS`.
It uses the existing `app_settings` SQLite storage, stores only safe model and
voice values plus `updated_at` / `updated_by`, and immediately affects new
requests without a restart. It never reads, displays or copies secrets.

Effective precedence is: user-specific model/reply preference → admin override
→ ENV bootstrap default → built-in default. `Reset to ENV` removes only the
admin override for that field. The live-editable fields are FAST/STRONG model
routes, Vision, batch STT model, TTS provider/fallback/voice, default voice
reply mode, Realtime Beta model and the allowed model catalogue.
