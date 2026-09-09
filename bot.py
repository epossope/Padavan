

import asyncio
import concurrent.futures
import contextlib

import base64

import html

import json

import os

import re

import sqlite3

import tempfile

import time

from datetime import datetime, timezone, timedelta

from pathlib import Path

from zoneinfo import ZoneInfo

from urllib.parse import urlparse



import edge_tts

import requests

from ddgs import DDGS

from dotenv import load_dotenv

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, Update

from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

from knowledge_store import KnowledgeItem, KnowledgeStore

from ingestion import (ActionBuilder, Attachment, IngestionInput, IngestionPipeline,
                       IngestionResult, VisionExtractor)

from url_enricher import HttpUrlEnricher

from retrieval import (compact_item, normalize_token, resolve_project, retrieve)
from model_router import ModelRouter
from telegram_renderer import TelegramRenderer



BASE = Path(__file__).resolve().parent

load_dotenv(BASE / ".env")



BUILD_ID = "v8-AMVERA-2026-09-09"

TG = (os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("BOT_TOKEN") or "").strip()

OR_KEY = os.getenv("OPENROUTER_API_KEY", "").strip()

MODEL = os.getenv("MODEL", "minimax/minimax-m3:free").strip()

FALLBACK_MODELS = [x.strip() for x in os.getenv("FALLBACK_MODELS", "").split(",") if x.strip()]

VISION_MODEL = os.getenv("VISION_MODEL", "google/gemini-2.5-flash-lite").strip()

VISION_FALLBACK_MODELS = [x.strip() for x in os.getenv("VISION_FALLBACK_MODELS", "google/gemini-2.5-flash-lite").split(",") if x.strip()]

STT_MODEL = os.getenv("STT_MODEL", "mistralai/voxtral-mini-transcribe").strip()

VOICE = os.getenv("EDGE_VOICE", "ru-RU-DmitryNeural").strip()

TZ_NAME = os.getenv("TIMEZONE", "Europe/Amsterdam").strip()

DEFAULT_CITY = os.getenv("DEFAULT_CITY", "Санкт-Петербург").strip()

DEFAULT_MODE = os.getenv("VOICE_REPLY_MODE", "auto").strip().lower()

MAX_FILE_MB = int(os.getenv("MAX_FILE_MB", "20"))

TZ = ZoneInfo(TZ_NAME)



_configured_data_dir = os.getenv("DATA_DIR", "").strip()
PERSISTENT_ROOT = Path(_configured_data_dir) if _configured_data_dir else (Path("/data") if Path("/data").is_dir() else BASE)
PERSISTENT_ROOT.mkdir(parents=True, exist_ok=True)
DB = PERSISTENT_ROOT / "noema_test.sqlite3"

CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"

STT_URL = "https://openrouter.ai/api/v1/audio/transcriptions"



KB = ReplyKeyboardMarkup([
    ["📚 Знания", "📅 Сегодня", "➕ Создать"],
    ["⚙️ Настройки", "☰ Ещё"],
], resize_keyboard=True)

AVAILABLE_MODELS = [x.strip() for x in os.getenv(
    "AVAILABLE_MODELS",
    "google/gemini-2.5-flash,google/gemini-2.5-pro,anthropic/claude-sonnet-4,openai/gpt-4.1"
).split(",") if x.strip()]



TOOLS = [
    {"type":"function","function":{

        "name":"internet_search",

        "description":"Найти актуальную информацию в интернете: факты, рекомендации, статьи, сервисы, товары, сравнения и ссылки. Вызывай, когда пользователь просит найти, исследовать, проверить или подобрать что-то во внешнем интернете, а не в сохранённой памяти.",

        "parameters":{"type":"object","properties":{
            "query":{"type":"string"},"limit":{"type":"integer"},"news":{"type":"boolean"}
        },"required":["query"]}

    }},
    {"type":"function","function":{

        "name":"set_reminder",

        "description":"Создать реальное напоминание. Если время можно разумно определить, не спрашивать подтверждение.",

        "parameters":{"type":"object","properties":{"text":{"type":"string"},"remind_at":{"type":"string"}},"required":["text","remind_at"]}

    }},

    {"type":"function","function":{

        "name":"save_note",

        "description":"Сохранить заметку.",

        "parameters":{"type":"object","properties":{"text":{"type":"string"},"title":{"type":"string"}},"required":["text"]}

    }},

    {"type":"function","function":{

        "name":"add_task",

        "description":"Создать задачу.",

        "parameters":{"type":"object","properties":{"text":{"type":"string"},"due_date":{"type":"string"},"priority":{"type":"string"}},"required":["text"]}

    }},

    {"type":"function","function":{

        "name":"person_upsert",

        "description":"Создать или обновить структурированный профиль человека.",

        "parameters":{"type":"object","properties":{

            "name":{"type":"string"},"relationship":{"type":"string"},"birthday":{"type":"string"},

            "age":{"type":"integer"},"home_city":{"type":"string"},"current_location":{"type":"string"},

            "projects":{"type":"string"},"notes":{"type":"string"}

        },"required":["name"]}

    }},

    {"type":"function","function":{

        "name":"person_interaction",

        "description":"Записать взаимодействие или план с человеком.",

        "parameters":{"type":"object","properties":{

            "name":{"type":"string"},"interaction":{"type":"string"},

            "interaction_date":{"type":"string"},"interaction_type":{"type":"string"}

        },"required":["name","interaction"]}

    }},

    {"type":"function","function":{

        "name":"add_expense",

        "description":"Сразу сохранить расход. Не откладывать из-за неясной категории.",

        "parameters":{"type":"object","properties":{

            "amount":{"type":"number"},"currency":{"type":"string"},"category":{"type":"string"},

            "description":{"type":"string"},"merchant":{"type":"string"},"spent_at":{"type":"string"}

        },"required":["amount","description"]}

    }},

    {"type":"function","function":{

        "name":"update_last_expense",

        "description":"Обновить последний расход после уточнения пользователя.",

        "parameters":{"type":"object","properties":{

            "category":{"type":"string"},"description":{"type":"string"},"merchant":{"type":"string"},

            "amount":{"type":"number"},"spent_at":{"type":"string"}

        }}

    }},

    {"type":"function","function":{

        "name":"get_expenses",

        "description":"Получить расходы и итог.",

        "parameters":{"type":"object","properties":{"date_from":{"type":"string"},"date_to":{"type":"string"},"category":{"type":"string"}}}

    }},

    {"type":"function","function":{

        "name":"get_today_plan",

        "description":"Получить задачи и напоминания на сегодня.",

        "parameters":{"type":"object","properties":{}}

    }},

    {"type":"function","function":{

        "name":"get_notes",

        "description":"Получить недавние заметки.",

        "parameters":{"type":"object","properties":{"limit":{"type":"integer"}}}

    }},

    {"type":"function","function":{

        "name":"get_people",

        "description":"Получить профили людей и взаимодействия.",

        "parameters":{"type":"object","properties":{"query":{"type":"string"}}}

    }},

    {"type":"function","function":{

        "name":"delete_note",

        "description":"Удалить заметку по id.",

        "parameters":{"type":"object","properties":{"note_id":{"type":"integer"}},"required":["note_id"]}

    }},

    {"type":"function","function":{

        "name":"delete_expense",

        "description":"Удалить расход по id.",

        "parameters":{"type":"object","properties":{"expense_id":{"type":"integer"}},"required":["expense_id"]}

    }},

    {"type":"function","function":{

        "name":"delete_task",

        "description":"Удалить задачу по id.",

        "parameters":{"type":"object","properties":{"task_id":{"type":"integer"}},"required":["task_id"]}

    }},

    {"type":"function","function":{

        "name":"delete_person",

        "description":"Удалить профиль человека по id.",

        "parameters":{"type":"object","properties":{"person_id":{"type":"integer"}},"required":["person_id"]}

    }},

    {"type":"function","function":{

        "name":"delete_interaction",

        "description":"Удалить взаимодействие по id.",

        "parameters":{"type":"object","properties":{"interaction_id":{"type":"integer"}},"required":["interaction_id"]}

    }},

    {"type":"function","function":{

        "name":"delete_reminder",

        "description":"Удалить напоминание по id.",

        "parameters":{"type":"object","properties":{"reminder_id":{"type":"integer"}},"required":["reminder_id"]}

    }},

    {"type":"function","function":{

        "name":"save_image_to_db",

        "description":"Сохранить изображение в базе данных с записью сводки.",

        "parameters":{"type":"object","properties":{

            "original_name":{"type":"string"},"mime_type":{"type":"string"},"local_path":{"type":"string"},

            "kind":{"type":"string"},"summary":{"type":"string"}

        },"required":["original_name","mime_type","local_path","kind"]}

    }},

    {"type":"function","function":{

        "name":"get_files",

        "description":"Получить список сохранённых файлов.",

        "parameters":{"type":"object","properties":{"kind":{"type":"string"},"limit":{"type":"integer"}}}

    }},

    {"type":"function","function":{

        "name":"get_file_from_telegram",

        "description":"Получить информацию о файле из Telegram по file_id.",

        "parameters":{"type":"object","properties":{"file_id":{"type":"string"}},"required":["file_id"]}

    }},

    {"type":"function","function":{

        "name":"send_stored_image",

        "description":"ОТПРАВИТЬ пользователю сохранённое изображение/файл. Используй, когда просят показphoto/скрин/картинку из памяти: 'покажи Тошку', 'дай фото', 'покажи скрин Shoncho/Honcho'. Ищет по knowledge_id или query и ставит файл в очередь реальной отправки через Telegram.",

        "parameters":{"type":"object","properties":{
            "knowledge_id":{"type":"integer"},"query":{"type":"string"},"file_id":{"type":"integer"},"kind":{"type":"string"},"limit":{"type":"integer"}
        }}

    }},

    {"type":"function","function":{

        "name":"knowledge_search",

        "description":"Искать ранее сохранённые пользователем знания/данные (сайты, URL, фото, скриншоты, заметки, чек). Вызывай ПЕРВЫМ, когда пользователь спрашивает о сохранённом: 'где я хранил...', 'что сохранял для проекта X', 'что ты знаешь про ...', 'какой сайт я кидал', 'покажи/найди ...'. Не говори 'у меня нет доступа', сначала сделай поиск.",

        "parameters":{"type":"object","properties":{
            "query":{"type":"string"},"project":{"type":"string"},"category":{"type":"string"},"entity":{"type":"string"},"limit":{"type":"integer"}
        },"required":["query"]}

    }},

    {"type":"function","function":{

        "name":"knowledge_get",

        "description":"Получить компактную карточку конкретного знания по его id (из results от knowledge_search).",

        "parameters":{"type":"object","properties":{
            "knowledge_id":{"type":"integer"}
        },"required":["knowledge_id"]}

    }},

    {"type":"function","function":{

        "name":"knowledge_files",

        "description":"Получить оригинальные файлы (фото/скрин/документ), привязанные к знанию по knowledge_id.",

        "parameters":{"type":"object","properties":{
            "knowledge_id":{"type":"integer"}
        },"required":["knowledge_id"]}

    }},

]



