# Noema Mini App — design recovery

Baseline was captured before product-source edits. The source-of-truth images are stored in `design-recovery-baseline/reference/`; the committed Mini App renders from `4388a2b^` are in `design-recovery-baseline/before-4388a2b/`.

## `4388a2b` change separation

| Category | Files / changes | Recovery decision |
| --- | --- | --- |
| Backend, performance, runtime | `.env.example`, `bot.py`, `miniapp_api.py`, `miniapp/app.js`, `tests/test_async_responsiveness.py`, related API tests | Preserved unchanged |
| Viewport and anti-zoom | locked viewport in `miniapp/index.html`; text-size, overflow and 16 px mobile form safeguards in `miniapp/refinement.css`; visual regression assertions | Preserved |
| Speech sanitizer | `miniapp/voice-conversation.js`, `tests/vosk_browser_smoke.cjs` | Preserved unchanged |
| Visual CSS, layout and design | compact dock frame, reduced dock height, flattened inner cards, reduced chat bubbles, dimmed membrane treatment in `miniapp/refinement.css` and `miniapp/orb.js`; chat membrane placement in `miniapp/screens.js` | Visual compression removed; foreground chat placement and blank technical status retained |

## Corrected visual contract

- Original card dimensions, glass gradients, hairline borders, depth and typographic hierarchy remain the base system.
- The dock is still a fixed overlay, but uses the original three-part composition with a transparent fade and no enclosing frame.
- Focus Day retains adaptive width and gains vertical presence without colliding with the centered membrane.
- Focus Day, membrane and keyboard controls share one vertical centerline; the full dock sits slightly lower.
- The membrane keeps the low-cost idle cadence introduced in `4388a2b`, while its halo, rim and refractive folds regain the bright reference presence.
- Chat keeps the membrane as central foreground content. The technical state label stays empty; existing full-size glass bubbles and composer proportions are restored.
- The viewport remains locked at scale 1.

## Verification

- Python suite: 138 tests passed.
- Browser suite: 6 mobile viewports × 8 screens, locked viewport, overlay dock, centered Chat membrane, stable membrane canvas and mouse/touch card sorting passed.
- Vosk/WASM wake command and speech sanitizer smoke test passed.

Open `design-recovery-baseline/comparison.html` or the six generated PNG files in `design-recovery-baseline/comparisons/` for the requested `reference | before 4388a2b | corrected` review.
