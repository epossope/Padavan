"""Shared test helpers: sys.path bootstrap, temp DBs, fixtures."""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from knowledge_store import KnowledgeStore  # noqa: E402


def make_tmp_db():
    fd, path = tempfile.mkstemp(suffix=".sqlite3")
    os.close(fd)
    return Path(path)


def make_store(db_path):
    """KnowledgeStore on a fresh tmp db with a real `files` table."""
    store = KnowledgeStore(db_path)
    with store._connect() as c:
        c.execute(
            """CREATE TABLE IF NOT EXISTS files (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER, telegram_file_id TEXT, original_name TEXT,
                mime_type TEXT, local_path TEXT, kind TEXT, summary TEXT,
                extracted_text TEXT, created_at TEXT)"""
        )
    return store


def make_image(tmp: Path, name: str = "img.png") -> Path:
    p = Path(tmp) / name
    p.write_bytes(b"\x89PNG\r\n" + b"0123456789abcdef" * 8)
    return p