WRITE_TOOLS = {"set_reminder","save_note","add_task","person_upsert","person_interaction","add_expense","update_last_expense","delete_note","delete_expense","delete_task","delete_person","delete_interaction","delete_reminder"}



# Database and originals must live together in Amvera's persistent mount.
STORAGE_ROOT = PERSISTENT_ROOT / "storage"

_pipeline = None


def _bot_save_file(cid, name, mime, path, kind, summary, source_file_id=None):
    try:
        r = save_image_to_db(cid, name, mime, path, kind, summary, telegram_file_id=source_file_id or "")
        return r.get("id")
    except Exception:
        return None


def get_pipeline():
    global _pipeline
    if _pipeline is None:
        _pipeline = IngestionPipeline(
            store=KnowledgeStore(DB),
            vision_extractor=VisionExtractor(),
            url_enricher=HttpUrlEnricher(),
            file_saver=_bot_save_file,
            action_builder=ActionBuilder(action_runner=lambda cid, name, args: execute_tool(cid, name, args)),
            storage_dir=STORAGE_ROOT,
        )
    return _pipeline


def knowledge_search(query, filters=None, limit=10):
    """Public search interface (SQLite text search now; pgvector/hybrid later)."""
    try:
        return get_pipeline().store.search(query, filters=filters, limit=limit)
    except Exception:
        return []


def find_photos(chat_id, name, limit=5):
    """Find persisted knowledge items + original image files about `name`."""
    try:
        items = get_pipeline().store.search_by_entity(name, chat_id=chat_id, limit=limit)
    except Exception:
        return []
    out = []
    for it in items:
        for f in get_pipeline().store.item_files(it["id"]):
            if (f.get("mime_type") or "").startswith("image/") and (f.get("local_path") or f.get("telegram_file_id")):
                out.append({"item_id": it["id"], "local_path": f["local_path"],
                            "summary": it.get("summary") or it.get("title") or "",
                            "file_row": f.get("id"),
                            "telegram_file_id": f.get("telegram_file_id") or ""})
    return out


def build_inquiry_input(result):
    if not result.ok or not result.item:
        return None
    it = result.item
    parts = [p for p in (it.get("title"), it.get("summary"), it.get("visible_text")) if p]
    body = "\n".join(parts)
    if result.urls:
        body += "\nURL: " + ", ".join(result.urls[:3])
    return ("[Сохранено в память]\n" + body) if body else None


# ---------- AGENT RETRIEVAL TOOLS ----------

#: chat_id -> list of queued media entries (drained by the async handler)
_media_outbox = {}
#: chat_id -> last retrieved compact item (for follow-up context)
_LAST_RETRIEVAL = {}


def _files_for_item(it):
    try:
        return list(get_pipeline().store.item_files(it["id"]))
    except Exception:
        return []


def _remember_retrieval(chat_id, items):
    if not items:
        _LAST_RETRIEVAL.pop(chat_id, None)
        return None
    it = items[0]
    top = compact_item(it, _files_for_item(it))
    _LAST_RETRIEVAL[chat_id] = {"item": top}
    return top


def knowledge_search_tool(chat_id, query="", project=None, category=None, entity=None,
                          limit=10, date_from=None, date_to=None):
    """LLM-facing knowledge_search: compact, scoped to the current user."""
    store = get_pipeline().store
    try:
        limit = max(1, min(int(limit or 10), 20))
    except Exception:
        limit = 10
    try:
        resolved = resolve_project(store, chat_id, project) if project else None
    except Exception:
        resolved = None
    if project and not resolved:
        # stated project does not exist in this user's knowledge -> honest empty
        return {"ok": True, "tool": "knowledge_search", "query": query,
                "project": project, "project_resolved": None, "count": 0, "results": []}
    items = retrieve(store, chat_id, query, project=resolved, category=category,
                     entity=entity, limit=limit, date_from=date_from, date_to=date_to)
    results = [compact_item(it, _files_for_item(it)) for it in items]
    # Relationship lookup is source-grounded by entity_relations. It supplements
    # ordinary retrieval, never replaces it or manufactures a fact.
    relation_name = entity or (normalize_token(query).split()[0] if normalize_token(query) else "")
    relations = store.relations_for(chat_id, relation_name) if relation_name else []
    top = _remember_retrieval(chat_id, items)
    return {
        "ok": True, "tool": "knowledge_search", "query": query,
        "project": resolved, "count": len(results),
        "results": results, "relations": relations[:20], "follow_up_key": (top or {}).get("id"),
    }


def knowledge_get_tool(chat_id, knowledge_id=None):
    if not knowledge_id:
        return {"ok": False, "tool": "knowledge_get", "error": "missing_knowledge_id"}
    store = get_pipeline().store
    try:
        it = store.get_item(int(knowledge_id))
    except Exception:
        it = None
    if not it or it.get("chat_id") != chat_id:
        return {"ok": False, "tool": "knowledge_get", "error": "not_found"}
    comp = compact_item(it, _files_for_item(it))
    _LAST_RETRIEVAL[chat_id] = {"item": comp}
    return {"ok": True, "tool": "knowledge_get", "item": comp}


def knowledge_files_tool(chat_id, knowledge_id=None):
    if not knowledge_id:
        return {"ok": False, "tool": "knowledge_files", "error": "missing_knowledge_id"}
    store = get_pipeline().store
    try:
        it = store.get_item(int(knowledge_id))
    except Exception:
        it = None
    if not it or it.get("chat_id") != chat_id:
        return {"ok": False, "tool": "knowledge_files", "error": "not_found"}
    files = store.item_files(it["id"])
    return {
        "ok": True, "tool": "knowledge_files", "knowledge_id": it["id"], "count": len(files),
        "files": [{
            "file_id": f["id"], "telegram_file_id": f.get("telegram_file_id") or "",
            "original_name": f.get("original_name"), "mime_type": f.get("mime_type"),
            "local_path": f.get("local_path"), "kind": f.get("kind"),
        } for f in files],
    }


async def drain_media_outbox(update, context):
    """Send media queued by send_stored_image through the Telegram API."""
    cid = update.effective_chat.id
    entries = _media_outbox.pop(cid, [])
    for e in entries:
        caption = (e.get("caption") or "")[:200] or None
        fid = e.get("telegram_file_id") or ""
        is_image = (e.get("mime_type") or "").startswith("image/")
        try:
            if fid:
                if is_image:
                    await update.effective_message.reply_photo(photo=fid, caption=caption)
                else:
                    await update.effective_message.reply_document(
                        document=fid, filename=e.get("original_name") or "file", caption=caption)
            elif e.get("local_path") and Path(e["local_path"]).exists():
                if is_image:
                    with Path(e["local_path"]).open("rb") as fh:
                        await update.effective_message.reply_photo(photo=fh, caption=caption)
                else:
                    with Path(e["local_path"]).open("rb") as fh:
                        await update.effective_message.reply_document(
                            document=fh, filename=e.get("original_name") or "file", caption=caption)
            else:
                await update.effective_message.reply_text("Файл недоступен для отправки.")
        except Exception:
            continue


def conn():

    c = sqlite3.connect(DB)

    c.row_factory = sqlite3.Row

    return c



def ensure_column(c, table, column, sql_type):

    cols = {r["name"] for r in c.execute(f"PRAGMA table_info({table})").fetchall()}

    if column not in cols:

        c.execute(f"ALTER TABLE {table} ADD COLUMN {column} {sql_type}")



def init_db():

    with conn() as c:

        c.executescript("""

        CREATE TABLE IF NOT EXISTS settings(chat_id INTEGER PRIMARY KEY,response_mode TEXT NOT NULL DEFAULT 'auto');

        CREATE TABLE IF NOT EXISTS user_settings(
            chat_id INTEGER PRIMARY KEY,
            primary_model TEXT NOT NULL DEFAULT '',
            fallback_model TEXT NOT NULL DEFAULT '',
            vision_model TEXT NOT NULL DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS chat_models(
            chat_id INTEGER NOT NULL,
            model TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            PRIMARY KEY(chat_id, model)
        );

        CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY AUTOINCREMENT,chat_id INTEGER,role TEXT,content TEXT,created_at TEXT);

        CREATE TABLE IF NOT EXISTS reminders(id INTEGER PRIMARY KEY AUTOINCREMENT,chat_id INTEGER,text TEXT,remind_at_utc TEXT,sent INTEGER DEFAULT 0);

        CREATE TABLE IF NOT EXISTS notes(id INTEGER PRIMARY KEY AUTOINCREMENT,chat_id INTEGER,title TEXT,text TEXT,created_at TEXT);

        CREATE TABLE IF NOT EXISTS tasks(id INTEGER PRIMARY KEY AUTOINCREMENT,chat_id INTEGER,text TEXT,due_date TEXT,priority TEXT,status TEXT DEFAULT 'open',created_at TEXT);

        CREATE TABLE IF NOT EXISTS people(

            id INTEGER PRIMARY KEY AUTOINCREMENT,chat_id INTEGER,name TEXT,relationship TEXT,birthday TEXT,

            age INTEGER,home_city TEXT,current_location TEXT,projects TEXT,notes TEXT,updated_at TEXT,

            UNIQUE(chat_id,name)

        );

        CREATE TABLE IF NOT EXISTS interactions(

            id INTEGER PRIMARY KEY AUTOINCREMENT,chat_id INTEGER,person_name TEXT,interaction TEXT,

            interaction_date TEXT,interaction_type TEXT,created_at TEXT

        );

        CREATE TABLE IF NOT EXISTS expenses(

            id INTEGER PRIMARY KEY AUTOINCREMENT,chat_id INTEGER,amount REAL,currency TEXT,category TEXT,

            description TEXT,merchant TEXT,spent_at TEXT,created_at TEXT

        );

        CREATE TABLE IF NOT EXISTS briefings(

            chat_id INTEGER PRIMARY KEY,enabled INTEGER NOT NULL DEFAULT 0,time TEXT NOT NULL DEFAULT '08:00',

            city TEXT NOT NULL DEFAULT '',topics TEXT NOT NULL DEFAULT 'главные новости, ИИ, бизнес',

            last_sent_date TEXT NOT NULL DEFAULT ''

        );

        CREATE TABLE IF NOT EXISTS files(
            id INTEGER PRIMARY KEY AUTOINCREMENT,chat_id INTEGER,telegram_file_id TEXT,
            original_name TEXT,mime_type TEXT,local_path TEXT,kind TEXT,summary TEXT,
            extracted_text TEXT,created_at TEXT
        );

        """)

        # migrate older prototype DBs

        for table, col, typ in [

            ("people","age","INTEGER"),("people","home_city","TEXT"),("people","current_location","TEXT"),

            ("people","projects","TEXT"),("interactions","interaction_type","TEXT"),("expenses","merchant","TEXT")

        ]:

            ensure_column(c, table, col, typ)

    KnowledgeStore(DB).init_schema()


