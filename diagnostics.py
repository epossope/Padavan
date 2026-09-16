"""Small, privacy-safe, current-runtime diagnostics journal."""
import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path


class DiagnosticsJournal:
    """Append structured operational events without retaining user content."""

    _ALLOWED = {
        "stage", "build", "platform", "duration_ms", "elapsed_ms", "auth_ms",
        "settings_ms", "tasks_ms", "reminders_ms", "notes_ms", "budget_ms",
        "people_ms", "files_ms", "knowledge_ms", "serialize_ms", "total_ms",
        "error_class", "error_code", "source", "status", "action", "attempt",
        "event_loop_lag_ms", "delivered", "operation", "result",
        "media_kind", "cache", "size_bucket", "duration_bucket",
    }

    def __init__(self, directory: Path, max_bytes=5 * 1024 * 1024, backups=2):
        self.directory = Path(directory)
        self.path = self.directory / "noema_diagnostics.jsonl"
        self.max_bytes = int(max_bytes)
        self.backups = int(backups)
        self._lock = threading.Lock()

    def _rotate_locked(self):
        if not self.path.exists() or self.path.stat().st_size < self.max_bytes:
            return
        oldest = self.path.with_name(self.path.name + f".{self.backups}")
        oldest.unlink(missing_ok=True)
        for index in range(self.backups - 1, 0, -1):
            source = self.path.with_name(self.path.name + f".{index}")
            if source.exists():
                source.replace(self.path.with_name(self.path.name + f".{index + 1}"))
        self.path.replace(self.path.with_name(self.path.name + ".1"))

    def record(self, event: str, **fields):
        """Best effort only: diagnostic failure must never affect product flows."""
        if not isinstance(event, str) or not event.replace("_", "").isalnum():
            return
        safe = {"ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"), "event": event}
        for name, value in fields.items():
            if name not in self._ALLOWED or isinstance(value, (dict, list, tuple, bytes)):
                continue
            if isinstance(value, bool):
                safe[name] = value
            elif isinstance(value, (int, float)) and not isinstance(value, bool):
                safe[name] = round(value, 3)
            elif isinstance(value, str):
                # Fields are fixed operational labels, never user-provided content.
                safe[name] = "".join(char for char in value[:80] if char.isascii() and (char.isalnum() or char in "._:-"))
        try:
            encoded = json.dumps(safe, ensure_ascii=False, separators=(",", ":")) + "\n"
            with self._lock:
                self.directory.mkdir(parents=True, exist_ok=True)
                self._rotate_locked()
                with self.path.open("a", encoding="utf-8", newline="\n") as output:
                    output.write(encoded)
        except OSError:
            pass

    def current_file(self) -> Path | None:
        try:
            return self.path if self.path.is_file() else None
        except OSError:
            return None
