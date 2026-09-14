"""Read-only inventory of assistant messages that may contain reasoning.

This tool never prints message text or mutates the database. Its phrase signals
are audit candidates only; they are deliberately not used by runtime filtering.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from pathlib import Path
from urllib.parse import quote


TAG_SIGNALS = ("<think", "</think", "<analysis", "</analysis", "<reasoning", "</reasoning")
PREFIX_SIGNALS = (
    "the user is asking", "the user asks", "i need to", "i should", "i must",
    "let me ", "we need to", "the question is", "пользователь спрашивает",
    "мне нужно", "я должен", "нужно ответить",
)


def audit(database_path: Path) -> dict:
    resolved = database_path.resolve(strict=True)
    connection = sqlite3.connect(f"file:{quote(resolved.as_posix())}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            "SELECT id,chat_id,content,created_at FROM messages WHERE role='assistant' ORDER BY id"
        ).fetchall()
    finally:
        connection.close()

    candidates = []
    for row in rows:
        content = str(row["content"] or "")
        lowered = content.casefold()
        prefix = lowered[:700]
        signals = [signal for signal in TAG_SIGNALS if signal in lowered]
        signals.extend(signal for signal in PREFIX_SIGNALS if signal in prefix)
        if not signals:
            continue
        chat_ref = hashlib.blake2s(str(row["chat_id"]).encode("utf-8"), digest_size=6).hexdigest()
        candidates.append({
            "message_id": row["id"],
            "chat_ref": chat_ref,
            "created_at": row["created_at"],
            "characters": len(content),
            "signals": sorted(set(signals)),
        })
    return {
        "mode": "read-only-dry-run",
        "assistant_messages_scanned": len(rows),
        "candidate_count": len(candidates),
        "candidates": candidates,
        "content_emitted": False,
        "database_mutated": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("database", type=Path)
    args = parser.parse_args()
    print(json.dumps(audit(args.database), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
