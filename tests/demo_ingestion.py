"""Demo runner: executes TEST 1-10 of the Universal Ingestion Pipeline and prints
real data flowing through ingestion -> storage -> search -> actions.

Usage:  PYTHONPATH=. .venv/Scripts/python.exe tests/demo_ingestion.py
"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from ingestion import (ActionBuilder, Attachment, IngestionInput,  # noqa: E402
                       IngestionPipeline)
from tests import fakes  # noqa: E402
from tests import helpers as h  # noqa: E402

DOG = {
    "content_type": "photo", "title": "Ричи позирует", "summary": "Собака Ричи на фото",
    "visible_text": "", "urls": [],
    "entities": [{"type": "pet", "name": "Ричи"}],
    "objects": ["собака"], "tags": ["собака"], "category": "pet",
    "project_hint": None, "confidence": 0.92,
}
RECEIPT = {
    "content_type": "photo", "title": "Чек", "summary": "Чек из кафе",
    "visible_text": "Кофе 250 ₽\nИтого: 250 руб.", "urls": [],
    "entities": [], "objects": ["чек"], "tags": ["чек"], "category": "receipt",
    "project_hint": None, "confidence": 0.9,
}


def main():
    tmp = Path(tempfile.mkdtemp(prefix="noema_demo_"))
    store = h.make_store(tmp / "db.sqlite3")
    seq = [0]

    def pipe(payload, enricher=None, runner=None):
        return IngestionPipeline(
            store=store,
            vision_extractor=fakes.FakeVision(payload),
            url_enricher=enricher,
            file_saver=fakes.SqlFileSaver(store),
            action_builder=ActionBuilder(action_runner=runner or fakes.RecordingActionRunner()),
            storage_dir=tmp / "storage",
        )

    def inp(text="", attachments=(), msg_id=None):
        seq[0] += 1
        atts = []
        for i, a in enumerate(attachments):
            p = h.make_image(tmp, a)
            atts.append(Attachment(file_id=f"TLG_{seq[0]}_{i}", local_path=str(p),
                                   mime_type="image/jpeg", kind="image", original_name=a))
        return IngestionInput(chat_id=7, message_id=msg_id or seq[0], user_text=text,
                              attachments=atts)

    run = pipe(DOG)
    r1 = run.ingest(inp(text="Это моя собака Ричи", attachments=["richi.jpg"]))
    print("\n==== TEST 1: фото собаки + «Это моя собака Ричи» ====")
    print(" status:", r1.status, "| entity:", r1.item["entities"],
          "| actions:", [a["tool"] for a in r1.actions])
    print(" reply:", r1.reply.replace("\n", " | "))

    print("\n==== TEST 2: «покажи фото Ричи» -> original file ====")
    hits = store.search_by_entity("Ричи", chat_id=7)
    files = store.item_files(hits[0]["id"])
    p2 = Path(files[0]["local_path"])
    print(" found items:", len(hits), "| saved file:", p2.name, "| bytes ok:", len(p2.read_bytes()) > 0)

    payload3 = {
        "content_type": "screenshot", "title": "Noema UI kit",
        "summary": "Скриншот дизайн-ресурса для проекта Noema",
        "visible_text": "https://design.example.com/noema-ui Noema UI kit v3",
        "urls": ["https://design.example.com/noema-ui"], "entities": [],
        "objects": ["экран"], "tags": ["дизайн", "ui"], "category": "web_resource",
        "project_hint": None, "confidence": 0.9,
    }
    en3 = fakes.FakeEnricher()
    r3 = pipe(payload3, enricher=en3).ingest(
        inp(text="сохрани для проекта Noema как ресурс по дизайну", attachments=["shot.png"]))
    print("\n==== TEST 3: скриншот + URL + проект ====")
    print(" project_id:", r3.project_id, "| urls:", r3.urls, "| tags:", r3.item["tags"],
          "| files:", len(store.item_files(r3.item["id"])))
    print(" enrichment(calls):", en3.calls)

    payload4 = {
        "content_type": "screenshot", "title": "Список покупок", "summary": "",
        "visible_text": "Купить молоко, хлеб и сыр. Встреча в 18:00.",
        "urls": [], "entities": [], "objects": [], "tags": [],
        "category": "", "project_hint": None, "confidence": 0.8,
    }
    r4 = pipe(payload4).ingest(inp(text="сохрани", attachments=["list.png"]))
    print("\n==== TEST 4: скриншот с текстом ====")
    print(" visible_text:", r4.item["visible_text"])

    r5 = pipe(RECEIPT).ingest(inp(msg_id=81, text="добавь расход", attachments=["receipt.jpg"]))
    print("\n==== TEST 5: чек + «добавь расход» ====")
    act = next(a for a in r5.actions if a["tool"] == "add_expense")
    print(" extraction:", r5.item["visible_text"].replace("\n", " | "))
    print(" action add_expense:", act["args"], "=> ok:", act["result"]["ok"])

    r6 = pipe(RECEIPT).ingest(inp(msg_id=82, text="что тут написано?", attachments=["receipt.jpg"]))
    print("\n==== TEST 6: тот же чек + вопрос ====")
    print(" actions:", r6.actions,
          "| visible_text сохранён:", "Кофе 250 ₽" in r6.item["visible_text"])

    payload7 = {
        "content_type": "screenshot", "title": "", "summary": "",
        "visible_text": "Просто текст на скриншоте.", "urls": [], "entities": [],
        "objects": [], "tags": [], "category": "", "project_hint": None, "confidence": 0.8,
    }
    en7 = fakes.FakeEnricher()
    r7 = pipe(payload7, enricher=en7).ingest(inp(text="посмотри", attachments=["s.png"]))
    print("\n==== TEST 7: скриншот без URL ====")
    print(" urls:", r7.urls, "| enricher calls:", en7.calls)

    payload8 = {
        "content_type": "screenshot", "title": "Страница", "summary": "",
        "visible_text": "Скриншот https://example.com/page",
        "urls": ["https://example.com/page"], "entities": [], "objects": [],
        "tags": [], "category": "", "project_hint": None, "confidence": 0.8,
    }
    en8 = fakes.FakeEnricher(error="403 Forbidden")
    r8 = pipe(payload8, enricher=en8).ingest(inp(text="сохрани", attachments=["page.png"]))
    print("\n==== TEST 8: UrlEnricher failed ====")
    print(" ingestion ok:", r8.ok, "| ingestion:", r8.ingestion_status,
          "| enrichment:", r8.enrichment_status,
          "| item ingestion:", r8.item["status"], "| item enrichment:", r8.item["enrichment_status"],
          "| файл сохранён:", len(store.item_files(r8.item["id"])) == 1)

    payload9 = {
        "content_type": "photo", "title": "", "summary": "", "visible_text": "текст",
        "urls": [], "entities": [], "objects": [], "tags": [], "category": "",
        "project_hint": None, "confidence": 0.5,
    }
    p9 = pipe(payload9)
    i9 = inp(msg_id=90, text="сохрани", attachments=["d.png"])
    ra = p9.ingest(i9)
    rb = p9.ingest(i9)
    print("\n==== TEST 9: duplicate Telegram update ====")
    print(" первый:", ra.status, "| повторный:", rb.status, "| duplicate:", rb.duplicate,
          "| items:", store.count_items(chat_id=7))

    print("\n==== TEST 10: итог E2E ====")
    with store._connect() as c:
        links = c.execute("SELECT COUNT(*) FROM knowledge_files").fetchone()[0]
    print(" items в хранилище:", store.count_items(), "| file-связей:", links)
    print("Демо-скрипт завершён OK")


if __name__ == "__main__":
    main()