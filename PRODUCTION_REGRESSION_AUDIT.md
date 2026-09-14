# NOEMA — PRODUCTION REGRESSION AUDIT

**Дата аудита:** 2026-09-14
**Ревизия:** `3b79f4b` (`master`, совпадает с `origin/master`)
**Режим:** только аудит; код, commit, push и deploy не выполнялись
**Итоговый статус:** `AUDIT_COMPLETE`

## 1. EXECUTIVE SUMMARY

Аудит подтвердил две независимые критические production-проблемы и несколько архитектурных причин нестабильности UX.

1. **P0: reasoning Qwen реально приходит как обычный `delta.content`, без поля `reasoning` и без `<think>`-тегов.** Текущий фильтр умеет отбрасывать отдельные reasoning-поля и tagged-блоки, но считает untagged `content` пользовательским ответом. Поэтому внутренний текст немедленно попадает в общий visible stream, затем в Telegram/Mini App и в canonical history. Commit `3b79f4b` усилил наблюдаемость и тесты существующего фильтра, но не устранил этот provider-specific контракт.
2. **P0: production Telegram adapter намеренно обрезает любой ответ, который требует больше одного Telegram chunk, до первых 3600 символов с многоточием.** Mini App получает полный общий stream. Следовательно, каналы используют общий backend runtime, но не один и тот же финальный delivery accumulator.
3. **P1: после первой успешной отправки любой невосстановленный Telegram edit failure оставляет в чате старый префикс.** Финальный flush не создаёт replacement-сообщение, если `editing_available == False`. Это объясняет короткие обрывки даже при полном canonical response.
4. **P1: visual states существуют только частично и теряются в adapters.** Backend выдаёт `Думаю…` и post-tool состояния. Mini App их передаёт, но не снимает status на первом токене. Обычный Telegram streaming path события `progress` и `tool` игнорирует полностью.
5. **P1: Chat имеет один класс scroll controller, но несколько неконсолидированных источников mutations.** Некоуплированные `requestAnimationFrame` из viewport/textarea resize могут применять устаревшие snapshots. После полного `load() → render()` transient anchor исчезает, а history не содержит стабильных message IDs; restore может оставить новый scroller наверху.
6. **P1: safe-area рассчитана правильно только при свежих Telegram значениях, но lifecycle неполон.** Значения считываются до `requestFullscreen()`, нет обработки `fullscreenChanged/fullscreenFailed`, а CSS после `6ed93f2` оставляет лишь 8 px сверх переменной. Нулевой/устаревший `contentSafeAreaInset` поэтому немедленно приводит header под Telegram overlay.
7. **Global model для chat действительно консолидирован.** Telegram text, Mini App text, voice requests и tool rounds используют live ADMIN/ENV `fast_model`, затем глобальный `strong_model`. Legacy per-user model values больше не выигрывают precedence. Существующие background reminder/briefing jobs вообще не вызывают LLM.
8. **Global voice консолидирован не полностью.** Mini App держит controller вне Chat и корректно разрешает semantic gender на текущие admin voice IDs. Однако legacy `send_answer()` вызывает `make_voice(answer)` без `chat_id`, поэтому Telegram voice input, group path и experimental draft path теряют пользовательский gender. Server `make_voice()` также всегда вызывает Edge TTS независимо от admin `tts_provider`.

Рекомендованный порядок: сначала закрыть provider contract и очистить загрязнённую history (P0), затем заменить Telegram delivery на атомарный финальный контракт без потери chunks (P0), после этого унифицировать state machine, scroll/viewport lifecycle, safe-area lifecycle и voice routing (P1). CSS и swipe упрощать только после стабилизации этих контрактов.

## 2. CURRENT ARCHITECTURE MAP

### 2.1 Text / tools / persistence

```text
Telegram private text ─┐
                       ├─> bot.stream_agent_response()
Mini App /chat/stream ─┘       │
                               ├─ direct_live_request()
                               └─ request_chat_stream(OpenRouter)
                                      │ SSE data frames
                                      v
                               iter_sse_json()
                                      v
                               StreamAccumulator.add()
                                      │
                        visible delta + tool calls
                                      v
                          channel-specific adapter
                             │                │
                    Telegram send/edit   Mini App NDJSON
                             │                │
                             └──────┬─────────┘
                                    v
                         add_message() / messages DB
```

Canonical ownership находится в `bot.stream_agent_response()`: там выбирается глобальная модель, выполняются tools, формируется answer и делается persistence. Однако видимый текст перед persistence уже прошёл недостаточный фильтр, поэтому «canonical» не означает «безопасный».

### 2.2 Voice

```text
Mini App sphere (любой screen)
  -> VoiceController (global singleton)
  -> /voice/transcribe
  -> /chat/stream -> global chat model/tools
  -> SpeechQueue
       -> Edge: /speech -> make_voice(chat_id, preferences)
       -> Browser: SpeechSynthesis

Telegram voice
  -> transcribe
  -> ask() (legacy non-streaming core)
  -> send_answer()
  -> make_voice(answer)        [chat_id потерян]
```

### 2.3 Mini App shell

