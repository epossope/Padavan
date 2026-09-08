"""LIVE Vision E2E for the Universal Ingestion Pipeline.

Runs the REAL vision provider (OpenRouter, key from .env) with NO mock extractor.

  Scenario A: screenshot with a visible URL + "Сохрани этот сайт как полезный
              ресурс для дизайна" -> vision extracts visible_text/URL/tags,
              knowledge item + original file saved, enrichment of the URL runs.
  Scenario B: real dog photo + "Это моя собака Ричи" -> vision description,
              entity pet/Ричи expected, NO person_upsert, photo retrievable via
              search.

Usage:  PYTHONPATH=. .venv/Scripts/python.exe tests/live_vision_e2e.py
Exit 0 when both scenarios pass, 3 when the environment cannot support the test
(no key / no network), non-zero otherwise.
"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import bot  # noqa: E402
from ingestion import (ActionBuilder, Attachment, IngestionInput,  # noqa: E402
                       IngestionPipeline, VisionExtractor)
from knowledge_store import KnowledgeStore  # noqa: E402
from url_enricher import HttpUrlEnricher  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

SCREENSHOT = ROOT / "storage" / "temp" / "design_screenshot.png"
DOG_IMG = ROOT / "storage" / "temp" / "dog_2.jpg"
RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond)))
    print(("PASS" if cond else "FAIL"), name, ("| " + str(detail) if detail else ""))


def main():
    if not bot.OR_KEY or "PASTE" in bot.OR_KEY:
        print("NO_OPENROUTER_KEY")
        return 3
    tmp = Path(tempfile.mkdtemp(prefix="noema_live_"))
    bot.DB = tmp / "live.sqlite3"
    bot.init_db()
    store = KnowledgeStore(bot.DB)

    def pipeline():
        return IngestionPipeline(
            store=store,
            vision_extractor=VisionExtractor(),       # REAL vision (no mock)
            url_enricher=HttpUrlEnricher(),           # REAL enricher (no allowlist)
            file_saver=bot._bot_save_file,            # REAL bot file saver
            action_builder=ActionBuilder(action_runner=lambda cid, n, a: bot.execute_tool(cid, n, a)),
            storage_dir=tmp / "storage",
        )

    def inp(image, caption, mid):
        return IngestionInput(
            chat_id=555, message_id=mid, user_text=caption,
            attachments=[Attachment(file_id=f"LIVE_{mid}", local_path=str(image),
                                    mime_type=("image/png" if image.suffix == ".png" else "image/jpeg"),
                                    kind="image", original_name=image.name)],
            timestamp=bot.datetime.now(bot.TZ),
        )

    # ------------------------- Scenario A ----------------------------------
    print("\n=== SCENARIO A: screenshot + visible URL + design resource ===")
    res_a = pipeline().ingest(inp(SCREENSHOT, "Сохрани этот сайт как полезный ресурс для дизайна", 7001))
    it_a = res_a.item or {}
    print(" vision used:", it_a.get("metadata", {}).get("used_vision"),
          "| content_type:", it_a.get("content_type"))
    print(" title:", it_a.get("title"))
    print(" visible_text:", (it_a.get("visible_text") or "")[:200])
    print(" urls:", res_a.urls)
    print(" tags:", it_a.get("tags"))
    print(" ingestion:", res_a.ingestion_status, "| enrichment:", res_a.enrichment_status)
    check("A1 real vision request ran", res_a.ok and it_a.get("metadata", {}).get("used_vision") is True)
    check("A2 visible_text extracted", bool(it_a.get("visible_text")))
    check("A3 URL detected", "example.com" in " ".join(res_a.urls))
    check("A4 knowledge item + original file saved",
          bool(it_a.get("id")) and bool(store.item_files(it_a.get("id"))))
    hits_a = store.search("example.com", filters={"chat_id": 555})
    check("A5 search finds the saved resource", bool(hits_a))
    if res_a.enrichment:
        print(" enrichment records:", [r.get("status") for r in res_a.enrichment])
        check("A6 enrichment attempted/completed",
              res_a.enrichment_status in ("completed", "partial") or res_a.enrichment[0].get("status") != "blocked")

    # ------------------------- Scenario B ----------------------------------
    print("\n=== SCENARIO B: dog photo + 'Это моя собака Ричи' ===")
    res_b = pipeline().ingest(inp(DOG_IMG, "Это моя собака Ричи", 7002))
    it_b = res_b.item or {}
    print(" vision used:", it_b.get("metadata", {}).get("used_vision"),
          "| content_type:", it_b.get("content_type"))
    print(" summary:", (it_b.get("summary") or "")[:200])
    print(" entities:", it_b.get("entities"))
    check("B1 real vision request ran", res_b.ok and it_b.get("metadata", {}).get("used_vision") is True)
    check("B2 description present", bool(it_b.get("summary") or it_b.get("visible_text")))
    check("B3 no person_upsert", all(a["tool"] != "person_upsert" for a in res_b.actions))
    check("B4 photo saved + linked", bool(store.item_files(it_b.get("id"))))
    check("B5 'Ричи' searchable/entity-typed",
          any(e.get("name", "").lower() in ("ричи", "richi", "richie") for e in it_b.get("entities") or [])
          or "ричи" in (it_b.get("searchable_text") or "").lower())
    hits_b = store.search_by_entity("Ричи", chat_id=555)
    files_b = store.item_files(hits_b[0]["id"]) if hits_b else []
    retrieved = files_b and Path(files_b[0]["local_path"]).exists()
    check("B6 subsequent photo search works", bool(hits_b) and retrieved,
          [f.get("local_path") for f in files_b])

    print("\n=== SUMMARY ===")
    failed = [n for n, ok in RESULTS if not ok]
    print(f"PASS {len(RESULTS) - len(failed)}/{len(RESULTS)}", ("FAILED: " + ", ".join(failed)) if failed else "")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())