def model_router():
    """Construct cheaply so every request observes the latest SQLite setting."""
    return ModelRouter(conn, MODEL, FALLBACK_MODELS, VISION_MODEL)


def available_models_for(chat_id):
    """Default catalogue plus the chat owner's persistent custom choices."""
    with conn() as c:
        rows = c.execute("SELECT model, enabled FROM chat_models WHERE chat_id=?", (chat_id,)).fetchall()
    overrides = {r["model"]: bool(r["enabled"]) for r in rows}
    models = [model for model in AVAILABLE_MODELS if overrides.get(model, True)]
    models += [model for model, enabled in overrides.items() if enabled and model not in models]
    return models


def set_chat_model(chat_id, model, enabled=True):
    with conn() as c:
        c.execute("INSERT INTO chat_models(chat_id,model,enabled,created_at) VALUES(?,?,?,?) "
                  "ON CONFLICT(chat_id,model) DO UPDATE SET enabled=excluded.enabled",
                  (chat_id, model, 1 if enabled else 0, datetime.now(timezone.utc).isoformat()))



def get_mode(chat_id):

    with conn() as c:

        r = c.execute("SELECT response_mode FROM settings WHERE chat_id=?", (chat_id,)).fetchone()

    return r["response_mode"] if r else DEFAULT_MODE



def set_mode(chat_id, mode):

    with conn() as c:

        c.execute("""INSERT INTO settings(chat_id,response_mode) VALUES(?,?)

        ON CONFLICT(chat_id) DO UPDATE SET response_mode=excluded.response_mode""",(chat_id,mode))



def add_message(chat_id, role, content):

    with conn() as c:

        c.execute("INSERT INTO messages(chat_id,role,content,created_at) VALUES(?,?,?,?)",

                  (chat_id,role,content,datetime.now(timezone.utc).isoformat()))

        c.execute("""DELETE FROM messages WHERE chat_id=? AND id NOT IN(

        SELECT id FROM messages WHERE chat_id=? ORDER BY id DESC LIMIT 60)""",(chat_id,chat_id))



def history(chat_id, n=18):

    with conn() as c:

        rs = c.execute("SELECT role,content FROM messages WHERE chat_id=? ORDER BY id DESC LIMIT ?",

                       (chat_id,n)).fetchall()

    return [{"role":r["role"],"content":r["content"]} for r in reversed(rs)]



def clear_history(chat_id):

    with conn() as c:

        c.execute("DELETE FROM messages WHERE chat_id=?", (chat_id,))



def save_reminder(chat_id, text, remind_at):

    dt = datetime.fromisoformat(remind_at)

    if dt.tzinfo is None:

        dt = dt.replace(tzinfo=TZ)

    dt = dt.astimezone(TZ)

    with conn() as c:

        cur = c.execute("INSERT INTO reminders(chat_id,text,remind_at_utc,sent) VALUES(?,?,?,0)",

                        (chat_id,text,dt.astimezone(timezone.utc).isoformat()))

    return {"ok":True,"tool":"set_reminder","id":cur.lastrowid,"text":text,"local_time":dt.strftime("%d.%m.%Y %H:%M")}



def save_image_to_db(chat_id, original_name, mime_type, local_path, kind, summary=None, telegram_file_id=""):

    b64 = base64.b64encode(Path(local_path).read_bytes()).decode() if local_path else ""

    with conn() as c:

        cur = c.execute("INSERT INTO files (chat_id, telegram_file_id, original_name, mime_type, local_path, kind, summary, extracted_text, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",

                        (chat_id, telegram_file_id or "", original_name, mime_type, local_path, kind, summary, "", datetime.now(timezone.utc).isoformat()))

    return {"ok":True,"tool":"save_image","id":cur.lastrowid,"original_name":original_name}



def get_file_from_telegram(chat_id=None,file_id=None):

    try:

        from telegram import Bot

        import asyncio as _asyncio

        bot = Bot(TG)

        file = _asyncio.run(bot.get_file(file_id))

        if file is None: return {"ok":False,"tool":"get_file_from_telegram","error":"file_unavailable"}

        return {"ok":True,"tool":"get_file_from_telegram","file_id":str(file.file_id)}

    except Exception as e:

        return {"ok":False,"tool":"get_file_from_telegram","error":"file_unavailable"}



def send_stored_image(chat_id, knowledge_id=None, query=None, file_id=None, kind=None, limit=1):

    store = get_pipeline().store

    try:
        limit = max(1, min(int(limit or 1), 5))
    except Exception:
        limit = 1

    item, files = None, []
    if knowledge_id:
        try:
            item = store.get_item(int(knowledge_id))
        except Exception:
            item = None
        if not item or item.get("chat_id") != chat_id:
            return {"ok": False, "tool": "send_stored_image", "error": "not_found"}
        files = store.item_files(item["id"])
    elif query:
        items = retrieve(store, chat_id, query, limit=1)
        if not items:
            return {"ok": False, "tool": "send_stored_image", "error": "no_item_found", "query": query}
        item = items[0]
        files = store.item_files(item["id"])
    else:
        fb = get_files(chat_id=chat_id, kind=kind, limit=limit)
        files = (fb or {}).get("files") or []

    if not files:
        return {"ok": False, "tool": "send_stored_image", "error": "no_files_found"}

    images = [f for f in files if (f.get("mime_type") or "").startswith("image/")]
    chosen = (images or files)[:limit]
    queued = []
    for f in chosen:
        entry = {
            "file_row": f.get("id"),
            "telegram_file_id": f.get("telegram_file_id") or "",
            "local_path": f.get("local_path"),
            "mime_type": f.get("mime_type") or "",
            "original_name": f.get("original_name") or "файл",
            "caption": (item or {}).get("summary") or (f.get("summary") or "") or "",
        }
        queued.append(entry)
        _media_outbox.setdefault(chat_id, []).append(entry)

    if item:
        _LAST_RETRIEVAL[chat_id] = {"item": compact_item(item, files)}

    return {"ok": True, "tool": "send_stored_image", "queued": len(queued),
            "title": (item or {}).get("title") or (chosen[0].get("original_name") or "файл"),
            "media": [{"type": "queued", "original_name": e["original_name"]} for e in queued]}



def add_task(chat_id, text, due_date="", priority="normal"):

    with conn() as c:

        cur = c.execute("""INSERT INTO tasks(chat_id,text,due_date,priority,status,created_at)

        VALUES(?,?,?,?,?,?)""",(chat_id,text,due_date or "",priority or "normal","open",datetime.now(timezone.utc).isoformat()))

    return {"ok":True,"tool":"add_task","id":cur.lastrowid,"text":text,"due_date":due_date or ""}



def merge_text(old,new):

    old=(old or "").strip(); new=(new or "").strip()

    if not new: return old

    if not old: return new

    if new.lower() in old.lower(): return old

    return old+"; "+new



def person_upsert(chat_id,name,relationship="",birthday="",age=None,home_city="",current_location="",projects="",notes=""):

    with conn() as c:

        old=c.execute("SELECT * FROM people WHERE chat_id=? AND lower(name)=lower(?)",(chat_id,name)).fetchone()

        if old:

            vals = {

                "relationship":relationship or old["relationship"] or "",

                "birthday":birthday or old["birthday"] or "",

                "age":age if age is not None else old["age"],

                "home_city":home_city or old["home_city"] or "",

                "current_location":current_location or old["current_location"] or "",

                "projects":merge_text(old["projects"],projects),

                "notes":merge_text(old["notes"],notes),

            }

            c.execute("""UPDATE people SET relationship=?,birthday=?,age=?,home_city=?,current_location=?,

            projects=?,notes=?,updated_at=? WHERE id=?""",

            (vals["relationship"],vals["birthday"],vals["age"],vals["home_city"],vals["current_location"],

             vals["projects"],vals["notes"],datetime.now(timezone.utc).isoformat(),old["id"]))

            pid=old["id"]

        else:

            cur=c.execute("""INSERT INTO people(chat_id,name,relationship,birthday,age,home_city,current_location,projects,notes,updated_at)

            VALUES(?,?,?,?,?,?,?,?,?,?)""",

            (chat_id,name,relationship,birthday,age,home_city,current_location,projects,notes,datetime.now(timezone.utc).isoformat()))

            pid=cur.lastrowid

    return {"ok":True,"tool":"person_upsert","id":pid,"name":name}



def person_interaction(chat_id,name,interaction,interaction_date="",interaction_type="other"):

    if not interaction_date:

        interaction_date=datetime.now(TZ).date().isoformat()

    person_upsert(chat_id,name)

    with conn() as c:

        cur=c.execute("""INSERT INTO interactions(chat_id,person_name,interaction,interaction_date,interaction_type,created_at)

        VALUES(?,?,?,?,?,?)""",(chat_id,name,interaction,interaction_date,interaction_type or "other",datetime.now(timezone.utc).isoformat()))

    return {"ok":True,"tool":"person_interaction","id":cur.lastrowid,"name":name,"interaction":interaction}



def normalize_spent_at(x):

    if not x: return datetime.now(TZ).isoformat()

    return x+"T12:00:00" if len(x)==10 else x



def add_expense(chat_id,amount,description,currency="RUB",category="прочее",merchant="",spent_at=""):

    spent_at=normalize_spent_at(spent_at)

    with conn() as c:

        cur=c.execute("""INSERT INTO expenses(chat_id,amount,currency,category,description,merchant,spent_at,created_at)

        VALUES(?,?,?,?,?,?,?,?)""",

        (chat_id,float(amount),currency or "RUB",category or "прочее",description or "расход",merchant or "",spent_at,datetime.now(timezone.utc).isoformat()))

    return {"ok":True,"tool":"add_expense","id":cur.lastrowid,"amount":float(amount),"currency":currency or "RUB",

            "category":category or "прочее","description":description or "расход","merchant":merchant or "","spent_at":spent_at}



def update_last_expense(chat_id,category="",description="",merchant="",amount=None,spent_at=""):

    with conn() as c:

        row=c.execute("SELECT * FROM expenses WHERE chat_id=? ORDER BY id DESC LIMIT 1",(chat_id,)).fetchone()

        if not row: return {"ok":False,"tool":"update_last_expense","error":"no_expense"}

        vals=(float(amount) if amount is not None else row["amount"],

              category or row["category"] or "прочее",

              description or row["description"] or "расход",

              merchant or row["merchant"] or "",

              normalize_spent_at(spent_at) if spent_at else row["spent_at"])

        c.execute("UPDATE expenses SET amount=?,category=?,description=?,merchant=?,spent_at=? WHERE id=?",(*vals,row["id"]))

    return {"ok":True,"tool":"update_last_expense","id":row["id"],"amount":vals[0],"currency":row["currency"],

            "category":vals[1],"description":vals[2],"merchant":vals[3],"spent_at":vals[4]}