`screens.js` полностью пересоздаёт screen DOM через `render()`. `VoiceController` создан глобально в `app.js` и переживает смену screen. Chat scroll controller также глобальный, но bind/detach следует за пересозданием `.chat`. Viewport события приходят из Telegram SDK (`screens.js`) и `VisualViewport` (`mobile.js`). CSS загружается в порядке `style.css → mobile.css → refinement.css → design-match.css`; при одинаковой специфичности выигрывает последний файл.

## 3. BUG MATRIX

| ID | SYMPTOM | ROOT CAUSE | FILE / FUNCTION / AREA | INTRODUCED BY | SEVERITY | CONFIDENCE | DEPENDENCIES |
|---|---|---|---|---|---|---|---|
| B1 | Пользователь видит «Пользователь спрашивает… / Я должен ответить…» | Qwen/Alibaba выдаёт untagged chain-of-thought в `delta.content`; request не отключает reasoning; tag-only filter пропускает его | `bot.py:3659-3683` `request_chat*`; `streaming_runtime.py:124-143` `StreamAccumulator.add`; `bot.py:3748-3760` | Provider behavior + request path `94e3b523`; неполный sanitizer `891cbee`; `3b79f4b` не закрыл | **P0** | **Высокая, live reproduction** | OpenRouter/Qwen route; canonical history |
| B2 | Telegram получает ответ только до `…`, Mini App полный | Adapter берёт только `chunks[0]`; при нескольких chunks принудительно рендерит `visible[:3600] + …` | `bot.py:3979-3990` `_telegram_stream_text` | `0c82385` | **P0** | **Высокая, direct code path** | Telegram text mode; длинный answer |
| B3 | Telegram остаётся на коротком префиксе/фрагменте | После exhausted/invalid edit `editing_available=False`; final flush выполняется только при `elif editing_available`, replacement отсутствует | `bot.py:3993-4034`, `4037-4118` | `0c82385` | **P1** | **Высокая, direct code path** | Network/RetryAfter/BadRequest/Forbidden после initial send |
| B4 | Нет «Думаю/Вспоминаю/…» в Telegram | Обычный Telegram adapter обрабатывает только `delta/done/cancelled`; `progress/tool` игнорируются | `bot.py:3691-3710`, `3815-3816`, `4037-4133` | Runtime `94e3b523`; adapter omission `0c82385` | **P1** | Высокая | Default `TELEGRAM_DRAFT_STREAMING_ENABLED=false` |
| B5 | Mini App status висит поверх уже идущего ответа | `say(progress)` снимается только после `load()`/TTS idle, не на первом visible delta | `miniapp/app.js:173`, `215-217` | Текущая voice/state интеграция `955c9f8`/`3b79f4b` | **P1** | Высокая | Mini App stream |
| B6 | Chat прыгает при keyboard/stream; после reload может оказаться не у bottom | Нескоординированные viewport rAF snapshots + transient anchor удаляется при full render; history не возвращает IDs, fallback key зависит от индекса | `miniapp/app.js:29-47,56,110,217`; `mobile.js:27-28`; `bot.py:1850-1863` | Scroll controller `8921fed`; последующие `13da45f`, `6ed93f2` | **P1** | Средне-высокая, статическая трассировка | VisualViewport animation; history window=50; DOM rerender |
| B7 | Header конфликтует с Telegram «Закрыть/…» на iPhone fullscreen | Safe area считывается до fullscreen; нет fullscreen lifecycle; нет Telegram CSS-var fallback; финальный CSS buffer только 8/6 px | `miniapp/screens.js:171-190`; `mobile.js:27-28`; `design-match.css:1118-1120,1190+` | Safe-area `891cbee`; компрессия `6ed93f2` | **P1** | Средне-высокая; нужен real-device capture для размера | Telegram iOS SDK timing/fullscreen |
| B8 | Telegram voice может использовать мужской voice несмотря на female preference | `send_answer()` не передаёт `chat_id` в `make_voice()` | `bot.py:4569-4595` | Legacy line из initial history; стало регрессией после per-user prefs `955c9f8` / semantic consolidation `3b79f4b` | **P1** | Высокая | Telegram voice input, groups, draft path |
| B9 | Admin TTS provider не одинаково действует по каналам | `make_voice()` всегда вызывает `edge_tts.Communicate`; provider switch реализован только в Mini App client | `bot.py:4546-4555`; `miniapp/app.js:137-160` | Накопленная voice architecture | **P1** | Высокая | `tts_provider=browser` особенно заметен |
| B10 | Composer/orb visual regressions возвращаются при небольших CSS/cache изменениях | Несколько competing definitions и 46 `!important`; поздний design patch определяет geometry/transparent background | `style.css`, `refinement.css:48`, `design-match.css:986-1059,1147-1180` | `629db7b`, `afd8e4c`, `6ed93f2`; cache fix существует только в `codex/checkbox-preview` | **P2** | Высокая для debt, средняя для конкретного square на текущем cache | Asset version/cache; cascade order |
| B11 | Swipe иногда захватывает горизонтальное намерение далеко от edge | Active zone = 34% ширины (до 144 px), `touch-action: pan-y`, собственная animation параллельно native BackButton | `miniapp/mobile.js:30-67`; `design-match.css:700-704` | `e185756`, `eb23373`, текущие thresholds `6ed93f2` | **P2** | Высокая | Telegram WebView pointer arbitration |
| B12 | Два Mini App chat entry points могут разойтись | Streaming UI использует `/chat/stream`, но generic action `chat` всё ещё вызывает legacy `core.ask()` | `miniapp_api.py:283-289,565-648` | Legacy architecture | **P2** | Высокая | Любой старый/внешний caller generic endpoint |

