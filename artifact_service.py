"""Safe, channel-neutral generated artifact storage for Noema."""
from __future__ import annotations

import csv
import contextlib
import io
import json
import mimetypes
import os
import re
import tempfile
import uuid
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from xml.sax.saxutils import escape


ALLOWED_EXTENSIONS = {
    ".txt", ".md", ".py", ".js", ".ts", ".html", ".css", ".json", ".csv",
    ".docx", ".xlsx", ".zip",
}
TEXT_EXTENSIONS = {".txt", ".md", ".py", ".js", ".ts", ".html", ".css", ".json", ".csv"}
MIME_TYPES = {
    ".txt": "text/plain", ".md": "text/markdown", ".py": "text/x-python",
    ".js": "text/javascript", ".ts": "text/typescript", ".html": "text/html",
    ".css": "text/css", ".json": "application/json", ".csv": "text/csv",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".zip": "application/zip",
}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _safe_filename(value: str, *, allow_pdf_fallback: bool = True) -> tuple[str, str]:
    raw = str(value or "").strip()
    if not raw or len(raw) > 180 or "\x00" in raw:
        raise ValueError("invalid_artifact_filename")
    if Path(raw).is_absolute() or raw != Path(raw).name or "/" in raw or "\\" in raw or raw in {".", ".."}:
        raise ValueError("invalid_artifact_filename")
    cleaned = re.sub(r"[^\w.() -]+", "_", raw, flags=re.UNICODE).strip(" .")
    cleaned = re.sub(r"\s+", "_", cleaned)[:140]
    suffix = Path(cleaned).suffix.lower()
    fallback_from = ""
    if suffix == ".pdf" and allow_pdf_fallback:
        cleaned = str(Path(cleaned).with_suffix(".docx"))
        suffix, fallback_from = ".docx", ".pdf"
    if suffix not in ALLOWED_EXTENSIONS:
        raise ValueError("artifact_extension_not_allowed")
    stem = Path(cleaned).stem[:100].strip(" ._") or "artifact"
    return f"{stem}{suffix}", fallback_from


def _zip_entry_name(value: str) -> str:
    raw = str(value or "").replace("\\", "/").strip()
    path = PurePosixPath(raw)
    if not raw or raw.startswith("/") or path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("invalid_zip_entry")
    if len(raw) > 180 or Path(path.name).suffix.lower() not in TEXT_EXTENSIONS:
        raise ValueError("zip_entry_not_allowed")
    clean_parts = [re.sub(r"[^\w.() -]+", "_", part, flags=re.UNICODE).strip(" .") for part in path.parts]
    if any(not part for part in clean_parts):
        raise ValueError("invalid_zip_entry")
    return "/".join(clean_parts)


def _docx_bytes(text: str) -> bytes:
    paragraphs = str(text or "").splitlines() or [""]
    body = "".join(
        f'<w:p><w:r><w:t xml:space="preserve">{escape(line)}</w:t></w:r></w:p>'
        for line in paragraphs
    )
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f'<w:body>{body}<w:sectPr/></w:body></w:document>'
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        '</Types>'
    )
    rels = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
        '</Relationships>'
    )
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", rels)
        archive.writestr("word/document.xml", document)
    return output.getvalue()


def _xlsx_bytes(content: str = "", rows=None) -> bytes:
    if rows is None:
        dialect = csv.excel_tab if "\t" in str(content) and "," not in str(content) else csv.excel
        rows = list(csv.reader(io.StringIO(str(content or "")), dialect=dialect))
    if not isinstance(rows, list) or len(rows) > 10000:
        raise ValueError("invalid_xlsx_rows")
    xml_rows = []
    for row_index, row in enumerate(rows or [[""]], 1):
        if not isinstance(row, list) or len(row) > 256:
            raise ValueError("invalid_xlsx_rows")
        cells = []
        for col_index, value in enumerate(row, 1):
            number = col_index
            letters = ""
            while number:
                number, rem = divmod(number - 1, 26)
                letters = chr(65 + rem) + letters
            cells.append(f'<c r="{letters}{row_index}" t="inlineStr"><is><t>{escape(str(value))}</t></is></c>')
        xml_rows.append(f'<row r="{row_index}">{"".join(cells)}</row>')
    sheet = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<sheetData>{"".join(xml_rows)}</sheetData></worksheet>'
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        '</Types>'
    )
    root_rels = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
        '</Relationships>'
    )
    workbook = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets></workbook>'
    )
    workbook_rels = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
        '</Relationships>'
    )
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", root_rels)
        archive.writestr("xl/workbook.xml", workbook)
        archive.writestr("xl/_rels/workbook.xml.rels", workbook_rels)
        archive.writestr("xl/worksheets/sheet1.xml", sheet)
    return output.getvalue()