def get_expenses(chat_id,date_from="",date_to="",category=""):

    q="SELECT * FROM expenses WHERE chat_id=?"; args=[chat_id]

    if date_from: q+=" AND substr(spent_at,1,10)>=?"; args.append(date_from)

    if date_to: q+=" AND substr(spent_at,1,10)<=?"; args.append(date_to)

    if category: q+=" AND lower(category)=lower(?)"; args.append(category)

    q+=" ORDER BY spent_at DESC,id DESC LIMIT 100"

    with conn() as c:

        rows=c.execute(q,args).fetchall()

    return {"ok":True,"tool":"get_expenses","count":len(rows),

            "total":round(sum(float(r["amount"]) for r in rows),2),

            "items":[dict(r) for r in rows[:50]]}



def get_today_plan(chat_id):

    today=datetime.now(TZ).date().isoformat()

    with conn() as c:

        tasks=[dict(r) for r in c.execute("""SELECT id,text,due_date,priority,status FROM tasks

        WHERE chat_id=? AND status='open' AND (due_date='' OR due_date<=?) ORDER BY id""",(chat_id,today)).fetchall()]

        rem=[dict(r) for r in c.execute("""SELECT id,text,remind_at_utc FROM reminders

        WHERE chat_id=? AND sent=0 ORDER BY remind_at_utc""",(chat_id,)).fetchall()]

    reminders=[]

    for r in rem:

        dt=datetime.fromisoformat(r["remind_at_utc"]).astimezone(TZ)

        if dt.date().isoformat()==today:

            reminders.append({"id":r["id"],"text":r["text"],"time":dt.strftime("%H:%M")})

    return {"ok":True,"tool":"get_today_plan","date":today,"tasks":tasks,"reminders":reminders}



def get_notes(chat_id,limit=20):

    limit=max(1,min(int(limit or 20),50))

    with conn() as c:

        rows=c.execute("SELECT id,title,text,created_at FROM notes WHERE chat_id=? ORDER BY id DESC LIMIT ?",(chat_id,limit)).fetchall()

    return {"ok":True,"tool":"get_notes","notes":[dict(r) for r in rows]}



def get_people(chat_id,query=""):

    with conn() as c:

        if query:

            p=f"%{query}%"

            rows=c.execute("""SELECT * FROM people WHERE chat_id=? AND (

            lower(name) LIKE lower(?) OR lower(notes) LIKE lower(?) OR lower(relationship) LIKE lower(?) OR lower(projects) LIKE lower(?))

            ORDER BY updated_at DESC LIMIT 30""",(chat_id,p,p,p,p)).fetchall()

        else:

            rows=c.execute("SELECT * FROM people WHERE chat_id=? ORDER BY updated_at DESC LIMIT 30",(chat_id,)).fetchall()

        out=[]

        for r in rows:

            d=dict(r)

            ints=c.execute("""SELECT interaction,interaction_date,interaction_type FROM interactions

            WHERE chat_id=? AND lower(person_name)=lower(?) ORDER BY id DESC LIMIT 8""",(chat_id,r["name"])).fetchall()

            d["recent_interactions"]=[dict(x) for x in ints]

            out.append(d)

    return {"ok":True,"tool":"get_people","people":out}



def save_note(chat_id, text, title=""):

    with conn() as c:
        cur = c.execute("INSERT INTO notes(chat_id,title,text,created_at) VALUES(?,?,?,?)",
                        (chat_id, title or "", text or "", datetime.now(timezone.utc).isoformat()))
    return {"ok":True,"tool":"save_note","id":cur.lastrowid,"title":title or ""}


def delete_note(chat_id,note_id):

    with conn() as c:
        cur = c.execute("DELETE FROM notes WHERE id=? AND chat_id=?", (note_id,chat_id))
    return {"ok":True,"tool":"delete_note","deleted":cur.rowcount}


def delete_expense(chat_id,expense_id):

    with conn() as c:
        cur = c.execute("DELETE FROM expenses WHERE id=? AND chat_id=?", (expense_id,chat_id))
    return {"ok":True,"tool":"delete_expense","deleted":cur.rowcount}


def delete_task(chat_id,task_id):

    with conn() as c:
        cur = c.execute("DELETE FROM tasks WHERE id=? AND chat_id=?", (task_id,chat_id))
    return {"ok":True,"tool":"delete_task","deleted":cur.rowcount}


def delete_person(chat_id,person_id):

    with conn() as c:
        cur = c.execute("DELETE FROM people WHERE id=? AND chat_id=?", (person_id,chat_id))
    return {"ok":True,"tool":"delete_person","deleted":cur.rowcount}


def delete_interaction(chat_id,interaction_id):

    with conn() as c:
        cur = c.execute("DELETE FROM interactions WHERE id=? AND chat_id=?", (interaction_id,chat_id))
    return {"ok":True,"tool":"delete_interaction","deleted":cur.rowcount}


def delete_reminder(chat_id,reminder_id):

    with conn() as c:
        cur = c.execute("DELETE FROM reminders WHERE id=? AND chat_id=?", (reminder_id,chat_id))
    return {"ok":True,"tool":"delete_reminder","deleted":cur.rowcount}


def get_files(chat_id,kind=None,limit=5):

    limit=max(1,min(int(limit or 5),100))
    q="SELECT id,original_name,mime_type,local_path,kind,summary,created_at FROM files WHERE chat_id=?"
    args=[chat_id]
    if kind:
        q+=" AND kind=?"
        args.append(kind)
    q+=" ORDER BY id DESC LIMIT ?"
    args.append(limit)
    with conn() as c:
        rows=c.execute(q,args).fetchall()
    return {"ok":True,"tool":"get_files","files":[dict(r) for r in rows]}


def execute_tool(chat_id,name,args):

    funcs={

        "set_reminder":save_reminder,

        "save_note":save_note,

        "add_task":add_task,

        "person_upsert":person_upsert,

        "person_interaction":person_interaction,

        "add_expense":add_expense,

        "update_last_expense":update_last_expense,

        "get_expenses":get_expenses,

        "get_today_plan":get_today_plan,

        "get_notes":get_notes,

        "get_people":get_people,

        "delete_note":delete_note,

        "delete_expense":delete_expense,

        "delete_task":delete_task,

        "delete_person":delete_person,

        "delete_interaction":delete_interaction,

        "delete_reminder":delete_reminder,

        "save_image_to_db":save_image_to_db,

        "get_files":get_files,

        "get_file_from_telegram":get_file_from_telegram,

        "send_stored_image":send_stored_image,

        "internet_search":internet_search,

        "knowledge_search":knowledge_search_tool,

        "knowledge_get":knowledge_get_tool,

        "knowledge_files":knowledge_files_tool

    }

    if name not in funcs: return {"ok":False,"tool":name,"error":"unknown_tool"}

    kwargs={k:v for k,v in args.items() if k!="chat_id"}
    return funcs[name](chat_id,**kwargs)

# ---------- LIVE DATA ----------

# ---------- LIVE DATA ----------

WEATHER_CODES={0:"ясно",1:"в основном ясно",2:"переменная облачность",3:"пасмурно",45:"туман",51:"слабая морось",

61:"слабый дождь",63:"дождь",65:"сильный дождь",71:"слабый снег",73:"снег",80:"ливни",95:"гроза"}



def geocode_city(city):

    r=requests.get("https://geocoding-api.open-meteo.com/v1/search",

                   params={"name":city,"count":1,"language":"ru","format":"json"},timeout=20)

    r.raise_for_status()

    rows=r.json().get("results") or []

    return rows[0] if rows else None



def get_weather_live(city):

    try:

        loc=geocode_city(city)

        if not loc: return {"ok":False,"error":"city_not_found"}

        r=requests.get("https://api.open-meteo.com/v1/forecast",params={

            "latitude":loc["latitude"],"longitude":loc["longitude"],

            "current":"temperature_2m,apparent_temperature,relative_humidity_2m,precipitation,weather_code,wind_speed_10m",

            "minutely_15":"precipitation",

            "daily":"weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max",

            "timezone":"auto","forecast_days":2

        },timeout=20)

        r.raise_for_status(); data=r.json(); cur=data.get("current") or {}; daily=data.get("daily") or {}

        min15=data.get("minutely_15") or {}

        precip=[{"time":t,"mm":v} for t,v in list(zip(min15.get("time") or [],min15.get("precipitation") or []))[:8]]

        return {"ok":True,"provider":"Open-Meteo","city":loc.get("name",city),

            "current":{"temperature":cur.get("temperature_2m"),"feels_like":cur.get("apparent_temperature"),

                       "humidity":cur.get("relative_humidity_2m"),"precipitation":cur.get("precipitation"),

                       "wind_kmh":cur.get("wind_speed_10m"),"condition":WEATHER_CODES.get(cur.get("weather_code"),"")},

            "forecast":[{"date":daily.get("time",[None])[i],"min":daily.get("temperature_2m_min",[None])[i],

                         "max":daily.get("temperature_2m_max",[None])[i],

                         "rain_probability":daily.get("precipitation_probability_max",[None])[i],

                         "condition":WEATHER_CODES.get(daily.get("weather_code",[None])[i],"")}

                        for i in range(min(2,len(daily.get("time",[]))))],

            "next_2h_precipitation":precip}

    except Exception:

        return {"ok":False,"error":"weather_unavailable"}



def get_exchange_rate_live(base="USD",quote="RUB"):

    try:

        r=requests.get(f"https://open.er-api.com/v6/latest/{base}",timeout=20); r.raise_for_status()

        data=r.json(); rate=(data.get("rates") or {}).get(quote)

        if rate is not None: return {"ok":True,"base":base,"quote":quote,"rate":float(rate)}

    except Exception: pass

    try:

        r=requests.get("https://api.frankfurter.app/latest",params={"from":base,"to":quote},timeout=20); r.raise_for_status()

        data=r.json(); rate=(data.get("rates") or {}).get(quote)

        if rate is not None: return {"ok":True,"base":base,"quote":quote,"rate":float(rate)}

    except Exception: pass

    return {"ok":False}



def web_search_live(query,n=6,news=False):

    try:
        rows=list(DDGS(timeout=6).news(query,max_results=n)) if news else list(DDGS(timeout=6).text(query,max_results=n))

    except Exception:

        return {"ok":False,"results":[]}

    out=[]

    for r in rows:

        out.append({"title":r.get("title",""),"url":r.get("url") or r.get("href") or "",

                    "snippet":r.get("body") or r.get("description") or "","source":r.get("source","")})

    return {"ok":True,"results":out}


def internet_search(chat_id, query="", limit=6, news=False):
    """LLM tool for broad web research; it is not limited to shopping."""
    try:
        limit = max(1, min(int(limit or 6), 8))
    except Exception:
        limit = 6
    result = web_search_live(str(query or ""), limit, bool(news))
    return {"ok": bool(result.get("ok")), "tool": "internet_search", "query": query,
            "results": result.get("results") or []}



def clean_product_query(text):

    q=text

    q=re.sub(r"(?i)\b(найди|подбери|покажи|хочу купить|где купить|купить)\b"," ",q)

    q=re.sub(r"(?i)\b(?:до|за|не дороже)\s*\d[\d\s]*\s*(?:₽|р|руб(?:лей)?)\b"," ",q)

    return re.sub(r"\s+"," ",q).strip(" ,.?")



