# Noema Mini App UI/UX and responsiveness hardening

Date: 2026-09-11

## Bottleneck audit

- OpenRouter, Mistral, weather, exchange-rate and vision `requests.*` calls remain in the established synchronous provider layer, but every path reachable from an async handler runs them through `asyncio.to_thread` or a dedicated streaming worker. An AST regression test rejects future direct `requests.*` or `time.sleep` calls inside async functions.
- The reproduced event-loop pressure came from synchronous SQLite-backed live-emoji rendering immediately before Telegram sends, serialized voice/image/callback handlers, and a reminder job running every five seconds.
- Telegram sends could additionally wait on the default HTTP pool/timeouts. A transient timeout then escaped `replace_active_ui` and failed the handler.

## Changes

- Live Telegram text/markup preparation and reminder database work now run outside the event loop.
- Callback acknowledgement remains the first awaited callback action. Callback, voice, text and image work no longer serialize unrelated incoming updates.
- Telegram uses an explicit 32-connection pool and bounded connect/read/write/pool timeouts.
- `send_message` has at most one short jittered retry by default. `replace_active_ui` absorbs `TimedOut`, `NetworkError` and `Forbidden` safely.
- Reminder polling is 20 seconds by default (clamped to 15–30 seconds), sends at bounded concurrency, and does not do database/file preparation in the event loop.
- Runtime telemetry records `callback_ack_ms`, `telegram_send_ms`, `event_loop_lag_ms`, `ui_action_ms`, `stt_ms`, `llm_ttft_ms`, `tts_start_ms` and `total_response_start_ms`. Normal samples stay in memory/debug logging; only slow/error samples produce warnings.

## UI proof

- Viewport declares `maximum-scale=1`, `user-scalable=no` and `viewport-fit=cover`; phone form controls render at 16 px or more.
- The bottom dock is a fixed translucent/blurred overlay. Focus, sphere and keyboard controls share one measured horizontal center line.
- The chat sphere is foreground content, centered before the composer, and persists across renders. Sleeping/listening/thinking/speaking have restrained but distinct membrane motion.
- Cards use quieter contours, tighter padding, shorter secondary copy and consistent metadata hierarchy.
- `display_text` remains intact while TTS receives separately sanitized `speech_text`. Browser tests reject Markdown emphasis, bullets, table pipes, brackets/braces, raw URLs and tool/debug metadata.

Before/after screenshots are stored in `ui-proof/`. The visual regression covers six phone viewports and eight screens.

## Verification boundary

The local concurrency regression runs a voice worker, eight delayed reminder sends, callback acknowledgement and a 10 ms heartbeat together; it fails if maximum heartbeat delay reaches 50 ms. A real Telegram + Amvera + microphone concurrency run still requires deployment access and must not be inferred from this local proof.
