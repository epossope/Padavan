"""LIVE Telegram E2E for Universal Retrieval.

Scenarios (require a real bot token and a chat that wrote to the bot):
  A: «покажи мне Тошку, дай её фото»   -> original image actually sent
  B: «где я храню базу данных по проекту Noema» -> text answer using knowledge
  C: «покажи скрин Honcho»            -> saved screenshot actually sent

chat id resolution:
  1. $env:TELEGRAM_TEST_CHAT_ID (or arg: python ... 123456)
  2. auto from get_updates() (works only while RUN.bat is NOT polling)

Exit codes: 0 = all delivered, 3 = cannot run (no chat id / conflict).
Data is seeded into a TEMP db, never touching the production one.
"""
import asyncio
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import bot  # noqa: E402
from knowledge_store import KnowledgeItem, KnowledgeStore  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond)))
    print(("PASS" if cond else "FAIL"), name, ("| " + str(detail) if detail else ""))


def seed(store, chat_id, title, entity, project, urls, summary, file_path, content_type="photo"):
    item = KnowledgeItem(
        chat_id=chat_id, content_type=content_type, title=title, summary=summary,
        visible_text=summary, searchable_text=f"{title} {summary} {' '.join(urls)}",
        urls=urls, entities=[entity],
        tags=["питомец"] if entity.get("type") == "pet" else [],
        category=("pet" if entity.get("type") == "pet" else "web_resource"),
        content_hash=title, project_id=project, source_message_id=1, status="completed",
        enrichment_status="not_required",
    )
    created, _ = store.insert_item(item)
    with store._connect() as c:
        cur = c.execute(
            """INSERT INTO files (chat_id, telegram_file_id, original_name, mime_type, local_path,
                kind, summary, extracted_text, created_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (chat_id, "", Path(file_path).name, "image/jpeg", str(file_path), "image",
             summary or "", "", created.get("created_at") or ""))
        fid = cur.lastrowid
    store.link_file(created["id"], fid, role="source")
    return created


async def send_media(bot_api, chat_id, entry):
    caption = (entry.get("caption") or "")[:200] or None
    path = entry.get("local_path") or ""
    if path and Path(path).exists():
        with Path(path).open("rb") as fh:
            return await bot_api.send_photo(chat_id=chat_id, photo=fh, caption=caption)
    return None


async def main():
    tg_chat = os.getenv("TELEGRAM_TEST_CHAT_ID", "")
    if len(sys.argv) > 1 and sys.argv[1].strip().isdigit():
        tg_chat = sys.argv[1].strip()
    if not bot.TG or "PASTE" in bot.TG:
        print("NO_TG_TOKEN")
        return 3

    from telegram import Bot
    api = Bot(bot.TG)
    await api.initialize()

    if not tg_chat:
        try:
            ups = await api.get_updates(limit=10, timeout=5)
            for u in ups:
                m = u.message or u.edited_message or u.channel_post
                if m is not None and m.chat is not None:
                    tg_chat = str(m.chat.id)
                    break
        except Exception as e:  # 409 conflict while RUN.bat is polling
            print("get_updates_failed:", repr(e))
    if not tg_chat:
        print("CHAT_ID_UNAVAILABLE")
        print("Set TELEGRAM_TEST_CHAT_ID=<id> or stop RUN.bat so get_updates() works.")
        await api.shutdown()
        return 3
    chat_id = int(tg_chat)

    # ---- isolated seed (temp db, never production data) -------------------
    tmp = Path(tempfile.mkdtemp(prefix="noema_live_retr_"))
    bot.DB = tmp / "retr.sqlite3"
    bot.init_db()
    store = KnowledgeStore(bot.DB)
    bot._pipeline = SimpleNamespace(store=store)
    bot._media_outbox.clear()
    bot._LAST_RETRIEVAL.clear()

    dog = ROOT / "storage" / "temp" / "dog_2.jpg"
    shot = ROOT / "storage" / "temp" / "design_screenshot.png"
    if not dog.exists() or not shot.exists():
        print("ASSETS_MISSING (need storage/temp/dog_2.jpg and design_screenshot.png)")
        await api.shutdown()
        return 3
    seed(store, chat_id, "Тошка", {"type": "pet", "name": "Тошка"}, None, [],
         "Собака Тошка, светлая, весёлая, на фото.", dog)
    seed(store, chat_id, "Honcho", {"type": "website", "name": "Honcho"}, "Noema",
         ["https://app.honcho.dev"],
         "База данных по проекту Noema — сервис https://app.honcho.dev, скрин приложен.", shot,
         content_type="screenshot")

    # ---- Scenario A ---------------------------------------------------------
    print("\n=== SCENARIO A: «покажи мне Тошку, дай её фото» ===")
    a = bot.execute_tool(chat_id, "knowledge_search",
                         {"query": "покажи мне Тошку, дай её фото"})
    check("A1 knowledge_search finds Тошка", a.get("count") == 1,
          str([r.get("title") for r in a.get("results", [])]))
    sent = bot.execute_tool(chat_id, "send_stored_image", {"query": "Тошка"})
    entry = (bot._media_outbox.get(chat_id) or [None])[0]
    ok = sent.get("ok") and entry is not None
    check("A2 send_stored_image queued file", bool(ok), str(sent))
    media = None
    if ok:
        media = await send_media(api, chat_id, entry)
    check("A3 original image actually sent to Telegram", media is not None,
          getattr(media, "message_id", None))

    # ---- Scenario B ---------------------------------------------------------
    print("\n=== SCENARIO B: «где я храню базу данных по проекту Noema» ===")
    b = bot.execute_tool(chat_id, "knowledge_search",
                         {"query": "где я храню базу данных по проекту Noema",
                          "project": "Noema"})
    check("B1 project-aware search finds Honcho", b.get("count") == 1,
          str([r.get("title") for r in b.get("results", [])]))
    if not b.get("results"):
        answer = "Не нашла."
    else:
        r = b["results"][0]
        answer = (f"\U0001F4CE {r.get('title')} — " + ", ".join(r.get("urls") or [])
                  + " · " + (r.get("summary") or ""))[:300]
    msg_b = await api.send_message(chat_id=chat_id, text=answer)
    check("B2 answer text sent to Telegram", msg_b is not None,
          f"{getattr(msg_b, 'message_id', None)} {answer}")

    # ---- Scenario C ---------------------------------------------------------
    print("\n=== SCENARIO C: «покажи скрин Honcho» ===")
    sent_c = bot.execute_tool(chat_id, "send_stored_image", {"query": "Honcho"})
    entry_c = (bot._media_outbox.get(chat_id) or [None])[-1]
    ok_c = sent_c.get("ok") and entry_c is not None
    check("C1 screenshot queued", bool(ok_c), str(sent_c))
    media_c = None
    if ok_c:
        media_c = await send_media(api, chat_id, entry_c)
    check("C2 screenshot actually sent to Telegram", media_c is not None,
          getattr(media_c, "message_id", None))

    await api.shutdown()
    print("\n=== SUMMARY ===")
    failed = [n for n, okv in RESULTS if not okv]
    print(f"PASS {len(RESULTS) - len(failed)}/{len(RESULTS)}",
          ("FAILED: " + ", ".join(failed)) if failed else "")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))