def is_vase(q):

    return bool(re.search(r"\bваз(?:а|у|ы|е|ой|очку|очка)?\b",q.lower()))



def product_ok(q,title,snippet):

    hay=f"{title} {snippet}".lower()

    if is_vase(q):

        bad=("lada","лада","автомоб","машин","ваз-210","ваз 210","запчаст","двигател","бампер","колес")

        return not any(x in hay for x in bad)

    return True



def search_products_live(text,max_price=None,city=""):

    product=clean_product_query(text)

    neg=" -ВАЗ -Lada -Лада -автомобиль -машина -запчасти" if is_vase(product) else ""

    marketplaces=[("Ozon","ozon.ru"),("Wildberries","wildberries.ru"),("Яндекс Маркет","market.yandex.ru"),

                  ("Мегамаркет","megamarket.ru"),("Avito","avito.ru")]

    items=[]

    def search_market(market):
        label, domain = market
        q=f'site:{domain} "{product}" купить'

        if max_price is not None: q+=f" до {max_price:g} рублей"

        if city: q+=f" {city}"

        q+=neg

        try:
            rows = list(DDGS(timeout=6).text(q, max_results=3))
        except Exception:
            rows = []
        return label, domain, rows

    # Market searches are independent. Running them concurrently caps a bad
    # provider/network delay at one timeout instead of five sequential waits.
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=len(marketplaces))
    futures = [pool.submit(search_market, market) for market in marketplaces]
    done, pending = concurrent.futures.wait(futures, timeout=8)
    results = []
    for future in done:
        try:
            results.append(future.result())
        except Exception:
            pass
    for future in pending:
        future.cancel()
    # Never make a Telegram reply wait for a stalled marketplace/provider.
    pool.shutdown(wait=False, cancel_futures=True)

    for label, domain, rows in results:
        for r in rows:

            title=(r.get("title") or "").strip(); url=(r.get("href") or r.get("url") or "").strip()

            sn=(r.get("body") or "").strip()

            if url and domain in url and product_ok(product,title,sn):

                items.append({"source":label,"title":title,"url":url,"snippet":sn})

    # dedupe and diversify

    seen=set(); out=[]

    for x in items:

        k=(x["url"].split("?")[0],x["title"].lower())

        if k in seen: continue

        seen.add(k); out.append(x)

    return {"ok":bool(out),"query":product,"results":out[:8],"searched_sources":[x[0] for x in marketplaces]}



def extract_city(text):

    m=re.search(r"(?:в|во)\s+([А-ЯA-ZЁ][А-Яа-яA-Za-zЁё\-\s]{2,40}?)(?:\?|$|,| сегодня| завтра)",text,re.I)

    return m.group(1).strip() if m else DEFAULT_CITY



def detect_pair(text):

    t=text.lower()

    if "евро" in t or "eur" in t: return "EUR","RUB"

    if "юан" in t or "cny" in t: return "CNY","RUB"

    return "USD","RUB"



def format_weather(d):

    if not d.get("ok"): return "Не удалось получить актуальную погоду."

    c=d["current"]; lines=[f'🌤 {d["city"]}: {c["temperature"]}°C, {c["condition"]}.',

                           f'Ощущается как {c["feels_like"]}°C. Ветер {c["wind_kmh"]} км/ч, влажность {c["humidity"]}%.']

    if d.get("forecast"):

        x=d["forecast"][0]; lines.append(f'Сегодня: {x["min"]}…{x["max"]}°C, вероятность осадков до {x["rain_probability"]}%.')

    wet=[x for x in d.get("next_2h_precipitation",[]) if isinstance(x.get("mm"),(int,float)) and x["mm"]>0]

    if d.get("next_2h_precipitation"):

        lines.append("В ближайшие 2 часа возможны осадки." if wet else "В ближайшие 2 часа заметных осадков не ожидается.")

    lines.append("Источник: Open-Meteo.")

    return "\n".join(lines)



def format_links(title,d):

    if not d.get("ok"): return "Поиск сейчас недоступен."

    rows=d.get("results") or []

    if not rows: return "Ничего подходящего не нашла."

    lines=[title]

    for r in rows[:5]:

        source=r.get("source","")

        lines.append(f'\n• {r.get("title","Результат")}' + (f' — {source}' if source else ""))

        sn=(r.get("snippet") or "").strip()

        if sn: lines.append(sn[:200] + ("..." if len(sn)>200 else ""))

        if r.get("url"): lines.append(r["url"])

    return "\n".join(lines)



def direct_live_request(text):

    t=text.lower().strip()

    if "погод" in t or "температур" in t:

        return format_weather(get_weather_live(extract_city(text)))

    if ("курс" in t and any(x in t for x in ("доллар","евро","руб","usd","eur","юан","cny"))) or "сколько стоит доллар" in t:

        b,q=detect_pair(text); d=get_exchange_rate_live(b,q)

        return f'💱 1 {b} = {d["rate"]:.4f} {q}.' if d.get("ok") else "Не удалось получить актуальный курс."

    if "новост" in t or "что нового сегодня" in t:

        q=re.sub(r"(?i)\b(свежие|последние|сегодняшние|новости|покажи|дай|найди)\b"," ",text)

        q=re.sub(r"\s+"," ",q).strip(" ,.?") or "главные новости"

        return format_links("📰 Свежие новости",web_search_live(q,6,True))

    if any(x in t for x in ("найди в интернете","поищи в интернете","погугли","сделай ресерч","исследуй в интернете","дай ссылку")):

        return format_links("🔎 Результаты поиска",web_search_live(text,6,False))

    if (any(x in t for x in ("хочу купить","где купить","найди товар","подбери","на озон","на wildberries","на вайлдберриз"))
            or ("найди" in t and re.search(r"(?:до|за|не дороже)\s*\d[\d\s]*\s*(?:₽|р|руб)", t))):

        max_price=None

        m=re.search(r"(?:до|за|не дороже)\s*(\d[\d\s]*)\s*(?:₽|р|руб)",t)

        if m:

            try: max_price=float(m.group(1).replace(" ",""))

            except: pass

        city=""

        cm=re.search(r"(?:в|по)\s+(петербург(?:е)?|санкт-петербург(?:е)?|москв(?:е|а)|брянск(?:е)?)",t)

        if cm: city=cm.group(1)

        return format_links("🛍 Нашла варианты",search_products_live(text,max_price,city))

    return None


def asks_external_web(text):
    """Conservative detector used only to encourage the web tool's first turn."""
    low = (text or "").lower()
    phrases = ("в интернете", "погугли", "ресерч", "исследуй", "найди информацию",
               "проверь в сети", "найди сайт", "найди статью", "сравни ", "отзывы о")
    return any(phrase in low for phrase in phrases)



# ---------- MODEL ----------

def system_prompt():

    now=datetime.now(TZ)

    return (

        "Ты Noema (Noema Model v1) — персональный помощник. "

        "Ты работаешь с tools: ты можешь сохранять заметки, задачи, напоминания, расходы, "

        "данные о людях и читать их обратно. Это твоя память — используй её. "

        "ВСЕГДА используй соответствующие tools, когда нужно сохранить или прочитать данные. "

        "Если пользователь просит что-то запомнить или сохранить — сразу вызывай save_note. "

        "Явную трату сохраняй сразу через add_expense. "

        "Если следующим сообщением уточняют предыдущую трату — используй update_last_expense. "

        "Людей сохраняй структурированно через person_upsert: отношение, возраст, ДР, город, текущее место, проекты, заметки. "

        "Если в одном сообщении человек + созвон/задача/напоминание — вызови несколько tools. "

        "Для чтения сохранённых данных используй get_notes, get_people, get_expenses, get_today_plan — не выдумывай. "

        "Текущие новости, погоду и курс обрабатывает внешний live-router — не выдумывай их самостоятельно. "

        "Когда пользователь просит найти, проверить, изучить, сравнить, подобрать или исследовать что-то во внешнем интернете, вызывай internet_search. Это относится не только к товарам: ищи статьи, сервисы, факты, рекомендации и ссылки. Сначала различай внешний интернет и сохранённую память пользователя. "

        "У тебя есть сохранённая память пользователя (knowledge): фото, скриншоты, сайты, URL, заметки, чек, сущности, проекты. "

        "Если пользователь спрашивает о ранее сохранённом — например «где я храню базу», «что я сохранял для Noema», «какой сайт я кидал», «покажи/найди Тошку», «что ты знаешь про ...», «что сохранял вчера», «покажи тот фото/скрин» — СНАЧАЛА сделай knowledge_search с подходящими query/project/entity. Не говори «у меня нет доступа», не написав в search. "

        "Если нужен конкретный элемент из results — можно knowledge_get по id или knowledge_files для файлов. "

        "Если пользователь просит ПОКАЗАТЬ/ДАТЬ/отправить фото или скрин — после поиска вызови send_stored_image (id найденного knowledge или query) — бот реально отправит файл. "

        "Если knowledge_search ничего не вернул — честно скажи «Я не нашла сохранённых данных по этому запросу», не выдумывай. "

        "Никогда не заявляй, что что-то сохранено, если tool не вернул ok=true. "

        "Не раскрывай внутренние модели, OpenRouter или провайдера. "

        "Отвечай коротко, естественно и персонально. "

        f"Сейчас {now.isoformat()}, timezone {TZ_NAME}."

    )



def request_chat(model,messages,tools=None,tool_choice="auto"):

    payload={"model":model,"messages":messages,"temperature":0.25,"max_tokens":int(os.getenv("CHAT_MAX_TOKENS", "1800"))}

    if tools: payload["tools"]=tools; payload["tool_choice"]=tool_choice

    return requests.post(CHAT_URL,headers={"Authorization":f"Bearer {OR_KEY}","Content-Type":"application/json"},

                         json=payload,timeout=180)



def call_or(chat_id, messages,tools=None,tool_choice="auto"):

    selected=model_router().resolve(chat_id, "chat")
    primary, fallback = selected["primary"], selected["fallback"]
    models=[primary]+[m for m in ([fallback] + FALLBACK_MODELS) if m and m!=primary]

    last=None

    for model in models:

        for attempt in range(2):

            started = time.perf_counter()
            r=request_chat(model,messages,tools,tool_choice)
            print(f"LLM request chat_id={chat_id} model={model} seconds={time.perf_counter()-started:.2f} status={r.status_code}")

            if r.ok:
                choice = r.json()["choices"][0]
                if choice.get("finish_reason") == "length":
                    print(f"LLM truncation chat_id={chat_id} model={model}")
                return choice["message"]

            last=(r.status_code,r.text)

            if r.status_code==429 or 500<=r.status_code<600:

                if attempt==0: time.sleep(1.2); continue

            break

    # If tool_choice was "required" and all models rejected it, retry with "auto"

    if tool_choice=="required":

        for model in models:

            r=request_chat(model,messages,tools,"auto")

            if r.ok: return r.json()["choices"][0]["message"]

            last=(r.status_code,r.text)

    raise RuntimeError("MODEL_BUSY" if last and last[0]==429 else "MODEL_ERROR")



