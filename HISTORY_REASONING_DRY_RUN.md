# NOEMA — HISTORY REASONING DRY-RUN

**Дата:** 2026-09-14
**Режим:** read-only candidate inventory; destructive migration не выполнялась

## Backup

- Source: local `noema_test.sqlite3`
- Backup: `history-backups/noema_test.pre-p0-20260914.sqlite3`
- Size: 348160 bytes
- SHA-256: `A414192E83414F14A75AFACA182EDCF7BD9A09DFB1089C00608806129771E100`
- Backup исключён из git правилом `*.sqlite3`; пользовательские данные в commit не попадут.

## Dry-run result

- Assistant messages scanned: **70**
- Potential candidates: **1**
- Message ID: `322`
- Pseudonymous chat reference: `ff55c86c48ee`
- Created at: `2026-09-10T23:35:49.164542+00:00`
- Length: 912 characters
- Candidate signal: known reasoning-style phrase within the first 700 characters
- Message content was not emitted into terminal output or this report.

Это **кандидат, а не подтверждённое загрязнение**: phrase matching может дать false positive в нормальном пользовательском ответе. Перед любым удалением или переписыванием требуется отдельное подтверждение и ручная проверка с контролируемым доступом.

## Method

`tools/history_reasoning_dry_run.py` открывает SQLite через `mode=ro`, читает только assistant messages и выводит metadata/псевдонимный chat reference. Runtime filtering этот phrase list не использует. Tool не выполняет `UPDATE`, `DELETE`, schema migration или content export.

## Status

`HISTORY_DRY_RUN_COMPLETE_NO_MUTATION`
