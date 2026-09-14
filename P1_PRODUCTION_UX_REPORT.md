# NOEMA — P1 PRODUCTION UX FIX

Дата: 2026-09-14
Scope: только подтверждённые P1 regressions из `PRODUCTION_REGRESSION_AUDIT.md`. P0 provider/Telegram final-delivery, knowledge, ingestion, model routing, redesign и deploy не изменялись.

## CHAT_SCROLL_ROOT_FIX

- `ChatScrollController` — единственный owner `chat.scrollTop`.
- Состояния сведены к `FOLLOWING_BOTTOM` и `READING_HISTORY`; layout/stream/textarea/viewport только уведомляют controller.
- Все corrections coalesced через controller `requestAnimationFrame` scheduler.
- Исправлена гонка late `ResizeObserver`/keyboard transition и гонка явной отправки сообщения с незавершённым anchor restore.
- В `READING_HISTORY` входящий stream не возвращает пользователя вниз; в `FOLLOWING_BOTTOM` stream, send и keyboard resize сохраняют bottom.
- Long Chat скрыт splash-слоем до завершения initial bottom positioning; short history остаётся внизу над composer.

## STABLE_IDS

- History API возвращает `message_id`, `role`, полный `content` и `created_at` при наличии.
- DOM identity использует `message-${message_id}`, а не `role-index`.
- Pending user bubble и один streaming assistant bubble после completion получают canonical IDs без destructive rerender.
- Anchor snapshot хранит stable message key и offset.

## VIEWPORT_CONTROLLER

- Добавлен единый `ChatViewportController` для `visualViewport`, Telegram viewport events и CSS variables.
- Viewport handlers обновляют measurements и передают snapshot в scroll controller; напрямую scroll не восстанавливают.
- Keyboard open/close сохраняет bottom либо stable message anchor в зависимости от состояния.
- `window.scrollTo()` не используется для keyboard compensation.

## SAFE_AREA_LIFECYCLE

- Telegram listeners регистрируются до `ready()`, `expand()` и fullscreen request.
- Обрабатываются `safeAreaChanged`, `contentSafeAreaChanged`, `viewportChanged`, `fullscreenChanged`, `fullscreenFailed`.
- После fullscreen transition safe area измеряется повторно.
- Fallback order: fresh content safe inset → safe inset → Telegram CSS variables → browser `env()` → controlled Telegram top fallback.
- Без user data доступны числовые `window.NoemaViewportDiagnostics`: `safe_top`, `content_safe_top`, `fullscreen`, `viewport_height`, `visual_viewport_height`.

## VISUAL_STATE_MACHINE

- Общие состояния: `IDLE`, `REQUESTING`, `MEMORY`, `TOOL`, `STREAMING`, `SPEAKING`, `ERROR`.
- `REQUESTING` показывает «Думаю…»; `MEMORY` и `TOOL` появляются только после реально выполненного tool с truthful label.
- Первый visible token переводит UI в `STREAMING` и убирает progress text.
- `SPEAKING` управляет voice/sphere state, не заменяя текст ответа.
- Mini App использует state machine; Telegram отображает те же backend states через ephemeral chat actions либо experimental draft, без постоянных status messages.

## MINIAPP_DONE_RECONCILIATION

- Mini App на terminal event сравнивает concatenated deltas с canonical `done.text`.
- При потерянном network tail тот же bubble дополняется/заменяется canonical text; duplicate bubble не создаётся.
- После done выполняется локальное canonical-ID reconciliation без полного history rerender.

## VOICE_PARITY

- Все server TTS entry points получают `chat_id` либо уже resolved preferences; отсутствие identity теперь fail-fast.
- Telegram voice, `voice_and_text`, experimental draft и group-compatible path разрешают semantic gender пользователя через live admin `tts_male_voice`/`tts_female_voice`.
- Изменение admin voice автоматически применяется к пользователям того же gender без переписывания user storage.
- Telegram использует server-capable Edge provider; browser TTS явно остаётся Mini App-only. Admin UI больше не обещает одинаковую provider semantics для несовместимых каналов.

## VOICE_MODE

- Канонические modes: `text`, `voice`, `voice_and_text`.
- Legacy/invalid `auto` read-normalize к `text` без массовой записи в DB; новые значения `auto` отклоняются.
- Telegram и Mini App используют одинаковую семантику трёх modes.

## TESTS

- Python full suite: `211 tests`, `OK`.
- P1 browser regression A–J/O: `PASS`.
- Existing UI/UX browser matrix, 320/360/375/390/414/430 px: `PASS`.
- Existing Mini App visual matrix, 7 viewports × 9 screens, edge swipe, composer and fullscreen boot: `PASS` после удаления устаревших ожиданий retired USER model/composer markup.
- Chat composer visual runtime: `PASS`.
- JavaScript syntax, Python compile и `git diff --check`: `PASS`.

Покрытие обязательной матрицы:

- A: long Chat opens at latest with no visible jump — browser.
- B: short Chat bottom-aligned — browser.
- C: stream at bottom remains stable — browser.
- D: reading history is not forced down — browser.
- E/F: keyboard open/close at bottom and while reading — browser.
- G: stable IDs survive load/reconcile — Python + browser.
- H: dropped tail repaired from `done.text` in one bubble — browser.
- I: first token clears «Думаю…» — browser.
- J: tool status only for actual tool — Python + browser.
- K/L/M: female, male and live admin voice changes — Python.
- N: `text` / `voice` / `voice_and_text` parity — Python/source contract.
- O: `fullscreenChanged` resynchronizes safe area — browser.

## DEVICE_QA

`BROWSER_QA: PASS`

`IPHONE_TELEGRAM_QA: NOT RUN / DEVICE PENDING`

В текущей среде нет подключённого физического iPhone с Telegram. Поэтому real-device gate для header safe-area, fullscreen lifecycle и gesture feel не закрыт программными эмуляциями.

## COMMIT

Изменения подготовлены как один focused P1 commit поверх завершённого P0 commit `217c34e`. Push и deploy не выполняются. Итоговый commit hash указывается в delivery summary после создания commit.

## P1_PRODUCTION_UX

`IMPLEMENTATION_PASS_DEVICE_PENDING`