def write_confirmation(results):

    parts=[]

    for r in results:

        if not r.get("ok"): continue

        n=r.get("tool")

        if n=="add_expense": parts.append(f'Записала {r["amount"]:g} {r["currency"]} — {r["description"]}.')

        elif n=="update_last_expense": parts.append(f'Обновила расход: {r["amount"]:g} {r["currency"]} — {r["category"]}, {r["description"]}.')

        elif n=="person_upsert": parts.append(f'Сохранила данные о {r["name"]}.')

        elif n=="person_interaction": parts.append(f'Записала взаимодействие с {r["name"]}.')

        elif n=="set_reminder": parts.append(f'Напоминание поставлено на {r["local_time"]}.')

        elif n=="add_task": parts.append(f'Задача добавлена: {r["text"]}.')

        elif n=="save_note": parts.append("Заметка сохранена.")

    out=[]

    for x in parts:

        if x not in out: out.append(x)

    return " ".join(out[:4]) or "Готово."



def ask(chat_id,text):

    started = time.perf_counter()

    live=direct_live_request(text)

    if live is not None:

        add_message(chat_id,"user",text); add_message(chat_id,"assistant",live)
        print(f"Request complete chat_id={chat_id} route=live seconds={time.perf_counter()-started:.2f}")
        return live



    msgs=[{"role":"system","content":system_prompt()}]

    ctx=_LAST_RETRIEVAL.get(chat_id)
    if ctx and ctx.get("item"):
        top=ctx["item"]
        note=("Контекст последнего поиска по твоей памяти: "
              f"id={top.get('id')}, title='{top.get('title') or ''}', "
              f"summary='{(top.get('summary') or '')[:300]}', "
              f"urls='{', '.join(top.get('urls') or [])}', "
              f"project='{top.get('project') or ''}', "
              f"has_files={bool(top.get('has_files'))}. "
              "Если пользователь спрашивает «его/её/то/про неё/его id», опирайся на этот элемент (можно knowledge_get/knowledge_files по id).")
        msgs.append({"role":"system","content":note})

    msgs+=history(chat_id)+[{"role":"user","content":text}]

    writes=[]

    for _ in range(5):

        # `required` made every ordinary conversation take at least two model
        # round trips. `auto` still exposes all tools, but allows a direct
        # answer when no database action is needed.
        tc="required" if _ == 0 and asks_external_web(text) else "auto"

        msg=call_or(chat_id,msgs,TOOLS,tc)

        calls=msg.get("tool_calls") or []

        if not calls:

            ans=(msg.get("content") or "").strip() or write_confirmation(writes)

            add_message(chat_id,"user",text); add_message(chat_id,"assistant",ans)
            print(f"Request complete chat_id={chat_id} route=llm seconds={time.perf_counter()-started:.2f}")
            return ans

        msgs.append(msg)

        round_results=[]

        for tc in calls:

            name=tc.get("function",{}).get("name","")

            raw=tc.get("function",{}).get("arguments","{}")

            try:

                args=json.loads(raw) if isinstance(raw,str) else raw

                result=execute_tool(chat_id,name,args or {})

            except Exception as e:

                result={"ok":False,"tool":name,"error":str(e)}

            if name in WRITE_TOOLS: writes.append(result)

            round_results.append((name,result))

            msgs.append({"role":"tool","tool_call_id":tc.get("id"),"content":json.dumps(result,ensure_ascii=False)})

        if round_results and all(name in WRITE_TOOLS for name,_ in round_results) and all(r.get("ok") for _,r in round_results):

            ans=write_confirmation(writes)

            add_message(chat_id,"user",text); add_message(chat_id,"assistant",ans); return ans

        ans=write_confirmation(writes) if writes else "Не удалось завершить действие."

    add_message(chat_id,"user",text); add_message(chat_id,"assistant",ans); return ans



# ---------- VISION ----------

def vision_prompt():

    return (

        "Ты — визуальный анализатор Noema. "

        "Подробно опиши, что изображено на этом изображении. "

        "Если на нём есть текст — распознаёй его и включи в описание. "

        "Описывай все детали: людей, предметы, места, даты, суммы, цвета, расположение объектов. "

        "Отвечай на русском языке, максимально подробно и структурированно."

    )



def request_vision(model,messages):

    payload={"model":model,"messages":messages,"temperature":0.3,"max_tokens":2000}

    return requests.post(CHAT_URL,headers={"Authorization":f"Bearer {OR_KEY}","Content-Type":"application/json"},

                         json=payload,timeout=180)



def describe_image(image_path,mime="image/jpeg",caption=""):

    b64=base64.b64encode(Path(image_path).read_bytes()).decode()

    parts=[vision_prompt()]

    if caption: parts.insert(0,f"Подпись пользователя: {caption}\n")

    parts.append("Опиши детально, что изображено на изображении.")

    content=[{"type":"text","text":" ".join(parts)},

             {"type":"image_url","image_url":{"url":f"data:{mime};base64,{b64}"}}]

    msgs=[{"role":"user","content":content}]

    models=[VISION_MODEL]+[m for m in VISION_FALLBACK_MODELS if m!=VISION_MODEL]

    last=None

    for model in models:

        for attempt in range(2):

            r=request_vision(model,msgs)

            if r.ok:

                msg=r.json()["choices"][0]["message"]

                content=msg.get("content","") or ""

                return content.strip()

            last=(r.status_code,r.text)

            if r.status_code==429 or 500<=r.status_code<600:

                if attempt==0: time.sleep(1.2); continue

            break

    raise RuntimeError("MODEL_ERROR")



# ---------- VOICE ----------

def transcribe(path):

    b64=base64.b64encode(Path(path).read_bytes()).decode()

    r=requests.post(STT_URL,headers={"Authorization":f"Bearer {OR_KEY}","Content-Type":"application/json"},

                    json={"model":STT_MODEL,"input_audio":{"data":b64,"format":"ogg"},"language":"ru"},timeout=180)

    if not r.ok: raise RuntimeError("STT_BUSY" if r.status_code==429 else "STT_ERROR")

    text=r.json().get("text","").strip()

    if not text: raise RuntimeError("STT_EMPTY")

    return text



def clean_tts(s):

    s=html.unescape(str(s)); s=re.sub(r"```(?:\w+)?\s*","",s).replace("```","")

    s=re.sub(r"`([^`]*)`",r"\1",s); s=re.sub(r"\[([^\]]+)\]\([^)]+\)",r"\1",s)

    for x in ("**","__","~~","*","_"): s=s.replace(x,"")

    s=re.sub(r"(?m)^\s{0,3}#{1,6}\s*","",s); s=re.sub(r"(?m)^\s*>\s*","",s)

    s=re.sub(r"\s+"," ",s)

    return s.strip()



async def make_voice(text):

    fd,n=tempfile.mkstemp(suffix=".mp3"); os.close(fd); p=Path(n)

    await edge_tts.Communicate(clean_tts(text) or "Готово.",VOICE).save(str(p))

    return p



def wants_voice(text):

    t=text.lower()

    return any(x in t for x in ("голосом","озвучь","скажи вслух","прочитай вслух","расскажи вслух","ответь вслух"))



async def send_answer(update,answer,voice_in=False,force_voice=False):

    mode=get_mode(update.effective_chat.id)

    eff="voice_and_text" if force_voice else (("voice_and_text" if voice_in else "text") if mode=="auto" else mode)

    if eff in ("text","voice_and_text"):
        for chunk in TelegramRenderer.chunks(answer):
            await update.effective_message.reply_text(chunk, parse_mode=TelegramRenderer.parse_mode)

    if eff in ("voice","voice_and_text"):

        p=await make_voice(answer)

        try:

            with p.open("rb") as f: await update.effective_message.reply_voice(voice=f)

        finally: p.unlink(missing_ok=True)



async def safe_error(update,e):

    code=str(e)

    msg={"MODEL_BUSY":"Сейчас модель перегружена. Повтори сообщение через минуту.",

         "MODEL_ERROR":"Не удалось получить ответ. Попробуй ещё раз.",

         "STT_BUSY":"Распознавание речи временно перегружено. Попробуй ещё раз.",

         "STT_ERROR":"Не удалось распознать голосовое.",

         "STT_EMPTY":"Не удалось распознать голосовое."}.get(code,"Не удалось выполнить запрос.")

    await update.effective_message.reply_text(msg)


def activity_labels(text):
    low = (text or "").lower()
    if any(word in low for word in ("найди", "купи", "товар", "вазу", "интернет")):
        return ["🔎 Ищу варианты…", "🔎 Проверяю источники…", "🧠 Собираю ответ…"]
    if any(word in low for word in ("где", "помни", "сохранял", "фото", "картинк")):
        return ["📚 Ищу в памяти…", "🧠 Проверяю данные…", "✍️ Готовлю ответ…"]
    return ["🧠 Думаю…", "📚 Проверяю данные…", "✍️ Готовлю ответ…"]


async def begin_activity(message, labels):
    """One temporary, unobtrusive progress card for operations lasting seconds."""
    card = await message.reply_text(labels[0])
    stopped = asyncio.Event()

    async def animate():
        index = 1
        while not stopped.is_set():
            try:
                await asyncio.wait_for(stopped.wait(), timeout=2.2)
                break
            except asyncio.TimeoutError:
                try:
                    await card.edit_text(labels[min(index, len(labels) - 1)])
                except Exception:
                    return
                index += 1

    return card, stopped, asyncio.create_task(animate())


async def end_activity(card, stopped, task):
    stopped.set()
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError, Exception):
        await task
    # The status is intentionally temporary: the final answer remains the only
    # persistent bot message in the chat.
    with contextlib.suppress(Exception):
        await card.delete()



# ---------- UI ----------

async def start(update,context):

    await update.effective_message.reply_text(f"Noema Model v1 готова.\nBuild: {BUILD_ID}",reply_markup=KB)



async def list_people(update,context):

    d=get_people(update.effective_chat.id)

    if not d["people"]: return await update.effective_message.reply_text("Людей пока нет.")

    lines=["Люди:"]

    for p in d["people"][:20]:

        facts=[]

        if p.get("relationship"): facts.append(p["relationship"])

        if p.get("age") is not None: facts.append(f'{p["age"]} лет')

        if p.get("birthday"): facts.append("ДР "+p["birthday"])

        if p.get("home_city"): facts.append(p["home_city"])

        if p.get("current_location"): facts.append("сейчас: "+p["current_location"])

        if p.get("projects"): facts.append("проекты: "+p["projects"])

        lines.append(f'• {p["name"]}'+(f' — {"; ".join(facts)}' if facts else ""))

        for x in p.get("recent_interactions",[])[:3]: lines.append(f'  ↳ {x["interaction_date"]}: {x["interaction"]}')

    await update.effective_message.reply_text("\n".join(lines))