## 4. LLM / REASONING PIPELINE

### 4.1 Фактический provider response

Выполнена read-only live diagnostic request с текущей основной моделью `qwen/qwen3.5-flash-02-23` и текущим provider route `alibaba` (`allow_fallbacks=false`, `require_parameters=true`). Prompt: «Какой сегодня день недели?». Секреты в отчёт не записывались.

Наблюдение без `reasoning` configuration:

- HTTP 200, provider `Alibaba`;
- 130 SSE JSON events;
- каждый meaningful delta имел ключ `content`; отдельных `reasoning`, `reasoning_details`, `analysis`, `thinking` не было;
- `<think>`, `<analysis>`, `<reasoning>` отсутствовали;
- первые tokens содержали английский внутренний разбор: модель описывала намерение пользователя, доступ к дате и необходимость вычислить день недели;
- `finish_reason=length`, visible content 1033 символа.

Контрольные requests:

- `reasoning: {"exclude": true}`: HTTP 200, reasoning всё равно пришёл untagged в `content` (1161 символ, `finish_reason=stop`);
- `reasoning: {"enabled": false}`: HTTP 200, пришёл только короткий русский ответ (48 символов), без внутренних маркеров.

Это согласуется с metadata модели: она объявляет параметры `reasoning`/`include_reasoning`. Контракт параметров описан в [OpenRouter reasoning tokens documentation](https://openrouter.ai/docs/guides/best-practices/reasoning-tokens). Существенное production-наблюдение здесь получено не из документации, а из фактических SSE текущего route.

### 4.2 Точка первого попадания в visible accumulator

1. `bot.request_chat_stream()` (`bot.py:3672-3683`) формирует payload с model/messages/temperature/max_tokens/tools/provider, **но без `reasoning.enabled=false`**.
2. `iter_sse_json()` (`streaming_runtime.py:171-183`) корректно декодирует каждый `data:` frame и не меняет schema.
3. `StreamAccumulator.add()` (`streaming_runtime.py:124-154`) считает поля `reasoning/reasoning_details/analysis/thinking` dropped, но затем независимо читает `delta.content`.
4. `VisibleContentFilter.feed()` (`streaming_runtime.py:27-72`) поддерживает split tags между chunks через `tag_buffer` и `hidden_depth`. Сценарий `<thi` + `nk>hidden</thi` + `nk>answer` обработан правильно.
5. Untagged reasoning не имеет machine-readable boundary, поэтому проходит в `self.content`, `emitted`, `visible_chars`.
6. `bot.stream_agent_response()` (`bot.py:3748-3760`) немедленно yield-ит этот `emitted` как `{"type":"delta"}`.
7. Финальный `message.content` снова проходит тот же tag-only sanitizer (`bot.py:3791`) и сохраняется `add_message()` (`bot.py:3792-3793`).
8. При следующих запросах `conversation_context()` и `history()` снова применяют только tag filter (`bot.py:1831-1845,1860-1863`). Уже сохранённый untagged reasoning не удаляется и может попадать в следующие prompts.

**Точный root cause:** отсутствие provider-level отключения reasoning в `request_chat()` и `request_chat_stream()` при том, что downstream sanitizer способен распознать лишь отдельные fields/tags, а текущий Qwen route помещает chain-of-thought в обычный untagged `content`.

### 4.3 Telegram и Mini App

До channel boundary source общий: оба читают deltas `stream_agent_response()`. Поэтому reasoning leak одинаково доступен обоим каналам и canonical history. После boundary поведение разное: Mini App append-ит каждый delta, Telegram повторно рендерит accumulated prefix и ограничивает его одним bubble.

### 4.4 Почему тесты не поймали

`tests/test_streaming_runtime.py`, `tests/test_realtime_delivery.py` и `tests/test_model_stream_consolidation.py` используют synthetic fixtures, где private text находится в отдельном `reasoning` field и/или внутри `<think>`. Нет fixture, в котором provider возвращает **только** untagged internal prose в `delta.content`, и нет contract test текущей Qwen/provider комбинации. Поэтому `196 tests PASS` совместимы с live leak.

Необходимые regression tests:

- outbound payload contract: reasoning disabled для reasoning-capable chat route;
- captured SSE fixture фактического Alibaba/Qwen schema;
- untagged-content safety policy (с заранее определённым product contract, а не хрупкими regex-фразами);
- assertion, что одинаковый final visible text попадает в Mini App, Telegram и DB;
- migration/audit test для уже загрязнённой assistant history.

## 5. TELEGRAM STREAM PIPELINE

### 5.1 Default production path

В текущей конфигурации `TELEGRAM_DRAFT_STREAMING_ENABLED` по умолчанию `false`; private text идёт через `stream_answer_to_telegram()` (`bot.py:4037-4133`). Pipeline:

```text
shared delta
 -> accumulated += delta.text
 -> _telegram_stream_text(accumulated)
 -> initial send после >= TELEGRAM_DRAFT_MIN_CHARS (default 24)
 -> throttled edits
 -> done.text
 -> final edit, только если editing_available
```

`3b79f4b` добавил threshold перед initial send. Это устраняет прежнюю прямую причину literal one-character initial bubble, введённую `0c82385`, но не закрывает последующие edit failures.

### 5.2 Конкретные места обрезания

**Длинные ответы:** `_telegram_stream_text()` вызывает `TelegramRenderer.chunks(visible)`, но берёт только `chunks[0]`. При `len(chunks)>1` он отбрасывает все chunks и строит новый текст только из `visible[:3600] + "…"`. Final flush вызывает ту же функцию. Это детерминированная потеря данных, а не HTML sanitizer или race.

**Короткие обрывки:** `telegram_edit_with_retry()` возвращает `None` после неподдерживаемого `BadRequest`, `Forbidden`, exhausted `RetryAfter`, `TimedOut` или `NetworkError`. Caller превращает это в `editing_available=False`. После `done` ветка `elif editing_available` пропускает final edit. Уже отправленный prefix остаётся навсегда. Комментарий мотивирует это защитой от duplicate, но текущая цена защиты — подтверждённая возможность incomplete delivery.

### 5.3 Draft path

Experimental `stream_answer_to_telegram_draft()` использует Telegram draft API, показывает `accumulated[-4096:]` и после завершения вызывает `send_answer(final)`. Persistent final там chunked корректно через `TelegramRenderer`, но сам draft может показывать только хвост. Этот path сейчас не default и дополнительно наследует voice preference bug `send_answer()`.

### 5.4 Source of truth: ответ

**Формально общий runtime есть, фактически channel delivery различается.** Canonical `done.text` общий, но Mini App отображает накопленные deltas без Telegram ограничения, а Telegram adapter создаёт собственное truncated representation и может отказаться от final flush. Поэтому observable user response не имеет единого source of truth.

### 5.5 Missing tests

- final text > 4096 и >2 chunks;
- initial send success + edit `TimedOut/RetryAfter/BadRequest` + `done`;
- final edit failure with readback/correlation semantics;
- parity assertion между `done.text`, Telegram-delivered chunks и Mini App answer;
- cancellation до/после initial send;
- real Telegram rate-limit/edit integration. Текущие mocks возвращают successful edits и короткие payloads.

## 6. MINI APP STREAM PIPELINE

`miniapp_api.chat_stream()` (`miniapp_api.py:565-648`) запускает `core.stream_agent_response()` в worker thread и передаёт события NDJSON без изменения `delta/progress/tool/done`. Disconnect не отменяет core job: он продолжает persistence и отправляет completion notification.

`streamChat()` (`miniapp/app.js:215-217`) складывает `answer += event.text` и рендерит delta через один rAF buffer. `done.text` не используется для reconciliation. При идеальном stream это совпадает с canonical answer; при пропущенном/повреждённом network tail клиент не сверяет локальный answer с canonical final.

Visual state chain:

| Runtime stage | Backend event | NDJSON | Mini App behavior |
|---|---|---|---|
| request start | `progress: Думаю…` | Да | `say()` в `#composer-status` на Chat |
| context/history build | Нет отдельного event | Нет | Остаётся `Думаю…` |
| memory/tool execution | Event только **после** execution | Да | Показывает `Вспоминаю…`/другое после факта |
| first visible token | `delta` | Да | Bubble начинает расти, status не очищается |
| completion | `done` | Да | Client игнорирует `done.text`, затем `load()` |
| settled | — | — | После load/TTS idle вызывает `say('')` |

Заявленные состояния `Проверяю задачи…`, `Получаю данные…`, `Готовлю ответ…` не образуют backend state machine. Имеются только `Думаю…`, `Вспоминаю…`, `Проверяю информацию…`, `Сохраняю изменения…`, `Выполняю действие…`; tool labels публикуются после выполнения. CSS скрывает global `#notice` на Chat, но это не причина исчезновения: `say()` целенаправленно использует локальный `#composer-status`.

Отдельный debt: generic `/api/v1/miniapp` action `chat` вызывает `core.ask()` — legacy non-streaming path. Текущий UI использует `/chat/stream`, но два контракта остаются доступны.

## 7. CHAT SCROLL / VIEWPORT ARCHITECTURE

### 7.1 Все места, меняющие или восстанавливающие scroll

1. `ChatScrollController.bind()` (`app.js:33`) — initial `scrollToBottom()` или `restore()` через rAF.
2. `handleScroll()` (`app.js:37`) — переключает follow mode и отменяет pending controller frame.
3. `IntersectionObserver` sentinel (`app.js:33,36`) — меняет `followingBottom/unseen`.
4. `ResizeObserver` на `.chat` и `.chat-inner` (`app.js:33,38`) — вызывает scheduled bottom scroll при follow mode.
5. `handleViewportResize()` (`app.js:39`) — snapshot, затем отдельный неконсолидированный rAF с bottom/restore.
6. `restore()` (`app.js:40`) — напрямую изменяет `thread.scrollTop += anchor delta`.
7. `schedule()/scrollToBottom()` (`app.js:41-42`) — rAF и `thread.scrollTo(top=scrollHeight)`.
8. `incoming()/addPendingUser()` (`app.js:43-45`) — включает follow mode и schedules bottom.
9. `load()` (`app.js:56`) — сохраняет snapshot, полностью пересоздаёт DOM через `render`, затем новый bind.
10. `streamChat()` (`app.js:217`) — rAF DOM append для каждого batch deltas; это активирует ResizeObserver и `incoming()`.
11. `resizeChatInput()` (`app.js:219`) — inline меняет textarea height и вызывает viewport handler.
12. `screens.go()` (`screens.js:121`) — `window.scrollTo({top:0})` для outer document.
13. Telegram `viewportChanged` (`screens.js:186`) — viewport handler.
14. `visualViewport.resize`, `visualViewport.scroll`, `window.resize` (`mobile.js:27-28`) — CSS variables + viewport handler.
15. Home drag auto-scroll (`mobile.js:10`) — `window.scrollBy`; gated Home-only, но это ещё один global document scroll source.
16. CSS `.chat { overflow-y:auto; overflow-anchor:none; scroll-behavior:auto }` (`design-match.css:986-999`) — browser anchoring отключён, все corrections принадлежат JS.
17. Flex bottom alignment `.chat-inner::before { flex:1 }` (`design-match.css:1000+`) — меняет layout/scrollHeight короткого thread без явного JS scroll.

`scrollIntoView()` для Chat не найден. Значимого Chat `setTimeout` также нет; timers относятся к voice/gesture, а не position.

### 7.2 Competing controllers и jumps

Класс scroll controller один, но control loop не единый. `handleViewportResize()` создаёт rAF на каждое viewport event и не coalesce/cancel их через `this.frame`. Во время iOS keyboard animation `resize` и `scroll` приходят серией; несколько callbacks держат разные snapshots и могут по очереди корректировать `scrollTop`, конкурируя с stream rAF и ResizeObserver bottom-scroll.

Вторая конкретная потеря позиции происходит после completion:

- pending bubble имеет key `pending-<time>`, streaming bubble — `stream-<time>`;
- `load()` запоминает первый visible bubble как anchor;
- server `history(chat_id,50)` возвращает только `role/content`, без `id/created_at`;
- rebuilt DOM получает fallback keys `role-index`;
- transient anchor больше не существует; `restore()` ничего не делает, но выставляет `followingBottom=false`;
- новый scroll container может остаться в default position, в том числе наверху.

Кроме того, при сдвиге 50-message window fallback индексы перестают идентифицировать тот же message.

### 7.3 Keyboard / viewport

`mobile.viewport()` вычисляет `--visible-height` из `visualViewport.height`, а `--keyboard-inset` — из `innerHeight - height - offsetTop`. `offsetTop` не участвует в позиционировании shell, только вычитается из inset. Во время движения visual viewport это может дать shell height, не соответствующий фактическому visible rectangle, и запустить описанные stale restore callbacks.

### 7.4 Composer / sphere

Финальный cascade (`design-match.css:1147-1180`) делает input-area левой pill, убирает правую border и помещает sphere абсолютно (`94×94`, `right:-5`, `bottom:-17`). Canvas и button в текущем финальном rule прозрачны. Значит постоянный square background не создаётся самим WebGL/canvas draw; наиболее вероятен stale/предыдущий CSS или проигравший/загруженный не в том порядке patch layer. Риск реален, потому что:

- `refinement.css` задаёт orb `radial-gradient(... ) !important`;
- более поздний `design-match.css` задаёт transparent `!important`;
- сам `design-match.css` повторно переопределяет composer/orb в конце;
- cache-buster сейчас `ui-1.7.0`; отдельный commit `6ed54a3 Fix stale Mini App asset loading` существует только на другой локальной ветке и не входит в `master`.

## 8. CSS CONFLICT MAP

### 8.1 Load order

`miniapp/index.html`:

1. `style.css?v=ui-1.7.0`
2. `mobile.css?v=ui-1.7.0`
3. `refinement.css?v=ui-1.7.0`
4. `design-match.css?v=ui-1.7.0`

`design-match.css` — фактический последний authority, но внутри самого файла накоплены хронологические override-блоки.

### 8.2 Измеренный debt

- Всего `!important`: **46** (`style.css` 2, `mobile.css` 5, `refinement.css` 5, `design-match.css` 34).
- Наиболее повторяемые selectors в четырёх files: `.focus-pill` 13, `.budget-totals .card` 13, `#dock` 10, `.keyboard-button` 10, `.segments button` 9, `#dock .orb-button` 9, `.subpage header h1` 8, `.segments` 7, `.primary` 7.
- `#shell` padding задаётся базовым `style.css`, затем несколькими блоками `design-match.css`; последний unified pass выигрывает.
- Chat composer задан в `style.css`, `refinement.css`, canonical chat block `design-match.css:986-1059` и late override `design-match.css:1147-1180`.
- `#notice` скрывается на Chat несколькими rules; current local status существует отдельно.
- Media query `max-width:370px` дополнительно уменьшает top buffer до 6 px, что усиливает safe-area failure на малом iPhone, если Telegram inset равен нулю.

### 8.3 Debt, реально связанный с текущими багами

1. **Safe area:** последний `#shell` rule отменяет прежние большие hardcoded buffers; correctness полностью зависит от runtime variable.
2. **Composer square/geometry:** несколько `!important` backgrounds и две разные модели layout; source order и cache становятся частью runtime behavior.
3. **Viewport:** height/padding распределены между CSS custom properties из двух JS modules и несколькими CSS layers.
4. **Swipe:** shell transform и `touch-action` объявлены в последнем CSS, gesture state — в другом JS; нет одного component contract.

Inline style assignments, влияющие на эту область: `--visible-height`, `--keyboard-inset`, `--app-safe-*`, textarea `height`, swipe `--edge-swipe-*`. Они выигрывают у обычных declarations и должны быть включены в будущую cascade inventory.

## 9. TELEGRAM SAFE AREA

Telegram различает system safe area и content safe area, защищающую от UI самого Telegram; это отражено в официальной [Mini Apps documentation](https://core.telegram.org/bots/webapps) и событиях [WebView API](https://core.telegram.org/api/web-events). Код берёт максимум двух inset — это разумно, если SDK state свежий.

Фактический failure chain:

1. `prepareTelegramViewport()` вызывает `ready()`, `expand()`, сразу `applyTelegramSafeArea()`.
2. Затем регистрирует `safeAreaChanged/contentSafeAreaChanged` и вызывает `requestFullscreen()`.
3. Fullscreen меняет область Telegram controls асинхронно, но `fullscreenChanged/fullscreenFailed` не слушаются; повторная синхронизация/явный fallback отсутствуют.
4. Если SDK property на первом чтении 0/stale и safe-area event не пришёл или пришёл до listener, `--app-safe-top` остаётся browser `env(safe-area-inset-top)`/0. Встроенные Telegram CSS variables `--tg-content-safe-area-inset-top` не используются как fallback.
5. После `6ed93f2` final CSS добавляет только `+8px` (`+6px` на <=370px) и уменьшает header geometry. Header оказывается в overlay region.

Следовательно, причина не «просто мало padding», а **гонка Telegram fullscreen/safe-area lifecycle плюс cascade, который не имеет устойчивого fallback**. Точный размер/частота на iPhone требует instrumented device capture событий до/после fullscreen; static code и известный screenshot symptom подтверждают path, но не дают timing конкретной версии Telegram iOS.

## 10. SWIPE NAVIGATION

### 10.1 Текущее состояние

- Один набор swipe-back handlers: global `pointerdown/move/up/cancel` в `miniapp/mobile.js:51-67`.
- Другие pointer handlers в том же файле обслуживают Home edit/drag; они не являются вторым back recognizer и в основном gated состоянием screen/edit.
- Active zone: `min(144px, max(72px, 34vw))`; при ширине 375 px это ~127.5 px, то есть более трети экрана.
- Axis acquisition: после 7 px, `abs(dx) > 1.2 * abs(dy)` и движение вправо.
- Commit: `dx >= 23vw` либо `dx > 30px` и release velocity > `0.34 px/ms`.
- Form controls, chips, ranges, draggable/edit areas блокируют gesture.
- После horizontal acquisition используется pointer capture + `preventDefault`.
- CSS shell имеет `touch-action: pan-y`; WebView может отдать gesture native scrolling и послать `pointercancel` до acquisition.
- Telegram `BackButton.onClick(back)` существует параллельно как native button API, но это не gesture handler.

Web app не может программно включить iOS/Telegram system interactive-back gesture; доступный официальный механизм — Telegram BackButton и внутренняя web navigation. Текущий custom swipe лишь имитирует back внутри DOM.

### 10.2 Recommendation: **SIMPLIFY**

Сохранить один internal recognizer, сделать Telegram BackButton authoritative, сузить zone до реального edge и убрать зависимость correctness от full-shell animation/timer. Не REPLACE на «system swipe»: такого управляемого API для Mini App нет. Не KEEP без изменений: 34% zone слишком широка и конкурирует с горизонтальным intent/vertical scroll arbitration.

## 11. GLOBAL MODEL VERIFICATION

### 11.1 Precedence

`runtime_config_snapshot()` (`bot.py:955-963`) на каждом чтении объединяет ENV/default с live `app_settings`; ADMIN override имеет фактический runtime precedence. `resolved_chat_models()` (`bot.py:1727-1738`) возвращает `fast_model` как primary и `strong_model` как emergency fallback. `chat_model_candidates()` удаляет duplicates.

`ModelRouter.resolve(chat_id,"chat")` больше не читает per-user model для выбора: legacy `user_settings.primary_model/fallback_model` сохранены в schema, но игнорируются; `set_primary()` их очищает. `available_models_for()` и `set_chat_model()` остаются legacy/dead surface, но не участвуют в request route.

### 11.2 Channel verification

| Surface | Path | Global chat model? |
|---|---|---|
| Telegram private text | `stream_answer_to_telegram → stream_agent_response → chat_model_candidates` | Да |
| Telegram group text | `ask → call_or → chat_model_candidates` | Да |
| Mini App text | `/chat/stream → stream_agent_response` | Да |
| Mini App legacy chat action | `core.ask → call_or` | Да, но отдельный runtime path |
| Telegram voice | `transcribe → ask → call_or` | Да |
| Mini App voice | `transcribe → /chat/stream` | Да |
| Tool follow-up rounds | Внутри `stream_agent_response` или `ask` | Да |
| Reminder job | Рендерит DB payload, LLM не вызывает | N/A |
| Briefing job | Deterministic `build_briefing`, LLM не вызывает | N/A |
| Vision ingestion | Отдельный global vision model contract | Отдельно по дизайну |

Персональный OpenRouter key может менять credentials/account quota, но не выбранную chat model.

### 11.3 Остаточные риски

- `global_model_mode/global_force_model` по-прежнему доступны в admin runtime UI как `Legacy`, но chat resolution их игнорирует. Это не precedence bug, однако UI создаёт ложное ожидание.
- Fallback loop идёт к `strong_model` после любого non-OK ответа, включая client/config 4xx, а не только transient/emergency status. Поэтому observable model может отличаться от admin primary при несовместимом provider parameter.
- `model_catalog` расширяет fallback candidates в legacy `call_or`; streaming path сейчас использует только primary + strong через `chat_model_candidates`. Это расхождение recovery policy, не user override.

**Вердикт:** основная цель commit `3b79f4b` по единому admin-selected chat model достигнута. Не достигнут единый execution/delivery path: streaming и legacy `ask` сосуществуют.

## 12. GLOBAL VOICE VERIFICATION

### 12.1 Mini App

`VoiceController` — singleton `window.NoemaVoiceController` (`app.js:162-175`), а не child Chat component. `renderDock()` создаёт sphere вне Chat; Chat рендерит собственную кнопку с тем же `[data-voice]`; `sync()` обновляет все mounted buttons. Переход между screens не отменяет выполняющийся `streamChat` или SpeechQueue. Требование «sphere на любом screen → STT → current global LLM → TTS по mode» для Mini App архитектурно выполнено.

Semantic preference хранится как `voice_preferences:<chat_id>` и содержит gender + speed/pitch/volume. `get_voice_preferences()` при каждом обращении разрешает `male/female` в текущие admin `tts_male_voice/tts_female_voice`; concrete voice ID не замораживается в user storage. Mini App `/speech` передаёт user ID/preferences в `make_voice`, а client выбирает Edge или browser route по текущему runtime.

### 12.2 Telegram gaps

Default private text streaming корректно вызывает `make_voice(final, chat_id=chat_id)`. Но `send_answer()` — общий финальный adapter для Telegram voice handler, groups и experimental draft — вызывает `make_voice(answer)` без chat ID. В этом случае `make_voice()` явно выбирает admin male voice defaults. User female preference сохранена, но не прочитана.

Также `make_voice()` не ветвится по `runtime["tts_provider"]`: всегда вызывается Edge TTS. В Mini App `browser` реализован client-side, в Telegram browser route невозможен в таком виде. Admin field поэтому не имеет единой cross-channel семантики.

Дополнительное расхождение: runtime default допускает `mode=auto`, Mini App UI предлагает только `text/voice/voice_and_text`. Mini App трактует `auto` как text, а `send_answer()` при voice input — как voice+text. Это legacy behavior, которое следует формализовать или удалить при consolidation.

**Вердикт:** global voice IDs и user semantic gender реализованы корректно в storage/resolution, но end-to-end parity нарушена legacy Telegram adapter. User control также шире заявленного male/female: доступны speed, pitch и volume.

## 13. FIX ORDER

### P0 — критично

1. Зафиксировать provider contract: reasoning должен быть отключён в outbound chat requests; validation должна проверять live captured schema до rollout.
2. Определить безопасную политику для уже загрязнённой canonical history до повторного использования в prompts. Не пытаться решать untagged CoT только списком русских/английских фраз.
3. Сделать Telegram final delivery lossless: все `TelegramRenderer.chunks()` должны быть доставлены; final canonical text не может заменяться 3600-char preview.
4. Определить атомарный recovery contract после edit failure (correlation/readback/idempotent final send), затем покрыть реальными failure fixtures.

### P1 — production UX

1. Ввести единый runtime state machine с точками `request/context/tool-start/tool-finish/first-token/completion`; adapters должны явно mapping-ить её.
2. Свести viewport/stream/resize scroll mutations в одну coalesced scheduler; вернуть stable message IDs в Mini App history и не использовать transient bubble как restore anchor.
3. Перестроить safe-area lifecycle вокруг fullscreen events и Telegram content-safe fallback, затем проверить на реальном iPhone/Telegram versions.
4. Передавать user identity во все TTS adapters; формализовать provider semantics и `auto` mode.

### P2 — polish / debt

1. Схлопнуть CSS layers в один component-authoritative слой без late `!important` patches.
2. Упростить swipe zone/state и согласовать с Telegram BackButton.
3. Удалить/закрыть legacy Mini App chat action и dead model settings после telemetry/deprecation window.
4. Включить asset cache correctness commit только после review относительно текущего master, не cherry-pick вслепую.

## 14. FILES THAT SHOULD BE REFACTORED

| File | Причина | Целевая граница refactor |
|---|---|---|
| `streaming_runtime.py` | Provider normalization смешан с tag filtering | Явный normalized event contract: visible content, reasoning, tool delta, finish |
| `bot.py` | В одном файле model requests, agent loop, DB, Telegram adapters, voice, jobs | Разделить provider adapter, canonical agent, Telegram delivery, TTS resolution; не делать big-bang rewrite |
| `miniapp/app.js` | Voice, chat streaming, scroll, settings и rendering в одном global module | Отдельные state machine/controller interfaces с стабильными IDs |
| `miniapp/screens.js` | Full DOM render, navigation и Telegram viewport lifecycle связаны | App shell/Telegram lifecycle отдельно от screen rendering |
| `miniapp/mobile.js` | Home drag, viewport и swipe делят global pointer/resize scope | По одному gesture/viewport owner на feature |
| `miniapp/design-match.css` | Хронологический patch stack и 34 `!important` | Компонентные authoritative blocks + tokens; удалить superseded declarations после visual baseline |
| `miniapp_api.py` | Streaming endpoint и legacy action coexist | Один chat contract и typed terminal reconciliation |
| `model_router.py` + legacy DB/UI surfaces | Correct runtime с оставшимися мёртвыми knobs | Удалить legacy chat-model state после миграции/compat review |

## 15. FILES THAT SHOULD NOT BE TOUCHED

До фикса перечисленных контрактов не следует расширять scope на следующие areas:

- `telegram_renderer.py`: chunking работает; потеря chunks происходит в `_telegram_stream_text()`. Изменение renderer замаскирует adapter bug.
- `miniapp/orb.js`: текущий canvas прозрачный; square определяется CSS/cache. Перерисовка orb не устраняет cascade.
- `knowledge_store.py`, `retrieval.py`, `ingestion.py`: они не являются источником reasoning leak/stream truncation/scroll/safe-area.
- DB schema и пользовательские knowledge/messages нельзя массово менять до утверждённой политики очистки reasoning и backup/rollback plan.
- `storage/`, `data/`, `.env`, credentials и production state не должны участвовать в UI refactor.
- `amvera.yml`/deploy history не менять до завершения regression fixes и device/provider gates.
- Архивные backup-файлы `bot.py.*backup` не использовать как source of truth и не редактировать.

## 16. PROPOSED MINIMAL FIX PLAN

Это план, не выполненные изменения.

### Шаг 1 — зафиксировать контракты тестами

1. Добавить captured Qwen SSE fixture, где internal text находится untagged в `content`.
2. Добавить assertion outbound `reasoning.enabled=false` для обоих `request_chat*`.
3. Добавить Telegram parity test на 9–12k символов и failure matrix edits.
4. Добавить Mini App terminal reconciliation test: concatenated deltas должны равняться `done.text`.

### Шаг 2 — минимальный P0 provider fix

1. В одном provider payload builder отключать reasoning для production-visible Noema chat.
2. Fail closed/alert, если выбранная модель игнорирует контракт; не эвристически публиковать unknown pre-answer text.
3. Провести one-time audit загрязнённых assistant messages; миграцию выполнять отдельным одобренным действием с backup.

### Шаг 3 — минимальный P0 Telegram fix

1. Разделить mutable preview и immutable final delivery.
2. На completion доставлять canonical `done.text` всеми `TelegramRenderer.chunks()`; не передавать через preview truncator.
3. Ввести idempotent correlation для final recovery после edit failure; duplicate-prevention не должна означать data loss.

### Шаг 4 — state/scroll/safe-area P1

1. Один state enum и один mapping для Telegram/Mini App.
2. Очистить status на `first-visible-token`, отдельно от TTS state.
3. Один coalesced scroll scheduler; viewport callbacks только публикуют measurement, не восстанавливают каждый собственный snapshot.
4. API history возвращает stable message ID; transient anchors заменяются canonical ID после `done`.
5. Safe-area sync повторяется после `fullscreenChanged`; использовать Telegram content-safe variable/property fallback; instrument logs только числовыми inset/version values.

### Шаг 5 — voice/CSS/swipe P1–P2

1. Все TTS calls получают `chat_id`/resolved preferences; provider contract формализуется per channel.
2. Удалить `auto` либо документировать одинаковую семантику во всех adapters.
3. После screenshot baseline свернуть composer/shell rules в один слой и только затем удалить старые rules.
4. Сузить custom swipe edge, сохранить один recognizer и Telegram BackButton.

### Шаг 6 — release gates

Перед deploy обязательны:

- live OpenRouter/Qwen probe без сохранения payload content;
- Telegram long-answer + injected edit-failure tests;
- Mini App/Telegram/DB visible-text parity;
- real iPhone Telegram tests: normal/fullscreen, keyboard open/close, long streaming while scrolled up/down, navigation during voice response;
- cold-cache и mixed-cache asset test;
- весь `.venv` Python 3.12 suite.

### Audit evidence / limitations

- Проверены `git status`, branches, последние 30 commits, blame/diffs для `3b79f4b`, `6ed93f2`, `0c82385` и связанных UI/stream/voice commits.
- Выполнен полный test suite через project `.venv` Python 3.12: **196 tests, OK**. Предварительные запуски через неподходящие interpreters были отброшены: system Python 3.11 не поддерживает используемый PEP 701 f-string syntax, bundled Python 3.12 не имел project dependencies.
- Выполнена live OpenRouter diagnostic текущей Qwen/Alibaba route; production user data не использовались и response не сохранялся в DB.
- Не выполнялись изменения runtime configuration, production DB, Telegram messages или deployment.
- Реальный iPhone в этой сессии не подключался; safe-area и scroll выводы основаны на полной статической event/cascade трассировке и заявленном real-device symptom. Поэтому device-specific timing помечен confidence ниже, чем provider/Telegram adapter bugs.

---

**STATUS: AUDIT_COMPLETE**
