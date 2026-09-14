# NOEMA — P0 PRODUCTION FIX REPORT

**Дата:** 2026-09-14
**Scope:** provider reasoning contract + Telegram final delivery only
**Result:** `P0_PRODUCTION_FIX: PASS`

## ROOT CAUSE CONFIRMED

Live audit root causes confirmed:

1. Qwen/Alibaba without an explicit disabled-reasoning contract can return untagged internal reasoning in ordinary `delta.content`. Field/tag-only sanitization cannot reliably distinguish that prose from the final answer.
2. `_telegram_stream_text()` was reused for final delivery and intentionally replaced multi-chunk output with a 3600-character preview.
3. A failed preview edit set `editing_available=false`; completion then skipped the final edit and left the old prefix as the only Telegram message.

No Chat UI, CSS, orb, safe-area, swipe, voice UI, knowledge/retrieval or Amvera files were changed.

## QWEN CONTRACT FIX

- Added one `build_chat_payload()` used by both `request_chat()` and `request_chat_stream()`.
- Every production-visible chat request now includes exactly `"reasoning": {"enabled": false}`.
- `reasoning.exclude` is not used.
- Existing provider routing keeps `require_parameters=true`, preventing OpenRouter from selecting routes that do not advertise support for the requested parameter.
- Machine-readable contract violations (`reasoning`, `reasoning_details`, `analysis`, `thinking`, or reasoning tags) reject the provider attempt and advance to the configured fallback. No prose/CoT regex is used in runtime delivery.
- Untagged prose is not guessed after generation; source prevention is verified by the captured transport contract test and live provider probe.

## TELEGRAM FINAL DELIVERY

- `_telegram_stream_text()` is now preview-only and no longer contains `visible[:3600] + "…"`.
- `_telegram_final_chunks()` renders the canonical `done.text` through `TelegramRenderer.chunks()` and preserves every chunk in order.
- Completion sends the immutable canonical final sequence first, then removes the superseded mutable preview on a best-effort basis; cleanup failure cannot erase the final answer.
- A 10,000-character regression case verifies delivery across at least three Telegram messages with no lost tail.

## EDIT FAILURE RECOVERY

- Final delivery no longer depends on `editing_available`.
- `TimedOut`, exhausted `RetryAfter`, and `BadRequest` preview-edit failures all proceed to immutable canonical delivery.
- Per-canonical-message/per-chunk correlation keys include chat, canonical ID, chunk index and content digest.
- The process-local guard prevents the same known final chunk from being sent twice if final delivery is invoked again in the same process.
- Telegram Bot API does not provide a send-message idempotency key for an ambiguous network timeout; the implemented guard covers application-level repeated invocation, while existing bounded transport retry behavior remains unchanged.

## HISTORY DRY-RUN STATUS

- Local SQLite backup created before analysis: `history-backups/noema_test.pre-p0-20260914.sqlite3`.
- SHA-256: `A414192E83414F14A75AFACA182EDCF7BD9A09DFB1089C00608806129771E100`.
- Backup is excluded from git by `*.sqlite3`.
- Read-only scan: 70 assistant messages, one potential candidate (`message_id=322`, pseudonymous chat reference only).
- Candidate is not treated as confirmed contamination; manual review and separate approval are required.
- No automatic migration, `UPDATE`, `DELETE`, or content export was performed.
- Detailed metadata: `HISTORY_REASONING_DRY_RUN.md`.

## PARITY TESTS

| Requirement | Coverage | Result |
|---|---|---|
| A. Captured Qwen schema / untagged reasoning prevented | Fixture-backed fake provider returns legacy untagged stream unless outbound contract disables reasoning; only clean final crosses delta/DB boundary | PASS |
| B. 9–12k Telegram answer | 10,000 characters split and delivered in full | PASS |
| C. Initial preview + edit `TimedOut` | Preview removed; complete immutable final chunks sent | PASS |
| D. `RetryAfter` edit failure | Complete immutable final chunks sent | PASS |
| E. `BadRequest` edit failure | Complete immutable final chunks sent | PASS |
| F. Mini App deltas equal `done.text` | NDJSON endpoint assertion added | PASS |
| G. Telegram final equals `done.text` modulo rendering/chunks | Delivered call sequence equals `_telegram_final_chunks(done.text)` | PASS |
| H. DB assistant equals `done.text` | Captured provider end-to-end test queries canonical DB row | PASS |

Additional coverage verifies that a machine-readable reasoning violation produces no visible delta from that provider attempt and falls back to a compliant response.

## LIVE QWEN PROBE

Read-only probe performed after tests against the active route:

- Model: `qwen/qwen3.5-flash-02-23`
- Provider: `Alibaba`
- HTTP: 200
- Outbound reasoning contract: `enabled=false`
- SSE events: 5
- Delta fields: `content`, `role`
- Explicit reasoning fields: absent
- Reasoning tags: absent
- Usage reasoning tokens: 0
- Response matched the exact controlled contract token; response content was not written to diagnostic logs, DB or this report.

`LIVE_QWEN_PROBE: PASS`

## TESTS

- Python syntax compilation: PASS
- Targeted P0/provider/Telegram/Mini App suite: 59 tests, PASS
- Full project suite: **203 tests, PASS**
- `git diff --check`: PASS

## COMMIT

- Commit subject: `Fix P0 reasoning contract and Telegram final delivery`
- The exact commit SHA is returned after commit creation. A commit cannot embed its own final SHA without changing that SHA.

---

`P0_PRODUCTION_FIX: PASS`