async def list_expenses(update,context):

    today=datetime.now(TZ).date(); start=today.replace(day=1).isoformat()

    d=get_expenses(update.effective_chat.id,start,today.isoformat(),"")

    lines=[f'Расходы за месяц: {d["total"]:.2f} ₽, записей: {d["count"]}.']

    for x in d["items"][:10]:

        lines.append(f'• {str(x["spent_at"])[:10]}: {x["amount"]:g} ₽ — {x["description"]} [{x["category"]}]')

    await update.effective_message.reply_text("\n".join(lines))



async def reminders(update,context):

    with conn() as c:

        rs=c.execute("SELECT id,text,remind_at_utc FROM reminders WHERE chat_id=? AND sent=0 ORDER BY remind_at_utc",(update.effective_chat.id,)).fetchall()

    if not rs: return await update.effective_message.reply_text("Активных напоминаний нет.")

    lines=[]; buttons=[]

    for r in rs[:20]:

        dt=datetime.fromisoformat(r["remind_at_utc"]).astimezone(TZ)

        lines.append(f'#{r["id"]} — {dt:%d.%m %H:%M} — {r["text"]}')

        buttons.append([InlineKeyboardButton(f'Удалить #{r["id"]}',callback_data=f'delrem:{r["id"]}')])

    await update.effective_message.reply_text("\n".join(lines),reply_markup=InlineKeyboardMarkup(buttons))



async def notes(update,context):

    d=get_notes(update.effective_chat.id,20)

    if not d["notes"]: return await update.effective_message.reply_text("Заметок пока нет.")

    await update.effective_message.reply_text("\n".join(f'#{x["id"]} — {x["text"]}' for x in d["notes"]))



async def today_plan(update,context):

    d=get_today_plan(update.effective_chat.id); lines=["Сегодня:"]

    for t in d["tasks"]: lines.append(f'• {t["text"]}')

    for r in d["reminders"]: lines.append(f'• {r["time"]} — {r["text"]}')

    if len(lines)==1: lines.append("Пока пусто.")

    await update.effective_message.reply_text("\n".join(lines))



async def callback(update,context):

    q=update.callback_query; await q.answer()

    if q.data == "settings:model":
        selected = model_router().resolve(q.message.chat_id, "chat")
        lines = ["<b>🧠 Текущая модель</b>", html.escape(selected["primary"]), "", "Выберите модель:"]
        buttons = []
        for model in available_models_for(q.message.chat_id):
            mark = "●" if model == selected["primary"] else "○"
            buttons.append([InlineKeyboardButton(f"{mark} {model}", callback_data=f"model:set:{model}")])
        buttons += [
            [InlineKeyboardButton("➕ Добавить модель", callback_data="model:add")],
            [InlineKeyboardButton("🗑 Удалить модель", callback_data="model:delete_menu")],
            [InlineKeyboardButton("‹ Настройки", callback_data="settings:back")],
        ]
        await q.edit_message_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(buttons), parse_mode="HTML")
        return

    if q.data.startswith("model:set:"):
        model = q.data.split(":", 2)[2]
        if model not in available_models_for(q.message.chat_id):
            return await q.edit_message_text("Модель недоступна.")
        model_router().set_primary(q.message.chat_id, model)
        await q.edit_message_text(
            f"<b>🧠 Модель выбрана</b>\n{html.escape(model)}\n\nСледующее сообщение сразу будет обработано этой моделью.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Изменить", callback_data="settings:model")]]),
        )
        return

    if q.data == "model:add":
        context.user_data["awaiting_model"] = True
        await q.edit_message_text(
            "Пришлите точный ID модели OpenRouter, например:\n<code>deepseek/deepseek-v4-flash-0731</code>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Отмена", callback_data="settings:model")]]),
        )
        return

    if q.data == "model:delete_menu":
        models = available_models_for(q.message.chat_id)
        buttons = [[InlineKeyboardButton(f"🗑 {model}", callback_data=f"model:delete:{model}")] for model in models]
        buttons.append([InlineKeyboardButton("‹ К моделям", callback_data="settings:model")])
        await q.edit_message_text("Выберите модель для удаления из этого чата:", reply_markup=InlineKeyboardMarkup(buttons))
        return

    if q.data.startswith("model:delete:"):
        model = q.data.split(":", 2)[2]
        if model not in available_models_for(q.message.chat_id):
            return await q.edit_message_text("Модель уже удалена.")
        set_chat_model(q.message.chat_id, model, enabled=False)
        if model_router().resolve(q.message.chat_id, "chat")["primary"] == model:
            model_router().set_primary(q.message.chat_id, "")
        await q.edit_message_text(f"Модель удалена из списка этого чата:\n{html.escape(model)}",
                                  reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("К моделям", callback_data="settings:model")]]))
        return

    if q.data == "settings:back":
        await q.edit_message_text("⚙️ Настройки", reply_markup=settings_keyboard())
        return

    if q.data in ("menu:mode", "settings:mode"):
        await q.edit_message_text("🔊 Режим ответа", reply_markup=mode_keyboard(q.message.chat_id))
        return

    if q.data.startswith("mode:set:"):
        mode = q.data.split(":", 2)[2]
        if mode not in ("text", "voice", "voice_and_text"):
            return await q.edit_message_text("Неизвестный режим.")
        set_mode(q.message.chat_id, mode)
        labels = {"text": "💬 Текст", "voice": "🎙 Голос", "voice_and_text": "🔊 Голос + текст"}
        await q.edit_message_text(f"Режим: {labels[mode]}.", reply_markup=mode_keyboard(q.message.chat_id))
        return

    if q.data == "menu:reminders":
        return await reminders(update, context)
    if q.data == "menu:expenses":
        return await list_expenses(update, context)
    if q.data == "menu:briefing":
        return await q.edit_message_text(TelegramRenderer.render(build_briefing(q.message.chat_id)), parse_mode="HTML")
    if q.data == "menu:people":
        return await list_people(update, context)
    if q.data == "menu:notes":
        return await notes(update, context)
    if q.data == "settings:status":
        return await q.edit_message_text(status_text(q.message.chat_id))
    if q.data == "settings:keys":
        return await q.edit_message_text(
            "🔐 API-ключи\nКлючи не сохраняются в переписке: сообщения Telegram не являются защищённым хранилищем. "
            "Меняйте TELEGRAM_BOT_TOKEN и OPENROUTER_API_KEY в Secrets/Environment Vero, затем перезапускайте деплой.\n\n"
            "Модели можно добавлять и удалять прямо в этом чате через «🧠 Модель».")
    if q.data == "settings:clear":
        clear_history(q.message.chat_id)
        return await q.edit_message_text("Контекст диалога очищен. Заметки, люди, файлы и знания сохранены.")

    if q.data.startswith("delrem:"):

        rid=int(q.data.split(":")[1])

        with conn() as c: c.execute("DELETE FROM reminders WHERE id=? AND chat_id=?",(rid,q.message.chat_id))

        await q.edit_message_text(f"Напоминание #{rid} удалено.")


def settings_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🧠 Модель", callback_data="settings:model")],
        [InlineKeyboardButton("🔊 Режим ответа", callback_data="menu:mode")],
        [InlineKeyboardButton("🔐 API-ключи", callback_data="settings:keys")],
        [InlineKeyboardButton("⚙️ Статус", callback_data="settings:status")],
        [InlineKeyboardButton("🧹 Очистить диалог", callback_data="settings:clear")],
    ])


def mode_keyboard(chat_id):
    current = get_mode(chat_id)
    choices = [("text", "💬 Текст"), ("voice", "🎙 Голос"), ("voice_and_text", "🔊 Голос + текст")]
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(("● " if current == value else "○ ") + label,
                              callback_data=f"mode:set:{value}")]
        for value, label in choices
    ])


def status_text(chat_id):
    selected = model_router().resolve(chat_id, "chat")
    return (f"Build: {BUILD_ID}\n"
            f"Model: {selected['primary']}\nVision: {model_router().resolve(chat_id, 'vision')}\n"
            f"Mode: {get_mode(chat_id)}\nTimezone: {TZ_NAME}\n"
            f"Storage: {PERSISTENT_ROOT}")



def set_briefing(chat_id,enabled,time_="08:00",city="",topics=""):

    with conn() as c:

        old=c.execute("SELECT * FROM briefings WHERE chat_id=?",(chat_id,)).fetchone()

        use_city=city or (old["city"] if old else "") or DEFAULT_CITY

        use_topics=topics or (old["topics"] if old else "") or "главные новости, ИИ, бизнес"

        c.execute("""INSERT INTO briefings(chat_id,enabled,time,city,topics,last_sent_date)

        VALUES(?,?,?,?,?,?) ON CONFLICT(chat_id) DO UPDATE SET enabled=excluded.enabled,time=excluded.time,city=excluded.city,topics=excluded.topics""",

        (chat_id,1 if enabled else 0,time_,use_city,use_topics,old["last_sent_date"] if old else ""))

    return {"enabled":enabled,"time":time_,"city":use_city,"topics":use_topics}



def build_briefing(chat_id):

    with conn() as c:

        cfg=c.execute("SELECT * FROM briefings WHERE chat_id=?",(chat_id,)).fetchone()

    cfg=dict(cfg) if cfg else {"city":DEFAULT_CITY,"topics":"главные новости, ИИ, бизнес"}

    plan=get_today_plan(chat_id); weather=get_weather_live(cfg.get("city") or DEFAULT_CITY)

    topics=[x.strip() for x in (cfg.get("topics") or "главные новости").split(",") if x.strip()]

    news=[]

    for topic in topics[:3]:

        d=web_search_live(topic,3,True)

        if d.get("ok"): news += d["results"][:2]

    lines=["🌅 Утренний брифинг"]

    if plan["tasks"] or plan["reminders"]:

        lines.append("\n📅 Сегодня")

        for t in plan["tasks"][:5]: lines.append("• "+t["text"])

        for r in plan["reminders"][:5]: lines.append(f'• {r["time"]} — {r["text"]}')

    if weather.get("ok"):

        c=weather["current"]; lines.append(f'\n🌤 {weather["city"]}: {c["temperature"]}°C, {c["condition"]}.')

    if news:

        lines.append("\n📰 Главное")

        for n in news[:5]:

            lines.append("• "+n.get("title",""))

            if n.get("url"): lines.append(n["url"])

    return "\n".join(lines)



