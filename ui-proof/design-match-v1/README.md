# Noema Design Match V1 — visual baseline

Each comparison uses the supplied reference, the Mini App immediately before this stage, and the corrected 390 × 844 viewport.

- `comparison-*.png` — side-by-side review artifacts.
- `reference-*.png` — immutable copies of the supplied visual references.
- `current-before-*.png` — baseline captured from commit `70cd1dc` or the existing baseline previews.
- `current-after-*.png` — corrected screenshots with deterministic, local-only fixture content.

Chat intentionally has no legacy visual reference: its accepted production architecture is the source of truth (history + compact composer + sphere on the right).

The visual fixture contains synthetic neutral UI content only. It does not connect to Telegram, persist data, or call provider APIs.