class ArtifactService:
    def __init__(self, connect, root, *, max_bytes=15 * 1024 * 1024, ttl_hours=72, close_connections=True):
        self.connect = connect
        self.root = Path(root).resolve()
        self.max_bytes = int(max_bytes)
        self.ttl = timedelta(hours=max(1, int(ttl_hours)))
        self.close_connections = bool(close_connections)
        self.root.mkdir(parents=True, exist_ok=True)
        self.init_schema()

    @contextlib.contextmanager
    def _connection(self):
        connection = self.connect()
        try:
            with connection:
                yield connection
        finally:
            if self.close_connections:
                connection.close()

    def init_schema(self):
        with self._connection() as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS artifacts(
                    artifact_id TEXT PRIMARY KEY, owner_chat_id INTEGER NOT NULL,
                    filename TEXT NOT NULL, mime_type TEXT NOT NULL, size_bytes INTEGER NOT NULL,
                    local_path TEXT NOT NULL, created_at TEXT NOT NULL, expires_at TEXT NOT NULL,
                    source_message_id INTEGER, fallback_from TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS idx_artifacts_owner_created
                    ON artifacts(owner_chat_id, created_at);
                CREATE TABLE IF NOT EXISTS message_artifacts(
                    message_id INTEGER NOT NULL, artifact_id TEXT NOT NULL,
                    PRIMARY KEY(message_id, artifact_id)
                );
            """)

    def _encode(self, suffix: str, content, rows, files) -> bytes:
        if suffix in TEXT_EXTENSIONS:
            if suffix == ".json":
                if not isinstance(content, str):
                    content = json.dumps(content, ensure_ascii=False, indent=2)
                else:
                    content = json.dumps(json.loads(content), ensure_ascii=False, indent=2)
            elif suffix == ".csv" and rows is not None:
                output = io.StringIO(newline="")
                csv.writer(output).writerows(rows)
                content = output.getvalue()
            return str(content or "").encode("utf-8")
        if suffix == ".docx":
            return _docx_bytes(str(content or ""))
        if suffix == ".xlsx":
            return _xlsx_bytes(str(content or ""), rows)
        if suffix == ".zip":
            if not isinstance(files, list) or not files or len(files) > 100:
                raise ValueError("invalid_zip_files")
            output = io.BytesIO()
            seen = set()
            with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
                for item in files:
                    if not isinstance(item, dict):
                        raise ValueError("invalid_zip_files")
                    name = _zip_entry_name(item.get("name"))
                    if name.casefold() in seen:
                        raise ValueError("duplicate_zip_entry")
                    seen.add(name.casefold())
                    archive.writestr(name, str(item.get("content") or "").encode("utf-8"))
            return output.getvalue()
        raise ValueError("artifact_extension_not_allowed")

    def create(self, owner_chat_id, filename, content="", *, rows=None, files=None):
        self.cleanup()
        safe_name, fallback_from = _safe_filename(filename)
        suffix = Path(safe_name).suffix.lower()
        body = self._encode(suffix, content, rows, files)
        if len(body) > self.max_bytes:
            raise ValueError("artifact_too_large")
        artifact_id = uuid.uuid4().hex
        folder = (self.root / artifact_id[:2]).resolve()
        if self.root not in folder.parents:
            raise ValueError("artifact_storage_error")
        folder.mkdir(parents=True, exist_ok=True)
        target = (folder / f"{artifact_id}{suffix}").resolve()
        if folder not in target.parents:
            raise ValueError("artifact_storage_error")
        fd, temporary = tempfile.mkstemp(prefix="artifact-", suffix=".tmp", dir=folder)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(body)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
        except BaseException:
            Path(temporary).unlink(missing_ok=True)
            raise
        created = utc_now()
        expires = created + self.ttl
        mime = MIME_TYPES.get(suffix) or mimetypes.guess_type(safe_name)[0] or "application/octet-stream"
        with self._connection() as connection:
            connection.execute(
                "INSERT INTO artifacts(artifact_id,owner_chat_id,filename,mime_type,size_bytes,local_path,created_at,expires_at,fallback_from) VALUES(?,?,?,?,?,?,?,?,?)",
                (artifact_id, int(owner_chat_id), safe_name, mime, len(body), str(target), created.isoformat(), expires.isoformat(), fallback_from),
            )
        return self.metadata(artifact_id, int(owner_chat_id), is_admin=False)

    def metadata(self, artifact_id, requester_chat_id, *, is_admin=False, include_path=False):
        with self._connection() as connection:
            row = connection.execute("SELECT * FROM artifacts WHERE artifact_id=?", (str(artifact_id),)).fetchone()
        if not row:
            raise FileNotFoundError("artifact_not_found")
        item = dict(row)
        if int(item["owner_chat_id"]) != int(requester_chat_id) and not is_admin:
            raise PermissionError("artifact_forbidden")
        if datetime.fromisoformat(item["expires_at"]) <= utc_now() or not Path(item["local_path"]).is_file():
            self._delete(item)
            raise FileNotFoundError("artifact_expired")
        public = {key: item[key] for key in ("artifact_id", "filename", "mime_type", "size_bytes", "created_at", "expires_at", "fallback_from")}
        if include_path:
            public["local_path"] = item["local_path"]
        return public

    def link_message(self, message_id, artifact_ids):
        with self._connection() as connection:
            for artifact_id in dict.fromkeys(str(value) for value in artifact_ids if value):
                connection.execute("INSERT OR IGNORE INTO message_artifacts(message_id,artifact_id) VALUES(?,?)", (int(message_id), artifact_id))
                connection.execute("UPDATE artifacts SET source_message_id=? WHERE artifact_id=?", (int(message_id), artifact_id))

    def for_messages(self, owner_chat_id, message_ids):
        ids = [int(value) for value in message_ids]
        if not ids:
            return {}
        placeholders = ",".join("?" for _ in ids)
        with self._connection() as connection:
            rows = connection.execute(
                f"SELECT ma.message_id,a.artifact_id,a.filename,a.mime_type,a.size_bytes,a.created_at,a.expires_at,a.fallback_from "
                f"FROM message_artifacts ma JOIN artifacts a ON a.artifact_id=ma.artifact_id "
                f"WHERE a.owner_chat_id=? AND ma.message_id IN ({placeholders}) AND a.expires_at>?",
                (int(owner_chat_id), *ids, utc_now().isoformat()),
            ).fetchall()
        output = {}
        for row in rows:
            item = dict(row)
            output.setdefault(item.pop("message_id"), []).append(item)
        return output

    def _delete(self, item):
        path = Path(item["local_path"])
        if path.is_file() and self.root in path.resolve().parents:
            path.unlink(missing_ok=True)
        with self._connection() as connection:
            connection.execute("DELETE FROM message_artifacts WHERE artifact_id=?", (item["artifact_id"],))
            connection.execute("DELETE FROM artifacts WHERE artifact_id=?", (item["artifact_id"],))

    def cleanup(self):
        with self._connection() as connection:
            rows = connection.execute("SELECT * FROM artifacts WHERE expires_at<=?", (utc_now().isoformat(),)).fetchall()
        for row in rows:
            self._delete(dict(row))
        return len(rows)