async def text_handler(update,context):

    t=update.effective_message.text.strip(); cid=update.effective_chat.id

    if context.user_data.pop("awaiting_model", False):
        model = t.strip()
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9_.:-]+", model):
            return await update.effective_message.reply_text(
                "Не похож на ID модели. Формат: <провайдер>/<модель>, например deepseek/deepseek-v4-flash-0731.")
        set_chat_model(cid, model, enabled=True)
        return await update.effective_message.reply_text(
            f"Добавила модель: {model}\nОткройте «⚙️ Настройки → 🧠 Модель» и выберите её.",
            reply_markup=settings_keyboard())

    if t=="⚙️ Настройки":
        return await update.effective_message.reply_text("⚙️ Настройки", reply_markup=settings_keyboard())

    if t=="☰ Ещё":
        return await update.effective_message.reply_text(
            "Дополнительно:", reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("👥 Люди", callback_data="menu:people")],
                [InlineKeyboardButton("📝 Заметки", callback_data="menu:notes")],
                [InlineKeyboardButton("⏰ Напоминания", callback_data="menu:reminders")],
                [InlineKeyboardButton("💰 Расходы", callback_data="menu:expenses")],
                [InlineKeyboardButton("🌅 Брифинг", callback_data="menu:briefing")],
            ]))

    if t=="➕ Создать":
        return await update.effective_message.reply_text("Напишите обычным сообщением: «создай задачу …», «напомни …» или «сохрани заметку …».")

    if t=="📚 Знания":
        return await update.effective_message.reply_text("📚 Знания\nНапишите, что найти: проект, человека, ресурс или тему. Например: «где Узел задеплоен?»")

    if t=="🎙 Голос": set_mode(cid,"voice"); return await update.effective_message.reply_text("Режим: голос.")

    if t=="💬 Текст": set_mode(cid,"text"); return await update.effective_message.reply_text("Режим: текст.")

    if t=="🔊 Голос+текст": set_mode(cid,"voice_and_text"); return await update.effective_message.reply_text("Режим: голос + текст.")

    if t=="📅 Сегодня": return await today_plan(update,context)

    if t=="⏰ Напоминания": return await reminders(update,context)

    if t=="📝 Заметки": return await notes(update,context)

    if t=="👥 Люди": return await list_people(update,context)

    if t=="💰 Расходы": return await list_expenses(update,context)

    if t=="🌅 Брифинг": return await update.effective_message.reply_text(build_briefing(cid))

    if t=="🔎 Поиск": return await update.effective_message.reply_text("Напиши: «Найди в интернете ...»")

    if t=="🛍 Товары": return await update.effective_message.reply_text("Напиши: «Хочу купить вазу до 2000 ₽»")

    if t=="⚙️ Статус":

        return await update.effective_message.reply_text(
            status_text(cid)
        )

    if t=="🧹 Очистить диалог":

        clear_history(cid); return await update.effective_message.reply_text("Контекст очищен. Сохранённые данные не удалены.")



    # simple briefing parser

    low=t.lower()

    if "каждое утро" in low and "бриф" in low:

        m=re.search(r"(\d{1,2}):(\d{2})",t)

        time_=f"{int(m.group(1)):02d}:{m.group(2)}" if m else "08:00"

        city=DEFAULT_CITY

        cm=re.search(r"город\s+([А-ЯA-ZЁ][^,.]+)",t,re.I)

        if cm: city=cm.group(1).strip()

        tm=re.search(r"тем[ыа]\s*[:\-]?\s*(.+)$",t,re.I)

        topics=tm.group(1).strip() if tm else "главные новости, ИИ, бизнес"

        cfg=set_briefing(cid,True,time_,city,topics)

        return await update.effective_message.reply_text(f'Утренний брифинг включён на {cfg["time"]}. Город: {cfg["city"]}.')

    if "отключи" in low and "бриф" in low:

        set_briefing(cid,False); return await update.effective_message.reply_text("Утренний брифинг отключён.")



    # knowledge photo retrieval: «покажи фото Ричи»

    pm=re.match(r"(?:покажи|пришли|отправь|дай|найди)\s+(?:(?:мне|пожалуйста)\s+)?(?:(?:фото|фотку|картинк\w*|изображен\w*|снимок)\s+)?(.+)$", t, re.I)

    if pm:

        name=pm.group(1).strip()

        found=find_photos(cid,name)

        if found:

            first=found[0]

            try:

                caption = (first.get("summary") or "")[:200]
                if first.get("telegram_file_id"):
                    await update.effective_message.reply_photo(photo=first["telegram_file_id"], caption=caption)
                else:
                    with Path(first["local_path"]).open("rb") as fh:
                        await update.effective_message.reply_photo(photo=fh, caption=caption)

                return

            except Exception:

                return await update.effective_message.reply_text("Нашла запись, но файл недоступен локально.")



    activity = await begin_activity(update.effective_message, activity_labels(t))
    try:
        a=await asyncio.to_thread(ask,cid,t); await send_answer(update,a,False,wants_voice(t))
        await drain_media_outbox(update, context)
    except Exception as e:
        await safe_error(update,e)
    finally:
        await end_activity(*activity)



async def voice_handler(update,context):

    fd,n=tempfile.mkstemp(suffix=".ogg"); os.close(fd); p=Path(n)

    try:

        f=await context.bot.get_file(update.effective_message.voice.file_id); await f.download_to_drive(custom_path=str(p))

        txt=await asyncio.to_thread(transcribe,p); await update.effective_message.reply_text("🎤 "+txt)

        a=await asyncio.to_thread(ask,update.effective_chat.id,txt); await send_answer(update,a,True,wants_voice(txt))

        await drain_media_outbox(update, context)

    except Exception as e: await safe_error(update,e)

    finally: p.unlink(missing_ok=True)



async def image_handler(update,context):

    cid=update.effective_chat.id

    msg=update.effective_message

    # Determine the image source

    if msg.photo:

        tg_file=await context.bot.get_file(msg.photo[-1].file_id); mime="image/jpeg"; file_size=msg.photo[-1].file_size

    elif msg.document and msg.document.mime_type and msg.document.mime_type.startswith("image/"):

        tg_file=await context.bot.get_file(msg.document.file_id); mime=msg.document.mime_type; file_size=msg.document.file_size

    else:

        return

    if file_size and file_size>MAX_FILE_MB*1024*1024:

        return await msg.reply_text(f"Изображение слишком большое (лимит {MAX_FILE_MB} МБ).")

    fd,n=tempfile.mkstemp(suffix=".img"); os.close(fd); p=Path(n)

    try:

        await tg_file.download_to_drive(custom_path=str(p))

        caption=msg.caption or ""

        status_msg=await msg.reply_text("🧠 Расшифровываю изображение…")

        try:

            src_image=msg.document if (msg.document and msg.document.mime_type and msg.document.mime_type.startswith("image/")) else msg.photo[-1]

            orig_name=(msg.document.file_name if msg.document else None) or f"image_{datetime.now().strftime("%Y%m%d%H%M%S")}.img"

            reply_body=((getattr(msg.reply_to_message,"text",None) if msg.reply_to_message else None) or (getattr(msg.reply_to_message,"caption",None) if msg.reply_to_message else None) or "")

            inp=IngestionInput(
                chat_id=cid,
                message_id=msg.message_id,
                user_text=caption,
                attachments=[Attachment(file_id=src_image.file_id, local_path=str(p),
                                         mime_type=mime, kind="image", original_name=orig_name)],
                timestamp=datetime.now(TZ),
                conversation_context=" ".join(f'{m["role"]}: {m["content"]}' for m in history(cid,6)),
                reply_to_text=reply_body,
            )

            result=await asyncio.to_thread(get_pipeline().ingest, inp)

        except Exception:

            await status_msg.edit_text("Не удалось проанализировать изображение. Попробуй другое.")

            return

        if not result.ok:

            await status_msg.edit_text("Не удалось сохранить изображение. Попробуй другое.")

            return

        pre=result.reply or "Готово."

        final_text=pre

        q=caption.strip().lower()

        if q.endswith("?") or any(w in q for w in ("что","какой","какая","какие","какое","сколько","написано","опиши","расскажи","покажи")):

            inquiry=build_inquiry_input(result)

            if inquiry:

                try:

                    await status_msg.edit_text("🧠 Изучаю материал и готовлю ответ…")
                    model_ans=await asyncio.to_thread(ask,cid,inquiry)

                    if model_ans and model_ans not in (pre,"Готово."):

                        final_text=pre+"\n\n"+model_ans

                except Exception as e: await safe_error(update,e)

        chunks = TelegramRenderer.chunks(final_text or "Готово.")
        try:
            await status_msg.edit_text(chunks[0], parse_mode=TelegramRenderer.parse_mode)
        except TypeError:  # lightweight test/message adapters without kwargs
            await status_msg.edit_text(chunks[0])
        for chunk in chunks[1:]:
            await msg.reply_text(chunk, parse_mode=TelegramRenderer.parse_mode)

        await drain_media_outbox(update, context)


    except Exception as e: await safe_error(update,e)

    finally: p.unlink(missing_ok=True)



async def reminder_tick(context):

    now=datetime.now(timezone.utc).isoformat()

    with conn() as c:

        rs=c.execute("SELECT id,chat_id,text FROM reminders WHERE sent=0 AND remind_at_utc<=? ORDER BY remind_at_utc LIMIT 50",(now,)).fetchall()

    for r in rs:

        try:

            await context.bot.send_message(chat_id=r["chat_id"],text="⏰ Напоминание: "+r["text"])

            p=await make_voice("Напоминание. "+r["text"])

            try:

                with p.open("rb") as f: await context.bot.send_voice(chat_id=r["chat_id"],voice=f)

            finally: p.unlink(missing_ok=True)

            with conn() as c: c.execute("UPDATE reminders SET sent=1 WHERE id=?",(r["id"],))

        except Exception: pass



async def briefing_tick(context):

    now=datetime.now(TZ); today=now.date().isoformat()

    with conn() as c: rows=c.execute("SELECT * FROM briefings WHERE enabled=1").fetchall()

    for row in rows:

        cfg=dict(row)

        if cfg.get("last_sent_date")==today: continue

        if now.strftime("%H:%M") < (cfg.get("time") or "08:00"): continue

        try:

            text=await asyncio.to_thread(build_briefing,cfg["chat_id"])

            await context.bot.send_message(chat_id=cfg["chat_id"],text=text)

            with conn() as c: c.execute("UPDATE briefings SET last_sent_date=? WHERE chat_id=?",(today,cfg["chat_id"]))

        except Exception: pass



def main():

    if not TG or "PASTE_" in TG:
        raise RuntimeError("Не задана переменная окружения TELEGRAM_BOT_TOKEN (или BOT_TOKEN). Добавь её в Secrets/Environment хостинга и перезапусти деплой.")

    if not OR_KEY or "PASTE_" in OR_KEY:
        raise RuntimeError("Не задана переменная окружения OPENROUTER_API_KEY. Добавь её в Secrets/Environment хостинга и перезапусти деплой.")

    init_db()

    print("="*60)

    print("NOEMA STARTED")

    print("BUILD:", BUILD_ID)

    print("PID:", os.getpid())

    print("PATH:", BASE)

    print("DATA:", PERSISTENT_ROOT)

    print("="*60)

    app=Application.builder().token(TG).build()

    app.add_handler(CommandHandler("start",start))

    app.add_handler(CallbackQueryHandler(callback))

    app.add_handler(MessageHandler(filters.VOICE,voice_handler))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND,text_handler))

    app.add_handler(MessageHandler(filters.PHOTO | filters.Document.IMAGE,image_handler))

    app.job_queue.run_repeating(reminder_tick,interval=5,first=2)

    app.job_queue.run_repeating(briefing_tick,interval=60,first=10)

    app.run_polling(drop_pending_updates=False)



if __name__=="__main__":

    main()

