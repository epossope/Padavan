

import asyncio
import concurrent.futures
import contextlib
from collections import deque

import base64

import hashlib

import hmac

import html

import json

import logging

import mimetypes

import os

import re

import secrets

import sqlite3

import tempfile

import time

import shutil
import threading
import unicodedata
from urllib.parse import parse_qsl

from datetime import datetime, timezone, timedelta

from pathlib import Path

from zoneinfo import ZoneInfo

from urllib.parse import urlparse



import edge_tts

import requests

from ddgs import DDGS

from dotenv import load_dotenv

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, MessageEntity, ReplyKeyboardMarkup, Update, WebAppInfo
from telegram.error import BadRequest, Forbidden, NetworkError, TimedOut

from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, TypeHandler, filters

from aiohttp import web
from cryptography.fernet import Fernet, InvalidToken
from pypdf import PdfReader

from knowledge_store import KnowledgeItem, KnowledgeStore

from ingestion import (ActionBuilder, Attachment, IngestionInput, IngestionPipeline,
                       IngestionResult, VisionExtractor)

from url_enricher import HttpUrlEnricher

from retrieval import (compact_item, normalize_token, resolve_project, retrieve)
from model_router import ModelRouter
from telegram_renderer import TelegramRenderer
from streaming_runtime import AdaptiveDraftThrottle, StreamAccumulator, ToolPackResolver, iter_sse_json



BASE = Path(__file__).resolve().parent

load_dotenv(BASE / ".env")



BUILD_ID = "0.3"

TG = (os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("BOT_TOKEN") or "").strip()
QUICK_ACTIONS_BASE_URL = (os.getenv("QUICK_ACTIONS_BASE_URL") or "").strip().rstrip("/")
USER_SECRETS_MASTER_KEY = (os.getenv("USER_SECRETS_MASTER_KEY") or "").strip()
ADMIN_CHAT_IDS = {int(value) for value in os.getenv("ADMIN_CHAT_IDS", "").split(",") if value.strip().isdigit()}

OR_KEY = os.getenv("OPENROUTER_API_KEY", "").strip()
OR_MANAGEMENT_KEY = os.getenv("OPENROUTER_MANAGEMENT_API_KEY", "").strip()
try:
    USER_MONTHLY_LIMIT_USD = max(0.01, float(os.getenv("NOEMA_USER_MONTHLY_LIMIT_USD", "2")))
except ValueError:
    USER_MONTHLY_LIMIT_USD = 2.0

def csv_env(name):
    return tuple(value.strip() for value in os.getenv(name, "").split(",") if value.strip())


def bool_env(name, default):
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


FAST_MODEL = os.getenv("FAST_MODEL", "qwen/qwen3.5-flash-02-23").strip()
STRONG_MODEL = os.getenv("STRONG_MODEL", "deepseek/deepseek-v3.2").strip()
FAST_MODEL_PROVIDERS = csv_env("FAST_MODEL_PROVIDERS")
STRONG_MODEL_PROVIDERS = csv_env("STRONG_MODEL_PROVIDERS")
FAST_MODEL_ALLOW_PROVIDER_FALLBACK = bool_env("FAST_MODEL_ALLOW_PROVIDER_FALLBACK", False)
STRONG_MODEL_ALLOW_PROVIDER_FALLBACK = bool_env("STRONG_MODEL_ALLOW_PROVIDER_FALLBACK", True)
MODEL = os.getenv("MODEL", FAST_MODEL).strip()

FALLBACK_MODELS = [x.strip() for x in os.getenv("FALLBACK_MODELS", STRONG_MODEL).split(",") if x.strip()]

VISION_MODEL = os.getenv("VISION_MODEL", "google/gemini-2.5-flash-lite").strip()

VISION_FALLBACK_MODELS = [x.strip() for x in os.getenv("VISION_FALLBACK_MODELS", "google/gemini-2.5-flash-lite").split(",") if x.strip()]

STT_MODEL = os.getenv("STT_MODEL", "mistralai/voxtral-mini-transcribe").strip()
MISTRAL_API_KEY = os.getenv("MISTRAL_API_KEY", "").strip()
MISTRAL_REALTIME_MODEL = os.getenv("MISTRAL_REALTIME_MODEL", "voxtral-mini-transcribe-realtime-2602").strip()
MISTRAL_CLIENT_SESSIONS_URL = os.getenv("MISTRAL_CLIENT_SESSIONS_URL", "https://api.mistral.ai/v1/client/sessions").strip()
TELEGRAM_DRAFT_STREAMING_ENABLED = os.getenv("TELEGRAM_DRAFT_STREAMING_ENABLED", "true").strip().lower() in {"1", "true", "yes"}
TELEGRAM_DRAFT_MIN_INTERVAL = max(0.8, float(os.getenv("TELEGRAM_DRAFT_MIN_INTERVAL", "0.8")))
TELEGRAM_DRAFT_MAX_INTERVAL = max(TELEGRAM_DRAFT_MIN_INTERVAL, float(os.getenv("TELEGRAM_DRAFT_MAX_INTERVAL", "1.2")))
TELEGRAM_DRAFT_MIN_CHARS = max(8, int(os.getenv("TELEGRAM_DRAFT_MIN_CHARS", "24")))
TELEGRAM_SEND_RETRIES = min(2, max(0, int(os.getenv("TELEGRAM_SEND_RETRIES", "1"))))
TELEGRAM_CONNECT_TIMEOUT = max(2.0, float(os.getenv("TELEGRAM_CONNECT_TIMEOUT", "5")))
TELEGRAM_READ_TIMEOUT = max(5.0, float(os.getenv("TELEGRAM_READ_TIMEOUT", "15")))
TELEGRAM_WRITE_TIMEOUT = max(5.0, float(os.getenv("TELEGRAM_WRITE_TIMEOUT", "15")))
TELEGRAM_POOL_TIMEOUT = max(1.0, float(os.getenv("TELEGRAM_POOL_TIMEOUT", "3")))
TELEGRAM_CONNECTION_POOL_SIZE = max(8, int(os.getenv("TELEGRAM_CONNECTION_POOL_SIZE", "32")))
REMINDER_TICK_SECONDS = min(30, max(15, int(os.getenv("REMINDER_TICK_SECONDS", "20"))))
VOICE_CONVERSATION_ENABLED = os.getenv("VOICE_CONVERSATION_ENABLED", "false").strip().lower() in {"1", "true", "yes"}
VOICE_MODE = os.getenv("VOICE_MODE", "push_to_talk").strip().lower()
VOICE_SESSION_TIMEOUT_SEC = max(5, int(os.getenv("VOICE_SESSION_TIMEOUT_SEC", "25")))
VAD_SPEECH_THRESHOLD = float(os.getenv("VAD_SPEECH_THRESHOLD", "0.035"))
VAD_END_SILENCE_MS = max(250, int(os.getenv("VAD_END_SILENCE_MS", "450")))
VAD_MIN_SPEECH_MS = max(100, int(os.getenv("VAD_MIN_SPEECH_MS", "300")))

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
OPENROUTER_KEYS_URL = "https://openrouter.ai/api/v1/keys"

STT_URL = "https://openrouter.ai/api/v1/audio/transcriptions"

MANAGED_KEY_LOCK = threading.RLock()
ACTIVE_DRAFTS_LOCK = threading.RLock()
ACTIVE_DRAFTS = {}
ACTIVE_STREAM_RESPONSES_LOCK = threading.RLock()
ACTIVE_STREAM_RESPONSES = {}
RUNTIME_METRICS = {}
LATENCY_METRICS = (
    "callback_ack_ms", "event_loop_lag_ms", "telegram_send_ms", "wake_ms",
    "stt_first_partial_ms", "stt_final_ms", "context_build_ms",
    "memory_retrieval_ms", "tool_execution_ms", "llm_ttft_ms",
    "llm_total_ms", "tts_queue_wait_ms", "tts_prepare_ms", "tts_first_start_ms",
    "tts_first_chunk_ms", "tts_voice_name", "tts_engine_name",
    "speech_text_length_chars", "total_response_start_ms", "total_ms",
)
VOICE_ROBUSTNESS_METRICS = (
    "barge_in_reason_code", "barge_in_duration_ms", "barge_in_peak_rms",
    "barge_in_rms", "barge_in_vad_probability", "audio_capture_sample_rate_hz",
    "stt_stream_sample_rate_hz", "vad_engine", "vad_engine_name",
    "vad_fallback_reason", "vad_fallback_reason_code",
    "noise_floor_rms", "speech_start_probability", "speech_start_rms",
    "realtime_empty_final_count", "realtime_fallback_batch_count",
    "batch_fallback_success_count", "stt_ws_connect_ms", "vosk_load_ms",
    "silero_load_ms", "get_user_media_ms", "mic_permission_ms",
    "conversation_ready_ms",
)
TELEMETRY_METRICS = LATENCY_METRICS + VOICE_ROBUSTNESS_METRICS
TELEMETRY_ENABLED = os.getenv("TELEMETRY_ENABLED", "false").strip().lower() in {"1", "true", "yes"}
try:
    TELEMETRY_SERIES_LIMIT = min(10000, max(50, int(os.getenv("TELEMETRY_SERIES_LIMIT", "2048"))))
except ValueError:
    TELEMETRY_SERIES_LIMIT = 2048
RUNTIME_METRIC_SERIES = {name: deque(maxlen=TELEMETRY_SERIES_LIMIT) for name in TELEMETRY_METRICS}
RUNTIME_METRICS_LOCK = threading.Lock()
# This is deliberately process-local: benchmark metadata must not create or
# mutate user records in SQLite. A restart simply requires a new reset.
TELEMETRY_BENCHMARK_STARTED_AT = None



LOGGER = logging.getLogger(__name__)

AVAILABLE_MODELS = [x.strip() for x in os.getenv(
    "AVAILABLE_MODELS",
    "google/gemini-2.5-flash,google/gemini-2.5-pro,anthropic/claude-sonnet-4,openai/gpt-4.1"
).split(",") if x.strip()]



TOOLS = [
    {"type":"function","function":{
        "name":"set_timezone",
        "description":"Установить личный часовой пояс пользователя по IANA ID, например Asia/Shanghai. Используй, когда пользователь говорит, что он переехал, находится в другой стране или просит сменить время.",
        "parameters":{"type":"object","properties":{"timezone":{"type":"string"}},"required":["timezone"]}
    }},
    {"type":"function","function":{

        "name":"save_behavior_rule",

        "description":"Сохранить правило поведения бота, явно заданное пользователем: стиль обращения, автоматизация напоминаний, предпочтения общения. Это НЕ личная заметка пользователя.",

        "parameters":{"type":"object","properties":{"description":{"type":"string"}},"required":["description"]}

    }},
    {"type":"function","function":{

        "name":"get_behavior_rules",

        "description":"Получить список правил поведения Noema с их номерами. Используй, когда пользователь просит показать, изменить или удалить правило.",

        "parameters":{"type":"object","properties":{}}

    }},
    {"type":"function","function":{

        "name":"update_behavior_rule",

        "description":"Изменить существующее правило поведения по его номеру. Сначала узнай номер через get_behavior_rules, если его не назвали.",

        "parameters":{"type":"object","properties":{"rule_id":{"type":"integer"},"description":{"type":"string"}},"required":["rule_id","description"]}

    }},
    {"type":"function","function":{

        "name":"delete_behavior_rule",

        "description":"Удалить или отключить правило поведения по его номеру. Сначала узнай номер через get_behavior_rules, если пользователь не сказал удалить все правила.",

        "parameters":{"type":"object","properties":{"rule_id":{"type":"integer"},"all":{"type":"boolean"}}}

    }},
    {"type":"function","function":{

        "name":"internet_search",

        "description":"Найти актуальную информацию в интернете: факты, рекомендации, статьи, сервисы, товары, сравнения и ссылки. Вызывай, когда пользователь просит найти, исследовать, проверить или подобрать что-то во внешнем интернете, а не в сохранённой памяти.",

        "parameters":{"type":"object","properties":{
            "query":{"type":"string"},"limit":{"type":"integer"},"news":{"type":"boolean"}
        },"required":["query"]}

    }},
    {"type":"function","function":{

        "name":"get_weather",

        "description":"Получить актуальную погоду. Используй для любого вопроса о погоде. Сам извлеки город из смысла и контекста диалога: понимай сокращения, разговорные названия и падежи; передавай нормальное название города. Если город не указан, передай пустую строку — будет использован город пользователя.",

        "parameters":{"type":"object","properties":{"city":{"type":"string"}}}

    }},
    {"type":"function","function":{

        "name":"set_reminder",

        "description":"Создать реальное напоминание с уведомлением в точное время. Используй, когда пользователь указал время или просит, чтобы бот сам напомнил. Если время можно разумно определить, не спрашивать подтверждение.",

        "parameters":{"type":"object","properties":{"text":{"type":"string"},"remind_at":{"type":"string"}},"required":["text","remind_at"]}

    }},

    {"type":"function","function":{

        "name":"save_note",

        "description":"Сохранить заметку.",

        "parameters":{"type":"object","properties":{"text":{"type":"string"},"title":{"type":"string"}},"required":["text"]}

    }},

    {"type":"function","function":{

        "name":"add_task",

        "description":"Создать задачу (дело на день без самостоятельного уведомления). Если пользователь указал точное время и ждёт сигнал от бота, используй set_reminder вместо add_task.",

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

        "name":"add_income",

        "description":"Сразу сохранить поступление или пополнение бюджета.",

        "parameters":{"type":"object","properties":{

            "amount":{"type":"number"},"currency":{"type":"string"},"category":{"type":"string"},

            "description":{"type":"string"},"merchant":{"type":"string"},"spent_at":{"type":"string"}

        },"required":["amount","description"]}

    }},

    {"type":"function","function":{

        "name":"set_briefing_preferences",

        "description":"Изменить настройки брифинга по явному пожеланию пользователя: город, темы новостей или ежедневное время отправки. Не менять без явной просьбы.",

        "parameters":{"type":"object","properties":{"city":{"type":"string"},"topics":{"type":"string"},"time":{"type":"string"},"enabled":{"type":"boolean"}}}

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



WRITE_TOOLS = {"set_timezone","set_reminder","save_note","save_behavior_rule","update_behavior_rule","delete_behavior_rule","add_task","person_upsert","person_interaction","add_expense","add_income","update_last_expense","update_task","update_note","update_reminder","update_expense","update_person","delete_note","delete_expense","delete_task","delete_person","delete_interaction","delete_reminder","set_briefing_preferences"}



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


def get_ingestion_pipeline(chat_id):
    """Keep Vision credentials scoped to the chat that sent the image."""
    return IngestionPipeline(
        store=KnowledgeStore(DB),
        vision_extractor=VisionExtractor(
            request_vision=lambda model, messages: request_vision(chat_id, model, messages),
            models=vision_models_for(chat_id),
        ),
        url_enricher=HttpUrlEnricher(),
        file_saver=_bot_save_file,
        action_builder=ActionBuilder(action_runner=lambda cid, name, args: execute_tool(cid, name, args)),
        storage_dir=STORAGE_ROOT,
    )


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
    body = "\n".join(str(part) for part in parts)[:2200]
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


def app_setting(key, default=""):
    with conn() as c:
        row = c.execute("SELECT setting_value FROM app_settings WHERE setting_key=?", (key,)).fetchone()
    return row["setting_value"] if row else default


def set_app_setting(key, value):
    with conn() as c:
        c.execute("INSERT INTO app_settings(setting_key,setting_value,updated_at) VALUES(?,?,?) "
                  "ON CONFLICT(setting_key) DO UPDATE SET setting_value=excluded.setting_value,updated_at=excluded.updated_at",
                  (key, value, datetime.now(timezone.utc).isoformat()))


def active_ui_message_id(chat_id):
    value = app_setting(f"active_ui_message:{chat_id}")
    return int(value) if str(value).isdigit() else 0


def set_active_ui_message_id(chat_id, message_id=0):
    set_app_setting(f"active_ui_message:{chat_id}", str(int(message_id or 0)))


def record_runtime_metric(name, value_ms, **detail):
    sample = {"value_ms": max(0, round(float(value_ms), 1)), "at": time.time(), **detail}
    RUNTIME_METRICS[name] = sample
    if TELEMETRY_ENABLED and name in RUNTIME_METRIC_SERIES:
        with RUNTIME_METRICS_LOCK:
            RUNTIME_METRIC_SERIES[name].append(sample["value_ms"])
    return sample


def reset_runtime_metric_series():
    """Clear only in-memory numeric samples and mark a fresh benchmark start."""
    global TELEMETRY_BENCHMARK_STARTED_AT
    started_at = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    with RUNTIME_METRICS_LOCK:
        for values in RUNTIME_METRIC_SERIES.values():
            values.clear()
        TELEMETRY_BENCHMARK_STARTED_AT = started_at
    return started_at


def _latency_percentile(values, percentile):
    if not values:
        return None
    ordered = sorted(values)
    index = (len(ordered) - 1) * percentile
    lower, upper = int(index), min(len(ordered) - 1, int(index) + 1)
    if lower == upper:
        return round(ordered[lower], 1)
    weight = index - lower
    return round(ordered[lower] * (1 - weight) + ordered[upper] * weight, 1)


def runtime_metric_export():
    with RUNTIME_METRICS_LOCK:
        series = {name: list(values) for name, values in RUNTIME_METRIC_SERIES.items()}
        started_at = TELEMETRY_BENCHMARK_STARTED_AT
    return {
        "enabled": TELEMETRY_ENABLED,
        "series_limit": TELEMETRY_SERIES_LIMIT,
        "started_at": started_at,
        "groups": {
            "latency": list(LATENCY_METRICS),
            "voice_robustness": list(VOICE_ROBUSTNESS_METRICS),
        },
        "metrics": {
            name: {
                "count": len(values),
                "avg": round(sum(values) / len(values), 1) if values else None,
                "p50": _latency_percentile(values, 0.50),
                "p95": _latency_percentile(values, 0.95),
                "max": round(max(values), 1) if values else None,
            }
            for name, values in series.items()
        },
    }


async def telegram_send_with_retry(bot, source="telegram", **kwargs):
    """Send without monopolizing handlers; retry only transient Telegram transport errors."""
    kwargs.setdefault("connect_timeout", TELEGRAM_CONNECT_TIMEOUT)
    kwargs.setdefault("read_timeout", TELEGRAM_READ_TIMEOUT)
    kwargs.setdefault("write_timeout", TELEGRAM_WRITE_TIMEOUT)
    kwargs.setdefault("pool_timeout", TELEGRAM_POOL_TIMEOUT)
    for attempt in range(TELEGRAM_SEND_RETRIES + 1):
        started = time.perf_counter()
        try:
            sent = await bot.send_message(**kwargs)
            elapsed = (time.perf_counter() - started) * 1000
            record_runtime_metric("telegram_send_ms", elapsed, source=source, attempt=attempt)
            if elapsed > 2000:
                LOGGER.warning("telemetry telegram_send_ms=%.1f source=%s attempt=%d", elapsed, source, attempt)
            else:
                LOGGER.debug("telemetry telegram_send_ms=%.1f source=%s attempt=%d", elapsed, source, attempt)
            return sent
        except Forbidden:
            record_runtime_metric("telegram_send_ms", (time.perf_counter() - started) * 1000, source=source, attempt=attempt, error="forbidden")
            raise
        except (TimedOut, NetworkError) as exc:
            elapsed = (time.perf_counter() - started) * 1000
            record_runtime_metric("telegram_send_ms", elapsed, source=source, attempt=attempt, error=type(exc).__name__)
            if attempt >= TELEGRAM_SEND_RETRIES:
                raise
            delay = 0.15 * (2 ** attempt) + secrets.randbelow(80) / 1000
            LOGGER.warning("Telegram send retry source=%s attempt=%d delay_ms=%d", source, attempt + 1, round(delay * 1000))
            await asyncio.sleep(delay)


async def replace_active_ui(update, context, text, reply_markup, parse_mode="HTML"):
    """Keep exactly one persistent inline control window per private chat."""
    chat_id = update.effective_chat.id
    previous_id = await asyncio.to_thread(active_ui_message_id, chat_id)
    if previous_id:
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=previous_id)
            await asyncio.to_thread(set_active_ui_message_id, chat_id, 0)
        except (BadRequest, Forbidden):
            await asyncio.to_thread(set_active_ui_message_id, chat_id, 0)
        except (TimedOut, NetworkError):
            LOGGER.warning("Telegram delete delayed chat_id=%s", chat_id)
    try:
        rendered_text, rendered_markup = await asyncio.to_thread(
            lambda: (live_ui_text(text), live_markup(reply_markup)))
        sent = await telegram_send_with_retry(
            context.bot, source="replace_active_ui", chat_id=chat_id,
            text=rendered_text, reply_markup=rendered_markup, parse_mode=parse_mode,
        )
    except Forbidden:
        await asyncio.to_thread(set_app_setting, f"telegram_destination_unavailable:{chat_id}", datetime.now(timezone.utc).isoformat())
        LOGGER.warning("Telegram destination unavailable chat_id=%s source=replace_active_ui", chat_id)
        return None
    except (TimedOut, NetworkError):
        LOGGER.warning("Telegram UI send timed out chat_id=%s after bounded retry", chat_id)
        return None
    await asyncio.to_thread(set_active_ui_message_id, chat_id, sent.message_id)
    return sent


async def refresh_active_ui(update, context, text, reply_markup, parse_mode="HTML"):
    """Edit the current control window after a form-style text response."""
    chat_id = update.effective_chat.id
    message_id = await asyncio.to_thread(active_ui_message_id, chat_id)
    if message_id:
        try:
            rendered_text, rendered_markup = await asyncio.to_thread(
                lambda: (live_ui_text(text), live_markup(reply_markup)))
            return await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=rendered_text,
                reply_markup=rendered_markup,
                parse_mode=parse_mode,
            )
        except Forbidden:
            await asyncio.to_thread(set_active_ui_message_id, chat_id, 0)
            return None
        except (TimedOut, NetworkError):
            LOGGER.warning("Telegram UI edit timed out chat_id=%s; keeping existing control", chat_id)
            return None
        except BadRequest:
            await asyncio.to_thread(set_active_ui_message_id, chat_id, 0)
    return await replace_active_ui(update, context, text, reply_markup, parse_mode)


async def adopt_active_ui(query):
    """Make a clicked legacy inline screen the sole active window."""
    if not query.message:
        return
    chat_id = query.message.chat_id
    message_id = query.message.message_id
    previous_id = await asyncio.to_thread(active_ui_message_id, chat_id)
    if previous_id and previous_id != message_id:
        with contextlib.suppress(Exception):
            await query.get_bot().delete_message(chat_id=chat_id, message_id=previous_id)
    await asyncio.to_thread(set_active_ui_message_id, chat_id, message_id)


async def delete_ephemeral_job(context):
    data = context.job.data or {}
    with contextlib.suppress(Exception):
        await context.bot.delete_message(chat_id=data["chat_id"], message_id=data["message_id"])


def schedule_ephemeral_delete(context, message, delay=240):
    """Remove momentary confirmations without blocking the update handler."""
    job_queue = getattr(context, "job_queue", None)
    chat = getattr(message, "chat", None)
    chat_id = getattr(chat, "id", None) or getattr(message, "chat_id", None)
    message_id = getattr(message, "message_id", None)
    if job_queue and chat_id and message_id:
        job_queue.run_once(
            delete_ephemeral_job,
            when=delay,
            data={"chat_id": int(chat_id), "message_id": int(message_id)},
            name=f"ephemeral:{chat_id}:{message_id}",
        )


EPHEMERAL_CONFIRMATION_PREFIXES = (
    "Напоминание поставлено", "Задача добавлена", "Заметка сохранена",
    "Записала расход", "Записала поступление", "Обновила расход",
    "Сохранила данные", "Записала взаимодействие", "Правило добавлено",
    "Правило обновлено", "Правило удалено", "Часовой пояс изменён",
)


def is_ephemeral_confirmation(text):
    compact = str(text or "").strip()
    return bool(compact) and any(compact.startswith(prefix) for prefix in EPHEMERAL_CONFIRMATION_PREFIXES)


EMOJI_SLOT_GROUPS = {
    "main": [("today", "Сегодня"), ("briefing", "Брифинг"), ("settings", "Настройки"), ("more", "Ещё")],
    "more": [("tasks", "Задачи"), ("reminders", "Напоминания"), ("people", "Люди"), ("notes", "Заметки"), ("budget", "Бюджет")],
    "settings": [("model", "Модель"), ("vision", "Vision"), ("replymode", "Режим ответа"), ("rules", "Правила"),
                 ("iphone", "iPhone"), ("keys", "Управление AI"), ("status", "Статус"), ("clear", "Очистить диалог")],
    "tasks": [("open", "Пустой квадрат"), ("done", "Галочка"), ("failed", "Не выполнено")],
}
EMOJI_SLOT_NAMES = {slot: label for group in EMOJI_SLOT_GROUPS.values() for slot, label in group}

# A button can have its own hand-picked icon, while the reply palette remains
# the convenient default for the rest of the interface.  The map also lets a
# configured menu icon animate the same symbol in headings and notices.
EMOJI_SLOT_BY_FALLBACK = {
    "📅": "today", "🌅": "briefing", "⚙️": "settings", "☰": "more",
    "✅": "tasks", "◻️": "open", "❌": "failed", "⏰": "reminders",
    "👥": "people", "📝": "notes", "💳": "budget", "🧠": "model",
    "👁": "vision", "🔊": "replymode", "📜": "rules", "📱": "iphone",
    "🔐": "keys", "🧹": "clear",
}


def emoji_key(value):
    """Compare emoji without text/emoji presentation variation selectors."""
    return html.unescape(str(value or "")).replace("\ufe0f", "").replace("\ufe0e", "")


def is_live_emoji_fallback(value):
    """Only custom emoji glyphs belong in the palette, never ordinary words."""
    fallback = emoji_key(value)
    if not fallback or len(fallback) > 16:
        return False
    # A custom emoji has an emoji/symbol glyph as its fallback.  Rejecting
    # letters and digits prevents a malformed entity from turning every word
    # like “Сегодня” into a picture in the UI.
    if any(char.isalnum() or unicodedata.category(char)[0] in {"L", "N"} for char in fallback):
        return False
    return any(unicodedata.category(char) == "So" for char in fallback)


def emoji_id_for(fallback):
    """Return the live equivalent for an ordinary interface emoji, if known."""
    expected = emoji_key(fallback)
    for item in reversed(reply_emoji_palette()):
        if is_live_emoji_fallback(item["alt"]) and emoji_key(item["alt"]) == expected:
            return item["id"]
    slot = EMOJI_SLOT_BY_FALLBACK.get(str(fallback or ""))
    return app_setting(f"interface_{slot}_custom_emoji_id") if slot else ""


def remove_button_fallback(text, fallback):
    """Leave a readable label after Telegram renders the live button icon."""
    label = str(text or "")
    canonical = emoji_key(fallback)
    variants = (str(fallback or ""), canonical, canonical + "\ufe0f")
    for variant in variants:
        if variant and variant in label:
            candidate = re.sub(r"\s{2,}", " ", label.replace(variant, "", 1)).strip()
            if candidate:
                return candidate
    return label


def interface_button(slot, fallback, text):
    emoji_id = app_setting(f"interface_{slot}_custom_emoji_id") or emoji_id_for(fallback)
    # The live icon is drawn separately by Telegram. Keeping the Unicode
    # fallback in the label would display two icons side by side.
    label = text if emoji_id else f"{fallback} {text}"
    return KeyboardButton(label, icon_custom_emoji_id=emoji_id or None)


def interface_inline_button(slot, fallback, text, callback_data):
    emoji_id = app_setting(f"interface_{slot}_custom_emoji_id") or emoji_id_for(fallback)
    return InlineKeyboardButton(text if emoji_id else f"{fallback} {text}", callback_data=callback_data,
                                icon_custom_emoji_id=emoji_id or None)


def main_keyboard():
    """Build the persistent keyboard with optional Telegram custom-emoji icons."""
    return ReplyKeyboardMarkup([
        [interface_button("today", "📅", "Сегодня"), interface_button("more", "☰", "Ещё"),
         interface_button("settings", "⚙️", "Настройки")],
    ], resize_keyboard=True, is_persistent=True)


def reply_emoji_palette():
    try:
        items = json.loads(app_setting("reply_custom_emoji_palette", "[]"))
    except (TypeError, ValueError):
        return []
    out = []
    for item in items if isinstance(items, list) else []:
        emoji_id = str(item.get("id") or "") if isinstance(item, dict) else ""
        alt = str(item.get("alt") or "") if isinstance(item, dict) else ""
        if emoji_id.isdigit() and is_live_emoji_fallback(alt):
            out.append({"id": emoji_id, "alt": alt})
    return out


def reply_emoji_limit(text_length):
    if text_length <= 180:
        return 1
    if text_length <= 700:
        return 3
    if text_length <= 1600:
        return 5
    return 7


def reply_emoji_prefix(chat_id):
    palette = reply_emoji_palette()
    if not palette:
        return ""
    # Stable rotation prevents a noisy random-looking feed while still using
    # the complete palette across the conversation.
    digest = hashlib.sha256(f"{chat_id}:{time.time_ns()}".encode()).digest()
    item = palette[int.from_bytes(digest[:4], "big") % len(palette)]
    return f'<tg-emoji emoji-id="{item["id"]}">{html.escape(item["alt"])}</tg-emoji> '


def animate_configured_emojis(rendered_html, limit):
    """Replace every configured Unicode fallback in a rendered reply with its live Telegram emoji."""
    replacements = {}
    variants = set()
    for item in reply_emoji_palette():
        # One animation per Unicode fallback; the newest configured variant is
        # enough and avoids nesting tags when a pack has duplicates.
        if not is_live_emoji_fallback(item["alt"]):
            continue
        canonical = emoji_key(item["alt"])
        if canonical:
            replacements[canonical] = item["id"]
            variants.update((item["alt"], canonical, canonical + "\ufe0f"))
    # A separately chosen interface icon (for example the Today calendar)
    # must also animate in headings and system notices, not just in its button.
    for fallback, slot in EMOJI_SLOT_BY_FALLBACK.items():
        emoji_id = app_setting(f"interface_{slot}_custom_emoji_id")
        canonical = emoji_key(fallback)
        if emoji_id and canonical:
            replacements[canonical] = emoji_id
            variants.update((fallback, canonical, canonical + "\ufe0f"))
    if not replacements or limit <= 0:
        return rendered_html, 0
    pattern = re.compile("|".join(re.escape(html.escape(alt)) for alt in sorted(variants, key=len, reverse=True) if alt))
    used = 0
    parts = re.split(r"(<[^>]+>)", rendered_html)
    inside_live_emoji = False
    for index, part in enumerate(parts):
        if part.startswith("<"):
            if re.match(r"<tg-emoji\b", part, re.I):
                inside_live_emoji = True
            elif re.match(r"</tg-emoji\s*>", part, re.I):
                inside_live_emoji = False
            continue
        if inside_live_emoji:
            continue
        def replace(match):
            nonlocal used
            if used >= limit:
                return match.group(0)
            alt = emoji_key(html.unescape(match.group(0)))
            used += 1
            return f'<tg-emoji emoji-id="{replacements[alt]}">{match.group(0)}</tg-emoji>'
        parts[index] = pattern.sub(replace, part)
    return "".join(parts), used


def live_ui_text(text):
    """Apply the user's live emoji palette to fixed Noema screens too."""
    # Interface screens must be consistent from top to bottom.  The 1/3/5/7
    # limit is only for conversational answers, never for lists and menus.
    rendered, _ = animate_configured_emojis(str(text or ""), 10_000)
    return rendered


def live_markup(markup):
    """Give every ordinary inline control a live icon when its emoji is in the palette."""
    if not isinstance(markup, InlineKeyboardMarkup):
        return markup
    rows = []
    palette = reply_emoji_palette()
    for row in markup.inline_keyboard:
        rendered_row = []
        for button in row:
            if button.icon_custom_emoji_id:
                fallback = next((item["alt"] for item in reversed(palette)
                                 if is_live_emoji_fallback(item["alt"])
                                 and item["id"] == button.icon_custom_emoji_id), "")
                if fallback:
                    payload = button.to_dict()
                    payload["text"] = remove_button_fallback(button.text, fallback)
                    rendered_row.append(InlineKeyboardButton.de_json(payload, None))
                else:
                    rendered_row.append(button)
                continue
            match = next((item["alt"] for item in reversed(palette)
                          if is_live_emoji_fallback(item["alt"])
                          and emoji_key(item["alt"]) in emoji_key(button.text)), "")
            emoji_id = emoji_id_for(match) if match else ""
            if not emoji_id:
                rendered_row.append(button)
                continue
            payload = button.to_dict()
            payload["icon_custom_emoji_id"] = emoji_id
            payload["text"] = remove_button_fallback(button.text, match)
            rendered_row.append(InlineKeyboardButton.de_json(payload, None))
        rows.append(rendered_row)
    return InlineKeyboardMarkup(rows)


class LiveMessage:
    """Apply the shared live palette to ordinary bot replies."""
    def __init__(self, message):
        self._message = message

    def __getattr__(self, name):
        return getattr(self._message, name)

    async def reply_text(self, text, *args, **kwargs):
        rendered = await asyncio.to_thread(live_ui_text, text)
        if rendered != str(text) and not kwargs.get("parse_mode"):
            kwargs["parse_mode"] = "HTML"
        if kwargs.get("reply_markup") is not None:
            kwargs["reply_markup"] = await asyncio.to_thread(live_markup, kwargs["reply_markup"])
        started = time.perf_counter()
        result = await self._message.reply_text(rendered, *args, **kwargs)
        record_runtime_metric("telegram_send_ms", (time.perf_counter() - started) * 1000, source="reply_text")
        return result


class LiveCallbackQuery:
    """Keep every callback screen consistent without duplicating UI plumbing."""
    def __init__(self, query):
        self._query = query
        self._message = LiveMessage(query.message) if query.message else None

    def __getattr__(self, name):
        return getattr(self._query, name)

    @property
    def message(self):
        return self._message

    async def edit_message_text(self, text, *args, **kwargs):
        rendered = await asyncio.to_thread(live_ui_text, text)
        if rendered != str(text) and not kwargs.get("parse_mode"):
            kwargs["parse_mode"] = "HTML"
        if kwargs.get("reply_markup") is not None:
            kwargs["reply_markup"] = await asyncio.to_thread(live_markup, kwargs["reply_markup"])
        started = time.perf_counter()
        result = await self._query.edit_message_text(rendered, *args, **kwargs)
        record_runtime_metric("telegram_send_ms", (time.perf_counter() - started) * 1000, source="edit_message_text")
        if kwargs.get("reply_markup") is not None and self._query.message:
            await asyncio.to_thread(set_active_ui_message_id, self._query.message.chat_id, self._query.message.message_id)
        return result



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

        CREATE TABLE IF NOT EXISTS app_settings(
            setting_key TEXT PRIMARY KEY, setting_value TEXT NOT NULL, updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS chat_models(
            chat_id INTEGER NOT NULL,
            model TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            PRIMARY KEY(chat_id, model)
        );

        CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY AUTOINCREMENT,chat_id INTEGER,role TEXT,content TEXT,created_at TEXT);

        CREATE TABLE IF NOT EXISTS conversation_summaries(
            chat_id INTEGER PRIMARY KEY, summary TEXT NOT NULL DEFAULT '',
            through_message_id INTEGER NOT NULL DEFAULT 0, version INTEGER NOT NULL DEFAULT 1,
            updated_at TEXT NOT NULL DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS reminders(id INTEGER PRIMARY KEY AUTOINCREMENT,chat_id INTEGER,text TEXT,remind_at_utc TEXT,sent INTEGER DEFAULT 0,
            acknowledged INTEGER NOT NULL DEFAULT 0, followup_count INTEGER NOT NULL DEFAULT 0,
            next_followup_at TEXT NOT NULL DEFAULT '', last_sent_message_id INTEGER);

        CREATE TABLE IF NOT EXISTS behavior_rules(
            id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER NOT NULL,
            rule_key TEXT NOT NULL, description TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1,
            UNIQUE(chat_id, rule_key)
        );

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

            description TEXT,merchant TEXT,spent_at TEXT,created_at TEXT,kind TEXT NOT NULL DEFAULT 'expense'

        );

        CREATE TABLE IF NOT EXISTS briefings(

            chat_id INTEGER PRIMARY KEY,enabled INTEGER NOT NULL DEFAULT 0,time TEXT NOT NULL DEFAULT '08:30',

            city TEXT NOT NULL DEFAULT '',topics TEXT NOT NULL DEFAULT 'главные новости, ИИ, бизнес',

            last_sent_date TEXT NOT NULL DEFAULT ''

        );

        CREATE TABLE IF NOT EXISTS quick_action_devices(
            id TEXT PRIMARY KEY, chat_id INTEGER NOT NULL, name TEXT NOT NULL,
            secret_hash TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL, last_used_at TEXT NOT NULL DEFAULT '',
            encrypted_secret TEXT NOT NULL DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS quick_action_bindings(
            device_id TEXT NOT NULL, trigger TEXT NOT NULL, action TEXT NOT NULL,
            PRIMARY KEY(device_id, trigger)
        );

        CREATE TABLE IF NOT EXISTS user_api_keys(
            chat_id INTEGER PRIMARY KEY, encrypted_key TEXT NOT NULL,
            key_hint TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1, updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS managed_api_keys(
            chat_id INTEGER PRIMARY KEY, encrypted_key TEXT NOT NULL,
            key_hash TEXT NOT NULL DEFAULT '', key_hint TEXT NOT NULL DEFAULT '',
            limit_usd REAL NOT NULL DEFAULT 2, active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS managed_key_events(
            id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER NOT NULL,
            user_number INTEGER NOT NULL DEFAULT 0, previous_key_hash TEXT NOT NULL DEFAULT '',
            new_key_hash TEXT NOT NULL DEFAULT '', reason TEXT NOT NULL, created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS usage_events(
            id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER NOT NULL,
            source TEXT NOT NULL, model TEXT NOT NULL, input_tokens INTEGER NOT NULL DEFAULT 0,
            output_tokens INTEGER NOT NULL DEFAULT 0, cost REAL NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS bot_users(
            user_number INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER NOT NULL UNIQUE,
            username TEXT NOT NULL DEFAULT '', display_name TEXT NOT NULL DEFAULT '',
            first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS user_timezones(
            chat_id INTEGER PRIMARY KEY, timezone_name TEXT NOT NULL, updated_at TEXT NOT NULL
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

            ("people","projects","TEXT"),("interactions","interaction_type","TEXT"),("expenses","merchant","TEXT"),
            ("expenses","kind","TEXT NOT NULL DEFAULT 'expense'"),
            ("reminders","acknowledged","INTEGER NOT NULL DEFAULT 0"),("reminders","followup_count","INTEGER NOT NULL DEFAULT 0"),
            ("reminders","next_followup_at","TEXT NOT NULL DEFAULT ''"),("reminders","last_sent_message_id","INTEGER"),
            ("tasks","completed_at","TEXT NOT NULL DEFAULT ''"),
            ("quick_action_devices","encrypted_secret","TEXT NOT NULL DEFAULT ''")

        ]:

            ensure_column(c, table, col, typ)

    KnowledgeStore(DB).init_schema()


def model_router():
    """Construct cheaply so every request observes the latest SQLite setting."""
    return ModelRouter(conn, MODEL, FALLBACK_MODELS, VISION_MODEL)


def provider_preferences_for(model):
    """Keep the A/B-tested model/provider routes beside the normal router.

    Returning None preserves OpenRouter's usual routing for custom per-chat
    models. The payload contains no credentials and does not alter any user
    model preference.
    """
    for configured_model, providers, allow_fallbacks in (
        (FAST_MODEL, FAST_MODEL_PROVIDERS, FAST_MODEL_ALLOW_PROVIDER_FALLBACK),
        (STRONG_MODEL, STRONG_MODEL_PROVIDERS, STRONG_MODEL_ALLOW_PROVIDER_FALLBACK),
    ):
        if model == configured_model and providers:
            configured = list(providers)
            return {
                "only": configured, "order": configured,
                "allow_fallbacks": allow_fallbacks, "require_parameters": True,
            }
    return None


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


def conversation_context(chat_id, recent_limit=10, summary_after=18, summary_chars=5000):
    """Keep raw history immutable while the prompt stays bounded and inspectable."""
    with conn() as c:
        rows = c.execute("SELECT id,role,content FROM messages WHERE chat_id=? ORDER BY id", (chat_id,)).fetchall()
        summary_row = c.execute("SELECT summary,through_message_id FROM conversation_summaries WHERE chat_id=?", (chat_id,)).fetchone()
        cutoff = max(0, len(rows) - recent_limit)
        older = rows[:cutoff]
        existing_through = int(summary_row["through_message_id"]) if summary_row else 0
        if len(rows) > summary_after and older and older[-1]["id"] > existing_through:
            # Deterministic compacting is deliberately conservative: exact state is still read through tools/DB.
            transcript = "\n".join(f'{r["role"]}: {str(r["content"] or "")[:500]}' for r in older)
            compact = transcript[-summary_chars:]
            c.execute("INSERT INTO conversation_summaries(chat_id,summary,through_message_id,version,updated_at) VALUES(?,?,?,?,?) "
                      "ON CONFLICT(chat_id) DO UPDATE SET summary=excluded.summary,through_message_id=excluded.through_message_id,version=excluded.version,updated_at=excluded.updated_at",
                      (chat_id, compact, older[-1]["id"], 1, datetime.now(timezone.utc).isoformat()))
            summary_row = {"summary": compact, "through_message_id": older[-1]["id"]}
    result = []
    if summary_row and summary_row["summary"]:
        result.append({"role": "system", "content": "Краткий контекст прошлой беседы (не источник точных данных):\n" + str(summary_row["summary"])})
    result.extend({"role": r["role"], "content": str(r["content"] or "")[:1400]} for r in rows[-recent_limit:])
    return result



def history(chat_id, n=18):

    with conn() as c:

        rs = c.execute("SELECT role,content FROM messages WHERE chat_id=? ORDER BY id DESC LIMIT ?",

                       (chat_id,n)).fetchall()

    # A long OCR/vision response must not make the next ordinary message exceed
    # a model's context window. The full original is safely kept in knowledge.
    return [{"role": r["role"], "content": str(r["content"] or "")[:1400]} for r in reversed(rs)]



def clear_history(chat_id):

    with conn() as c:

        c.execute("DELETE FROM messages WHERE chat_id=?", (chat_id,))


def ensure_behavior_rules(chat_id):
    """Bot preferences are separate from the user's notes and memory."""
    defaults = [
        ("reminder_followup", "Повторять непрочитанные напоминания через 30 минут, максимум 3 раза."),
    ]
    with conn() as c:
        for key, description in defaults:
            c.execute("INSERT OR IGNORE INTO behavior_rules(chat_id,rule_key,description,enabled) VALUES(?,?,?,1)",
                      (chat_id, key, description))


QUICK_ACTIONS = {
    "note": "💬 Сообщение Noema",
    "complete_next": "✅ Выполнить ближайшую задачу",
    "today": "📅 Что осталось сегодня",
    "break": "🧘 Начать перерыв",
}


def timezone_for(chat_id):
    """A user's local clock; fall back to the hosting default for new chats."""
    with conn() as c:
        row = c.execute("SELECT timezone_name FROM user_timezones WHERE chat_id=?", (chat_id,)).fetchone()
    try:
        return ZoneInfo(row["timezone_name"] if row else TZ_NAME)
    except Exception:
        return TZ


def timezone_name_for(chat_id):
    return timezone_for(chat_id).key


def set_user_timezone(chat_id, timezone_name):
    try:
        zone = ZoneInfo(str(timezone_name or "").strip())
    except Exception:
        return {"ok": False, "tool": "set_timezone", "error": "unknown_timezone"}
    with conn() as c:
        c.execute("INSERT INTO user_timezones(chat_id,timezone_name,updated_at) VALUES(?,?,?) "
                  "ON CONFLICT(chat_id) DO UPDATE SET timezone_name=excluded.timezone_name,updated_at=excluded.updated_at",
                  (chat_id, zone.key, datetime.now(timezone.utc).isoformat()))
    return {"ok": True, "tool": "set_timezone", "timezone": zone.key}


def encrypt_device_secret(secret):
    cipher = secrets_cipher()
    return cipher.encrypt(secret.encode()).decode() if cipher else ""


def decrypt_device_secret(value):
    cipher = secrets_cipher()
    if not cipher or not value:
        return ""
    try:
        return cipher.decrypt(value.encode()).decode()
    except (InvalidToken, UnicodeDecodeError):
        return ""


def create_quick_action_device(chat_id, name="iPhone"):
    device_id = secrets.token_urlsafe(9)
    secret = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc).isoformat()
    with conn() as c:
        c.execute("INSERT INTO quick_action_devices(id,chat_id,name,secret_hash,created_at,encrypted_secret) VALUES(?,?,?,?,?,?)",
                  (device_id, chat_id, name[:40] or "iPhone", hashlib.sha256(secret.encode()).hexdigest(), now,
                   encrypt_device_secret(secret)))
        for trigger, action in (("action", "note"), ("double", "complete_next"), ("triple", "today")):
            c.execute("INSERT INTO quick_action_bindings(device_id,trigger,action) VALUES(?,?,?)",
                      (device_id, trigger, action))
    return {"id": device_id, "secret": secret, "name": name[:40] or "iPhone"}


def quick_action_devices(chat_id):
    with conn() as c:
        devices = [dict(row) for row in c.execute(
            "SELECT id,name,active,last_used_at FROM quick_action_devices WHERE chat_id=? AND active=1 ORDER BY created_at DESC",
            (chat_id,)).fetchall()]
        for device in devices:
            device["bindings"] = {row["trigger"]: row["action"] for row in c.execute(
                "SELECT trigger,action FROM quick_action_bindings WHERE device_id=?", (device["id"],)).fetchall()}
    return devices


def set_quick_action_binding(chat_id, device_id, trigger, action):
    if trigger not in ("action", "double", "triple") or action not in QUICK_ACTIONS:
        return False
    with conn() as c:
        owned = c.execute("SELECT 1 FROM quick_action_devices WHERE id=? AND chat_id=? AND active=1", (device_id, chat_id)).fetchone()
        if not owned:
            return False
        c.execute("INSERT INTO quick_action_bindings(device_id,trigger,action) VALUES(?,?,?) ON CONFLICT(device_id,trigger) DO UPDATE SET action=excluded.action",
                  (device_id, trigger, action))
    return True


def revoke_quick_action_device(chat_id, device_id):
    with conn() as c:
        owned = c.execute("SELECT 1 FROM quick_action_devices WHERE id=? AND chat_id=?", (device_id, chat_id)).fetchone()
        if not owned:
            return False
        c.execute("DELETE FROM quick_action_bindings WHERE device_id=?", (device_id,))
        cur = c.execute("DELETE FROM quick_action_devices WHERE id=? AND chat_id=?", (device_id, chat_id))
    return bool(cur.rowcount)


def rotate_quick_action_secret(chat_id, device_id):
    secret = secrets.token_urlsafe(32)
    encrypted = encrypt_device_secret(secret)
    if not encrypted:
        return ""
    with conn() as c:
        cur = c.execute("UPDATE quick_action_devices SET secret_hash=?, encrypted_secret=? WHERE id=? AND chat_id=? AND active=1",
                        (hashlib.sha256(secret.encode()).hexdigest(), encrypted, device_id, chat_id))
    return secret if cur.rowcount else ""


def device_quick_action_secret(chat_id, device_id):
    with conn() as c:
        row = c.execute("SELECT encrypted_secret FROM quick_action_devices WHERE id=? AND chat_id=? AND active=1",
                        (device_id, chat_id)).fetchone()
    return decrypt_device_secret(row["encrypted_secret"] if row else "")


def quick_action_token(device_id, trigger, secret):
    """A single copyable token carries the non-secret device id and trigger."""
    return f"nq_{device_id}.{trigger}.{secret}"


def parse_quick_action_token(token):
    try:
        prefix, trigger, secret = str(token or "").split(".", 2)
        if not prefix.startswith("nq_") or trigger not in ("action", "double", "triple", "share", "screen") or not secret:
            return None
        return prefix[3:], trigger, secret
    except ValueError:
        return None


def authenticate_quick_token(token, allowed_triggers):
    """Validate one device token without exposing its secret to callers."""
    parsed = parse_quick_action_token(token)
    if not parsed:
        return None, "invalid_token"
    device_id, trigger, secret = parsed
    if trigger not in allowed_triggers:
        return None, "wrong_trigger"
    with conn() as c:
        device = c.execute("SELECT * FROM quick_action_devices WHERE id=? AND active=1", (device_id,)).fetchone()
        if not device or not secrets.compare_digest(device["secret_hash"], hashlib.sha256(secret.encode()).hexdigest()):
            return None, "unauthorized"
        c.execute("UPDATE quick_action_devices SET last_used_at=? WHERE id=?", (datetime.now(timezone.utc).isoformat(), device_id))
    return dict(device), ""


def secrets_cipher():
    if not USER_SECRETS_MASTER_KEY:
        return None
    try:
        return Fernet(USER_SECRETS_MASTER_KEY.encode())
    except (ValueError, TypeError):
        return None


def save_user_api_key(chat_id, api_key):
    cipher = secrets_cipher()
    if not cipher:
        return False, "master_key_missing"
    encrypted = cipher.encrypt(api_key.encode()).decode()
    hint = api_key[:7] + "…" + api_key[-4:]
    with conn() as c:
        c.execute("INSERT INTO user_api_keys(chat_id,encrypted_key,key_hint,active,updated_at) VALUES(?,?,?,?,?) "
                  "ON CONFLICT(chat_id) DO UPDATE SET encrypted_key=excluded.encrypted_key,key_hint=excluded.key_hint,active=1,updated_at=excluded.updated_at",
                  (chat_id, encrypted, hint, 1, datetime.now(timezone.utc).isoformat()))
    return True, hint


def user_api_key(chat_id):
    cipher = secrets_cipher()
    if not cipher:
        return None
    with conn() as c:
        row = c.execute("SELECT encrypted_key FROM user_api_keys WHERE chat_id=? AND active=1", (chat_id,)).fetchone()
    if not row:
        return None
    try:
        return cipher.decrypt(row["encrypted_key"].encode()).decode()
    except (InvalidToken, UnicodeDecodeError):
        return None


def remove_user_api_key(chat_id):
    with conn() as c:
        cur = c.execute("DELETE FROM user_api_keys WHERE chat_id=?", (chat_id,))
    return bool(cur.rowcount)


def api_key_status(chat_id):
    with conn() as c:
        row = c.execute("SELECT key_hint FROM user_api_keys WHERE chat_id=? AND active=1", (chat_id,)).fetchone()
    return row["key_hint"] if row else ""


def managed_api_key(chat_id):
    """Return Noema's per-user key without ever exposing it to Telegram."""
    cipher = secrets_cipher()
    if not cipher:
        return None
    with conn() as c:
        row = c.execute("SELECT encrypted_key FROM managed_api_keys WHERE chat_id=? AND active=1", (chat_id,)).fetchone()
    if not row:
        return None
    try:
        return cipher.decrypt(row["encrypted_key"].encode()).decode()
    except (InvalidToken, UnicodeDecodeError):
        return None


def provision_managed_api_key(chat_id):
    """Create a $2/month OpenRouter key for one Telegram chat, once."""
    existing = managed_api_key(chat_id)
    if existing or not OR_MANAGEMENT_KEY:
        return existing
    cipher = secrets_cipher()
    if not cipher:
        LOGGER.warning("Managed key provisioning skipped: USER_SECRETS_MASTER_KEY is missing")
        return None
    # A start event and a first message can arrive together. Avoid issuing two
    # billable keys for the same chat in that small window.
    with MANAGED_KEY_LOCK:
        existing = managed_api_key(chat_id)
        if existing:
            return existing
        with conn() as c:
            user = c.execute("SELECT user_number FROM bot_users WHERE chat_id=?", (chat_id,)).fetchone()
        number = int(user["user_number"]) if user else int(chat_id)
        # This visible name is the bridge between the OpenRouter dashboard and
        # the numbered people list in Noema. The provider's internal hash is
        # deliberately not used as a user-facing number.
        label = f"Noema · #{number:03d}"
        try:
            response = requests.post(
                OPENROUTER_KEYS_URL,
                headers={"Authorization": f"Bearer {OR_MANAGEMENT_KEY}", "Content-Type": "application/json"},
                json={"name": label, "limit": USER_MONTHLY_LIMIT_USD, "limit_reset": "monthly", "include_byok_in_limit": False},
                timeout=30,
            )
        except requests.RequestException:
            LOGGER.warning("Managed key provisioning failed for chat %s", chat_id)
            return None
        if not response.ok:
            LOGGER.warning("Managed key provisioning rejected for chat %s: HTTP %s", chat_id, response.status_code)
            return None
        try:
            payload = response.json()
            raw_key = str(payload.get("key") or "")
            details = payload.get("data") or {}
            key_hash = str(details.get("hash") or "")
        except (TypeError, ValueError):
            return None
        if not raw_key:
            LOGGER.warning("Managed key provisioning returned no key for chat %s", chat_id)
            return None
        now = datetime.now(timezone.utc).isoformat()
        encrypted = cipher.encrypt(raw_key.encode()).decode()
        hint = raw_key[:7] + "…" + raw_key[-4:]
        with conn() as c:
            c.execute("INSERT INTO managed_api_keys(chat_id,encrypted_key,key_hash,key_hint,limit_usd,active,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?) "
                      "ON CONFLICT(chat_id) DO UPDATE SET encrypted_key=excluded.encrypted_key,key_hash=excluded.key_hash,key_hint=excluded.key_hint,limit_usd=excluded.limit_usd,active=1,updated_at=excluded.updated_at",
                      (chat_id, encrypted, key_hash, hint, USER_MONTHLY_LIMIT_USD, 1, now, now))
        return raw_key


def replace_missing_managed_api_key(chat_id, reason="OpenRouter rejected the previous key"):
    """Replace only a confirmed-invalid managed key and preserve an audit trail."""
    if not OR_MANAGEMENT_KEY or not secrets_cipher():
        return None
    with MANAGED_KEY_LOCK:
        with conn() as c:
            old = c.execute("SELECT key_hash FROM managed_api_keys WHERE chat_id=? AND active=1", (chat_id,)).fetchone()
            user = c.execute("SELECT user_number FROM bot_users WHERE chat_id=?", (chat_id,)).fetchone()
            if not old:
                return None
            c.execute("UPDATE managed_api_keys SET active=0,updated_at=? WHERE chat_id=?",
                      (datetime.now(timezone.utc).isoformat(), chat_id))
        new_key = provision_managed_api_key(chat_id)
        if not new_key:
            # Do not silently fall through to the common project key after a
            # user's private key was deleted remotely.
            with conn() as c:
                c.execute("UPDATE managed_api_keys SET active=1 WHERE chat_id=?", (chat_id,))
            return None
        with conn() as c:
            current = c.execute("SELECT key_hash FROM managed_api_keys WHERE chat_id=?", (chat_id,)).fetchone()
            c.execute("INSERT INTO managed_key_events(chat_id,user_number,previous_key_hash,new_key_hash,reason,created_at) VALUES(?,?,?,?,?,?)",
                      (chat_id, int(user["user_number"]) if user else 0, old["key_hash"] or "",
                       current["key_hash"] if current else "", reason, datetime.now(timezone.utc).isoformat()))
        LOGGER.warning("Reissued managed OpenRouter key for chat %s", chat_id)
        return new_key


def recover_missing_managed_key(chat_id, response):
    """A 401 is the safe signal for a deleted/revoked credential, not a quota error."""
    if getattr(response, "status_code", None) != 401 or not managed_api_key(chat_id):
        return False
    return bool(replace_missing_managed_api_key(chat_id, "OpenRouter returned HTTP 401"))


def managed_key_history(chat_id, limit=5):
    with conn() as c:
        rows = c.execute("SELECT reason,created_at FROM managed_key_events WHERE chat_id=? ORDER BY id DESC LIMIT ?",
                         (chat_id, limit)).fetchall()
    return [dict(row) for row in rows]


def sync_managed_key_labels():
    """Give active users a key, then match OpenRouter names to Noema numbers."""
    if not OR_MANAGEMENT_KEY:
        return {"ok": False, "error": "management_key_missing"}
    created = 0
    # Some people may have spent through the old fallback key before the
    # management key was configured. Bring those active people onto their own
    # key first, rather than trying to guess which unrelated OpenRouter row is
    # theirs by its position in the dashboard.
    for user in shared_usage_users():
        chat_id = int(user["chat_id"])
        if managed_api_key(chat_id):
            continue
        if provision_managed_api_key(chat_id):
            created += 1
    with conn() as c:
        rows = [dict(row) for row in c.execute(
            """SELECT m.key_hash, u.user_number FROM managed_api_keys m
               JOIN bot_users u ON u.chat_id=m.chat_id
               WHERE m.active=1 AND m.key_hash<>''"""
        ).fetchall()]
    if not rows:
        return {"ok": True, "created": created, "updated": 0}
    headers = {"Authorization": f"Bearer {OR_MANAGEMENT_KEY}", "Content-Type": "application/json"}
    try:
        response = requests.get(OPENROUTER_KEYS_URL, headers=headers, timeout=30)
        response.raise_for_status()
        remote = {str(key.get("hash") or ""): str(key.get("name") or "")
                  for key in (response.json().get("data") or [])}
    except (requests.RequestException, ValueError, TypeError):
        LOGGER.warning("Could not load OpenRouter keys for label synchronization")
        return {"ok": False, "error": "openrouter_unavailable"}
    updated = 0
    for row in rows:
        desired = f"Noema · #{int(row['user_number']):03d}"
        key_hash = row["key_hash"]
        if remote.get(key_hash) == desired:
            continue
        try:
            response = requests.patch(f"{OPENROUTER_KEYS_URL}/{key_hash}", headers=headers,
                                      json={"name": desired}, timeout=30)
            if response.ok:
                updated += 1
            else:
                LOGGER.warning("Could not rename OpenRouter key %s: HTTP %s", key_hash[:8], response.status_code)
        except requests.RequestException:
            LOGGER.warning("Could not rename an OpenRouter key")
    return {"ok": True, "created": created, "updated": updated}


def api_key_for_chat(chat_id):
    managed = provision_managed_api_key(chat_id)
    if managed:
        return managed, "managed"
    personal = user_api_key(chat_id)
    return (personal or OR_KEY), ("personal" if personal else "shared")


def has_personal_api_key(chat_id):
    """A dedicated managed key counts as private billing for Vision routing."""
    return bool(managed_api_key(chat_id) or user_api_key(chat_id))


def shared_vision_model():
    with conn() as c:
        row = c.execute("SELECT setting_value FROM app_settings WHERE setting_key='shared_vision_model'").fetchone()
    return (row["setting_value"] if row else "") or VISION_MODEL


def set_shared_vision_model(model):
    with conn() as c:
        c.execute("INSERT INTO app_settings(setting_key,setting_value,updated_at) VALUES('shared_vision_model',?,?) "
                  "ON CONFLICT(setting_key) DO UPDATE SET setting_value=excluded.setting_value,updated_at=excluded.updated_at",
                  (model, datetime.now(timezone.utc).isoformat()))


def vision_models_for(chat_id):
    """Resolve Vision independently from chat models and key ownership."""
    if not has_personal_api_key(chat_id):
        primary = shared_vision_model()
        return [primary] + [m for m in VISION_FALLBACK_MODELS if m != primary]
    primary = model_router().resolve(chat_id, "vision")
    return [primary] + [m for m in VISION_FALLBACK_MODELS if m != primary]


def record_usage(chat_id, source, model, payload):
    usage = (payload or {}).get("usage") or {}
    input_tokens = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
    output_tokens = int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
    cost = float(usage.get("cost") or usage.get("total_cost") or 0)
    with conn() as c:
        c.execute("INSERT INTO usage_events(chat_id,source,model,input_tokens,output_tokens,cost,created_at) VALUES(?,?,?,?,?,?,?)",
                  (chat_id, source, model, input_tokens, output_tokens, cost, datetime.now(timezone.utc).isoformat()))


def register_bot_user(chat_id, user):
    """Assign a stable, non-sensitive sequential number to every chat user."""
    if not chat_id or not user:
        return None
    username = (getattr(user, "username", "") or "").strip().lstrip("@")[:64]
    display_name = " ".join(part for part in (
        getattr(user, "first_name", "") or "", getattr(user, "last_name", "") or "") if part).strip()[:120]
    now = datetime.now(timezone.utc).isoformat()
    with conn() as c:
        c.execute("INSERT INTO bot_users(chat_id,username,display_name,first_seen_at,last_seen_at) VALUES(?,?,?,?,?) "
                  "ON CONFLICT(chat_id) DO UPDATE SET username=excluded.username,display_name=excluded.display_name,last_seen_at=excluded.last_seen_at",
                  (chat_id, username, display_name, now, now))
        row = c.execute("SELECT user_number FROM bot_users WHERE chat_id=?", (chat_id,)).fetchone()
    return row["user_number"] if row else None


def usage_summary(chat_id=None, days=30, source=None):
    since = (datetime.now(TZ) - timedelta(days=days)).date().isoformat()
    where, args = "substr(created_at,1,10)>=?", [since]
    if chat_id is not None:
        where += " AND chat_id=?"; args.append(chat_id)
    if source:
        where += " AND source=?"; args.append(source)
    with conn() as c:
        rows = c.execute(f"SELECT chat_id,source,model,SUM(input_tokens) AS input_tokens,SUM(output_tokens) AS output_tokens,SUM(cost) AS cost,COUNT(*) AS requests FROM usage_events WHERE {where} GROUP BY chat_id,source,model ORDER BY cost DESC,requests DESC", args).fetchall()
    return [dict(row) for row in rows]


def shared_usage_users(days=30):
    """One compact row per person who spent tokens from the shared key."""
    since = (datetime.now(TZ) - timedelta(days=days)).date().isoformat()
    now = datetime.now(timezone.utc).isoformat()
    with conn() as c:
        # Older events predate the user catalogue; make them browsable too.
        missing = c.execute("""SELECT DISTINCT e.chat_id FROM usage_events e
                             LEFT JOIN bot_users u ON u.chat_id=e.chat_id
                             WHERE e.source IN ('shared','managed') AND substr(e.created_at,1,10)>=? AND u.chat_id IS NULL""",
                            (since,)).fetchall()
        for row in missing:
            c.execute("INSERT OR IGNORE INTO bot_users(chat_id,username,display_name,first_seen_at,last_seen_at) VALUES(?,?,?,?,?)",
                      (row["chat_id"], "", "", now, now))
        rows = c.execute("""
            SELECT u.user_number, e.chat_id, u.username, u.display_name,
                   COUNT(*) AS requests,
                   SUM(e.input_tokens) AS input_tokens, SUM(e.output_tokens) AS output_tokens,
                   SUM(e.cost) AS cost
            FROM usage_events e
            LEFT JOIN bot_users u ON u.chat_id=e.chat_id
            WHERE e.source IN ('shared','managed') AND substr(e.created_at,1,10)>=?
            GROUP BY e.chat_id
            ORDER BY cost DESC, requests DESC, e.chat_id
        """, (since,)).fetchall()
    return [dict(row) for row in rows]


def behavior_rules_for(chat_id):
    ensure_behavior_rules(chat_id)
    with conn() as c:
        return [dict(row) for row in c.execute(
            "SELECT id,rule_key,description,enabled FROM behavior_rules WHERE chat_id=? ORDER BY id", (chat_id,)).fetchall()]


def save_behavior_rule(chat_id, description=""):
    description = str(description or "").strip()[:280]
    if not description:
        return {"ok": False, "tool": "save_behavior_rule", "error": "empty_rule"}
    key = "custom:" + re.sub(r"\W+", "_", description.lower())[:120]
    with conn() as c:
        c.execute("INSERT INTO behavior_rules(chat_id,rule_key,description,enabled) VALUES(?,?,?,1) "
                  "ON CONFLICT(chat_id,rule_key) DO UPDATE SET description=excluded.description,enabled=1",
                  (chat_id, key, description))
    return {"ok": True, "tool": "save_behavior_rule", "description": description}


def get_behavior_rules(chat_id):
    return {"ok": True, "tool": "get_behavior_rules", "rules": behavior_rules_for(chat_id)}


def update_behavior_rule(chat_id, rule_id, description=""):
    description = str(description or "").strip()[:280]
    if not description:
        return {"ok": False, "tool": "update_behavior_rule", "error": "empty_rule"}
    with conn() as c:
        row = c.execute("SELECT rule_key FROM behavior_rules WHERE id=? AND chat_id=?", (int(rule_id), chat_id)).fetchone()
        if not row:
            return {"ok": False, "tool": "update_behavior_rule", "error": "not_found"}
        c.execute("UPDATE behavior_rules SET description=?, enabled=1 WHERE id=? AND chat_id=?", (description, int(rule_id), chat_id))
    return {"ok": True, "tool": "update_behavior_rule", "id": int(rule_id), "description": description}


def delete_behavior_rule(chat_id, rule_id=None, all=False):
    with conn() as c:
        if all:
            # Default rules are kept as disabled rows, so ensure_behavior_rules
            # will respect the user's choice instead of silently restoring them.
            c.execute("UPDATE behavior_rules SET enabled=0 WHERE chat_id=?", (chat_id,))
            return {"ok": True, "tool": "delete_behavior_rule", "deleted": c.total_changes}
        if not rule_id:
            return {"ok": False, "tool": "delete_behavior_rule", "error": "missing_rule_id"}
        row = c.execute("SELECT rule_key FROM behavior_rules WHERE id=? AND chat_id=?", (int(rule_id), chat_id)).fetchone()
        if not row:
            return {"ok": False, "tool": "delete_behavior_rule", "error": "not_found"}
        if str(row["rule_key"]).startswith("custom:"):
            cur = c.execute("DELETE FROM behavior_rules WHERE id=? AND chat_id=?", (int(rule_id), chat_id))
        else:
            cur = c.execute("UPDATE behavior_rules SET enabled=0 WHERE id=? AND chat_id=?", (int(rule_id), chat_id))
    return {"ok": True, "tool": "delete_behavior_rule", "deleted": cur.rowcount}



def save_reminder(chat_id, text, remind_at):

    dt = datetime.fromisoformat(remind_at)
    chat_tz = timezone_for(chat_id)

    if dt.tzinfo is None:

        dt = dt.replace(tzinfo=chat_tz)

    dt = dt.astimezone(chat_tz)

    with conn() as c:

        cur = c.execute("INSERT INTO reminders(chat_id,text,remind_at_utc,sent,acknowledged,followup_count,next_followup_at) VALUES(?,?,?,0,0,0,'')",

                        (chat_id,text,dt.astimezone(timezone.utc).isoformat()))

    return {"ok":True,"tool":"set_reminder","id":cur.lastrowid,"text":text,"local_time":dt.strftime("%d.%m.%Y %H:%M")}


def update_reminder(chat_id, reminder_id, text="", remind_at=""):
    text = str(text or "").strip()[:1000]
    if not text or not remind_at:
        return {"ok": False, "tool": "update_reminder", "error": "invalid_reminder"}
    try:
        dt = datetime.fromisoformat(str(remind_at))
    except ValueError:
        return {"ok": False, "tool": "update_reminder", "error": "invalid_time"}
    chat_tz = timezone_for(chat_id)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=chat_tz)
    utc_time = dt.astimezone(timezone.utc).isoformat()
    with conn() as c:
        cur = c.execute("UPDATE reminders SET text=?,remind_at_utc=?,sent=0,acknowledged=0,next_followup_at='' WHERE id=? AND chat_id=?",
                        (text, utc_time, int(reminder_id), chat_id))
    return {"ok": bool(cur.rowcount), "tool": "update_reminder", "updated": cur.rowcount}



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


def update_task(chat_id, task_id, text="", due_date="", priority="normal"):
    text = str(text or "").strip()[:1000]
    if not text:
        return {"ok": False, "tool": "update_task", "error": "empty_task"}
    with conn() as c:
        cur = c.execute("UPDATE tasks SET text=?,due_date=?,priority=? WHERE id=? AND chat_id=?",
                        (text, str(due_date or "")[:32], str(priority or "normal")[:32], int(task_id), chat_id))
    return {"ok": bool(cur.rowcount), "tool": "update_task", "updated": cur.rowcount}



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



def add_transaction(chat_id, amount, description, kind="expense", currency="RUB", category="прочее", merchant="", spent_at=""):

    spent_at=normalize_spent_at(spent_at)
    kind = "income" if kind == "income" else "expense"

    with conn() as c:

        cur=c.execute("""INSERT INTO expenses(chat_id,amount,currency,category,description,merchant,spent_at,created_at,kind)

        VALUES(?,?,?,?,?,?,?,?,?)""",

        (chat_id,float(amount),currency or "RUB",category or "прочее",description or ("пополнение" if kind == "income" else "расход"),merchant or "",spent_at,datetime.now(timezone.utc).isoformat(),kind))

    return {"ok":True,"tool":"add_income" if kind == "income" else "add_expense","id":cur.lastrowid,"amount":float(amount),"currency":currency or "RUB",

            "category":category or "прочее","description":description or ("пополнение" if kind == "income" else "расход"),"merchant":merchant or "","spent_at":spent_at,"kind":kind}


def add_expense(chat_id, amount, description, currency="RUB", category="прочее", merchant="", spent_at=""):
    return add_transaction(chat_id, amount, description, "expense", currency, category, merchant, spent_at)


def add_income(chat_id, amount, description, currency="RUB", category="пополнение", merchant="", spent_at=""):
    return add_transaction(chat_id, amount, description, "income", currency, category, merchant, spent_at)



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



def get_plan_for_date(chat_id, day):
    """Return a calendar day without silently completing anything overdue."""
    selected = datetime.fromisoformat(day).date()
    chat_tz = timezone_for(chat_id)
    today = datetime.now(chat_tz).date()
    with conn() as c:
        # Tasks without a date are actionable today, but don't clutter every
        # calendar day.  A passed task remains open until the user decides it.
        where = "substr(due_date,1,10)=?" if selected != today else "(due_date='' OR substr(due_date,1,10)<=?)"
        tasks = [dict(r) for r in c.execute(
            f"SELECT id,text,due_date,priority,status FROM tasks WHERE chat_id=? AND {where} ORDER BY due_date,id",
            (chat_id, day)).fetchall()]
        rem = [dict(r) for r in c.execute(
            "SELECT id,text,remind_at_utc,acknowledged FROM reminders WHERE chat_id=? ORDER BY remind_at_utc",
            (chat_id,)).fetchall()]
    reminders = []
    for r in rem:
        dt = datetime.fromisoformat(r["remind_at_utc"]).astimezone(chat_tz)
        if dt.date() == selected:
            reminders.append({"id": r["id"], "text": r["text"], "time": dt.strftime("%H:%M"),
                              "acknowledged": r["acknowledged"]})
    return {"ok": True, "tool": "get_today_plan", "date": day, "tasks": tasks, "reminders": reminders}


def get_today_plan(chat_id):
    return get_plan_for_date(chat_id, datetime.now(timezone_for(chat_id)).date().isoformat())


def set_task_status(chat_id, task_id, status):
    if status not in ("open", "done", "failed"):
        return {"ok": False, "error": "invalid_status"}
    with conn() as c:
        cur = c.execute("UPDATE tasks SET status=?, completed_at=? WHERE id=? AND chat_id=?",
                        (status, "" if status == "open" else datetime.now(timezone.utc).isoformat(), task_id, chat_id))
    return {"ok": True, "updated": cur.rowcount}


def toggle_task_status(chat_id, task_id):
    with conn() as c:
        row = c.execute("SELECT status FROM tasks WHERE id=? AND chat_id=?", (task_id, chat_id)).fetchone()
    if not row:
        return {"ok": False, "error": "not_found"}
    return set_task_status(chat_id, task_id, "open" if row["status"] == "done" else "done")



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


def update_note(chat_id, note_id, text="", title=""):
    text = str(text or "").strip()[:12000]
    if not text:
        return {"ok": False, "tool": "update_note", "error": "empty_note"}
    with conn() as c:
        cur = c.execute("UPDATE notes SET title=?,text=? WHERE id=? AND chat_id=?",
                        (str(title or "").strip()[:240], text, int(note_id), chat_id))
    return {"ok": bool(cur.rowcount), "tool": "update_note", "updated": cur.rowcount}


def update_expense(chat_id, expense_id, amount, description="", category="прочее", currency="RUB", spent_at=""):
    try:
        amount = float(amount)
    except (TypeError, ValueError):
        return {"ok": False, "tool": "update_expense", "error": "invalid_amount"}
    if amount <= 0:
        return {"ok": False, "tool": "update_expense", "error": "invalid_amount"}
    with conn() as c:
        row = c.execute("SELECT spent_at FROM expenses WHERE id=? AND chat_id=?", (int(expense_id), chat_id)).fetchone()
        if not row:
            return {"ok": False, "tool": "update_expense", "error": "not_found"}
        cur = c.execute("UPDATE expenses SET amount=?,description=?,category=?,currency=?,spent_at=? WHERE id=? AND chat_id=?",
                        (amount, str(description or "").strip()[:500], str(category or "прочее").strip()[:120],
                         str(currency or "RUB").strip()[:8], normalize_spent_at(str(spent_at)) if spent_at else row["spent_at"], int(expense_id), chat_id))
    return {"ok": bool(cur.rowcount), "tool": "update_expense", "updated": cur.rowcount}


def update_person(chat_id, person_id, name="", relationship="", birthday="", age=None,
                  home_city=None, current_location=None, projects="", notes=""):
    name = str(name or "").strip()[:160]
    if not name:
        return {"ok": False, "tool": "update_person", "error": "empty_name"}
    has_age = age is not None
    try:
        age = int(age) if age is not None and str(age).strip() else None
    except (TypeError, ValueError):
        return {"ok": False, "tool": "update_person", "error": "invalid_age"}
    with conn() as c:
        existing = c.execute("SELECT age,home_city,current_location FROM people WHERE id=? AND chat_id=?",
                             (int(person_id), chat_id)).fetchone()
        if not existing:
            return {"ok": False, "tool": "update_person", "error": "not_found"}
        cur = c.execute("UPDATE people SET name=?,relationship=?,birthday=?,age=?,home_city=?,current_location=?,projects=?,notes=?,updated_at=? WHERE id=? AND chat_id=?",
                        (name, str(relationship or "")[:160], str(birthday or "")[:32],
                         age if has_age else existing["age"],
                         str(existing["home_city"] if home_city is None else home_city or "")[:160],
                         str(existing["current_location"] if current_location is None else current_location or "")[:160],
                         str(projects or "")[:1000], str(notes or "")[:3000],
                         datetime.now(timezone.utc).isoformat(), int(person_id), chat_id))
    return {"ok": bool(cur.rowcount), "tool": "update_person", "updated": cur.rowcount}


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

        "set_timezone":set_user_timezone,

        "set_reminder":save_reminder,

        "save_behavior_rule":save_behavior_rule,

        "get_behavior_rules":get_behavior_rules,

        "update_behavior_rule":update_behavior_rule,

        "delete_behavior_rule":delete_behavior_rule,

        "save_note":save_note,

        "update_note":update_note,

        "add_task":add_task,

        "update_task":update_task,

        "update_reminder":update_reminder,

        "update_expense":update_expense,

        "update_person":update_person,

        "person_upsert":person_upsert,

        "person_interaction":person_interaction,

        "add_expense":add_expense,

        "add_income":add_income,

        "update_last_expense":update_last_expense,

        "get_expenses":get_expenses,

        "get_today_plan":get_today_plan,

        "set_briefing_preferences":set_briefing_preferences,

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

        "get_weather":get_weather,

        "knowledge_search":knowledge_search_tool,

        "knowledge_get":knowledge_get_tool,

        "knowledge_files":knowledge_files_tool

    }

    if name not in funcs: return {"ok":False,"tool":name,"error":"unknown_tool"}

    kwargs={k:v for k,v in args.items() if k!="chat_id"}
    started = time.perf_counter()
    try:
        return funcs[name](chat_id,**kwargs)
    finally:
        elapsed = (time.perf_counter() - started) * 1000
        record_runtime_metric("tool_execution_ms", elapsed)
        if name in {"knowledge_search", "knowledge_get", "knowledge_files"}:
            record_runtime_metric("memory_retrieval_ms", elapsed)

# ---------- LIVE DATA ----------

# ---------- LIVE DATA ----------

WEATHER_CODES={0:"ясно",1:"в основном ясно",2:"переменная облачность",3:"пасмурно",45:"туман",51:"слабая морось",

61:"слабый дождь",63:"дождь",65:"сильный дождь",71:"слабый снег",73:"снег",80:"ливни",95:"гроза"}

def geocode_city(city):

    r=requests.get("https://geocoding-api.open-meteo.com/v1/search",

                   params={"name":str(city or DEFAULT_CITY).strip(),"count":1,"language":"ru","format":"json"},timeout=20)

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



def get_weather(chat_id, city=""):
    """LLM-facing weather tool: city interpretation belongs to the model, not an alias list."""
    return get_weather_live(str(city or DEFAULT_CITY).strip())


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

def system_prompt(chat_id):

    chat_tz = timezone_for(chat_id)
    now=datetime.now(chat_tz)
    active_rules = [rule["description"] for rule in behavior_rules_for(chat_id) if rule.get("enabled")]
    rules_text = "; ".join(active_rules[:12]) or "нет"

    return (

        "Ты Noema (Noema Model v1) — персональный помощник. "

        "Ты работаешь с tools: ты можешь сохранять заметки, задачи, напоминания, расходы, "

        "данные о людях и читать их обратно. Это твоя память — используй её. "

        "ВСЕГДА используй соответствующие tools, когда нужно сохранить или прочитать данные. "

        "Если пользователь просит что-то запомнить или сохранить — сразу вызывай save_note. "

        "Явную трату сохраняй сразу через add_expense, а поступление, зарплату или пополнение — через add_income. "

        "Если следующим сообщением уточняют предыдущую трату — используй update_last_expense. "

        "Людей сохраняй структурированно через person_upsert: отношение, возраст, ДР, город, текущее место, проекты, заметки. "

        "Если в одном сообщении человек + созвон/задача/напоминание — вызови несколько tools. "

        "Для чтения сохранённых данных используй get_notes, get_people, get_expenses, get_today_plan — не выдумывай. "

        "Личные заметки принадлежат пользователю. Не сохраняй в них внутренние правила поведения бота, стиль общения или служебные напоминания. Когда пользователь явно задаёт такое правило, сохраняй его через save_behavior_rule: оно отображается отдельно в настройках «Правила». "

        "Правила можно показать через get_behavior_rules, изменить через update_behavior_rule и удалить через delete_behavior_rule. "

        "Никогда не создавай заметку, задачу, напоминание или правило только из короткого ответа «да», «давай», «ок», «продолжай» или другой реплики-подтверждения. Это продолжение разговора, а не команда сохранения. Если до этого предложила рассказ, объяснить или показать что-то — выполни обещанное, а не сохраняй служебную запись. "

        "Если пользователь говорит, что находится, переехал или путешествует в другой стране/часовом поясе — используй set_timezone с подходящим IANA ID (например Китай — Asia/Shanghai). Если пользователь явно просит изменить город, темы новостей, время или включение ежедневного брифинга — используй set_briefing_preferences. Состав и формат самого брифинга не меняй самовольно. "

        "Для актуальной погоды обязательно вызывай get_weather. Понимай город по смыслу и контексту: сокращения, разговорные названия и падежи; если город не указан, передай пустой city, чтобы использовать город пользователя. Если город невозможно понять однозначно — задай короткий уточняющий вопрос, не угадывай. Актуальные новости и курс обрабатывает внешний live-router — не выдумывай их самостоятельно. "

        "Когда пользователь просит найти, проверить, изучить, сравнить, подобрать или исследовать что-то во внешнем интернете, вызывай internet_search. Это относится не только к товарам: ищи статьи, сервисы, факты, рекомендации и ссылки. Сначала различай внешний интернет и сохранённую память пользователя. "

        "У тебя есть сохранённая память пользователя (knowledge): фото, скриншоты, сайты, URL, заметки, чек, сущности, проекты. "

        "Если пользователь спрашивает о ранее сохранённом — например «где я храню базу», «что я сохранял для Noema», «какой сайт я кидал», «покажи/найди Тошку», «что ты знаешь про ...», «что сохранял вчера», «покажи тот фото/скрин» — СНАЧАЛА сделай knowledge_search с подходящими query/project/entity. Вопросы «есть ли у меня питомцы/домашние животные» тоже ищи широко по питомцам, животным и их именам, а не только по точной фразе. Не говори «у меня нет доступа», не написав в search. "

        "Если нужен конкретный элемент из results — можно knowledge_get по id или knowledge_files для файлов. "

        "Если пользователь просит ПОКАЗАТЬ/ДАТЬ/отправить фото или скрин — после поиска вызови send_stored_image (id найденного knowledge или query) — бот реально отправит файл. "

        "Если knowledge_search ничего не вернул — честно скажи «Я не нашла сохранённых данных по этому запросу», не выдумывай. "

        "Никогда не заявляй, что что-то сохранено, если tool не вернул ok=true. "

        "Не раскрывай внутренние модели, OpenRouter или провайдера. "

        "Отвечай коротко, естественно и персонально. "

        f"Активные правила пользователя: {rules_text}. "

        f"Сейчас {now.isoformat()}, timezone {chat_tz.key}."

    )



def request_chat(chat_id, model, messages, tools=None, tool_choice="auto"):

    payload={"model":model,"messages":messages,"temperature":0.25,"max_tokens":int(os.getenv("CHAT_MAX_TOKENS", "1800"))}

    if tools: payload["tools"]=tools; payload["tool_choice"]=tool_choice
    if provider := provider_preferences_for(model): payload["provider"] = provider

    key, _ = api_key_for_chat(chat_id)
    return requests.post(CHAT_URL,headers={"Authorization":f"Bearer {key}","Content-Type":"application/json"},

                         json=payload,timeout=180)


def request_chat_stream(chat_id, model, messages, tools=None, tool_choice="auto"):
    payload = {"model": model, "messages": messages, "temperature": 0.25,
               "max_tokens": int(os.getenv("CHAT_MAX_TOKENS", "1800")), "stream": True,
               "stream_options": {"include_usage": True}}
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = tool_choice
    if provider := provider_preferences_for(model):
        payload["provider"] = provider
    key, _ = api_key_for_chat(chat_id)
    return requests.post(CHAT_URL, headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                         json=payload, timeout=(20, 180), stream=True)


def stream_agent_response(chat_id, text, cancel_event=None):
    """One streaming core for text and voice; yields display-safe runtime events."""
    started = time.perf_counter()
    cancel_event = cancel_event or threading.Event()
    if cancel_event.is_set():
        yield {"type": "cancelled"}
        return
    live = direct_live_request(text)
    if live is not None:
        add_message(chat_id, "user", text); add_message(chat_id, "assistant", live)
        yield {"type": "delta", "text": live}
        yield {"type": "done", "text": live}
        return
    context_started = time.perf_counter()
    messages = [{"role": "system", "content": system_prompt(chat_id)}] + conversation_context(chat_id) + [{"role": "user", "content": text}]
    tools = ToolPackResolver().resolve(TOOLS, text)
    selected = model_router().resolve(chat_id, "chat")
    models = list(dict.fromkeys([selected["primary"], selected["fallback"], *FALLBACK_MODELS, *AVAILABLE_MODELS]))
    record_runtime_metric("context_build_ms", (time.perf_counter() - context_started) * 1000)
    writes, final_text = [], ""
    for round_index in range(5):
        message = None
        last_error = None
        for model in [m for m in models if m]:
            if cancel_event.is_set():
                yield {"type": "cancelled"}
                return
            request_started = time.perf_counter()
            response = request_chat_stream(chat_id, model, messages, tools, "required" if round_index == 0 and asks_external_web(text) else "auto")
            if not response.ok:
                last_error = response.status_code
                response.close()
                record_runtime_metric("llm_total_ms", (time.perf_counter() - request_started) * 1000)
                continue
            accumulator = StreamAccumulator()
            first_delta = False
            with ACTIVE_STREAM_RESPONSES_LOCK:
                ACTIVE_STREAM_RESPONSES[cancel_event] = response
            try:
                for payload in iter_sse_json(response.iter_lines()):
                    if cancel_event.is_set():
                        response.close()
                        yield {"type": "cancelled"}
                        return
                    for delta in accumulator.add(payload):
                        if not first_delta:
                            first_delta = True
                            record_runtime_metric("llm_ttft_ms", (time.perf_counter() - request_started) * 1000)
                        final_text += delta
                        yield {"type": "delta", "text": delta}
                message = accumulator.message()
                if accumulator.usage:
                    record_usage(chat_id, api_key_for_chat(chat_id)[1], model, {"usage": accumulator.usage})
                break
            except requests.RequestException:
                if cancel_event.is_set():
                    yield {"type": "cancelled"}
                    return
                raise
            finally:
                with ACTIVE_STREAM_RESPONSES_LOCK:
                    if ACTIVE_STREAM_RESPONSES.get(cancel_event) is response:
                        ACTIVE_STREAM_RESPONSES.pop(cancel_event, None)
                response.close()
                record_runtime_metric("llm_total_ms", (time.perf_counter() - request_started) * 1000)
        if message is None:
            raise RuntimeError("MODEL_BUSY" if last_error == 429 else "MODEL_ERROR")
        calls = message.get("tool_calls") or []
        if not calls:
            answer = (message.get("content") or "").strip() or write_confirmation(writes)
            add_message(chat_id, "user", text); add_message(chat_id, "assistant", answer)
            yield {"type": "done", "text": answer, "elapsed_ms": round((time.perf_counter() - started) * 1000)}
            return
        messages.append(message)
        for call in calls:
            if cancel_event.is_set():
                yield {"type": "cancelled"}
                return
            name = call.get("function", {}).get("name", "")
            raw = call.get("function", {}).get("arguments", "{}")
            try:
                args = json.loads(raw) if isinstance(raw, str) else raw
                result = execute_tool(chat_id, name, args or {})
            except Exception as exc:
                result = {"ok": False, "tool": name, "error": str(exc)}
            if name in WRITE_TOOLS:
                writes.append(result)
            messages.append({"role": "tool", "tool_call_id": call.get("id"), "content": json.dumps(result, ensure_ascii=False)})
            yield {"type": "tool", "name": name, "ok": bool(result.get("ok"))}
            if cancel_event.is_set():
                yield {"type": "cancelled"}
                return
    answer = write_confirmation(writes) if writes else "Не удалось завершить действие."
    add_message(chat_id, "user", text); add_message(chat_id, "assistant", answer)
    yield {"type": "done", "text": answer}


def mint_mistral_realtime_session():
    """Mint a model-scoped, short-lived rt_* token without exposing the API key."""
    if not MISTRAL_API_KEY:
        raise RuntimeError("MISTRAL_NOT_CONFIGURED")
    response = requests.post(
        MISTRAL_CLIENT_SESSIONS_URL,
        headers={"Authorization": f"Bearer {MISTRAL_API_KEY}", "Content-Type": "application/json"},
        json={"purpose": "realtime", "model": MISTRAL_REALTIME_MODEL},
        timeout=20,
    )
    if not response.ok:
        raise RuntimeError("MISTRAL_SESSION_ERROR")
    payload = response.json()
    secret = payload.get("client_secret") or {}
    token = secret.get("value")
    if not isinstance(token, str) or not token.startswith("rt_"):
        raise RuntimeError("MISTRAL_SESSION_ERROR")
    return {
        "token": token,
        "expires_at": secret.get("expires_at") or payload.get("expires_at"),
        "model": MISTRAL_REALTIME_MODEL,
        "url": "wss://api.mistral.ai/v1/audio/transcriptions/realtime",
    }


def _new_draft_id():
    return secrets.randbelow(2_147_483_646) + 1


def register_active_draft(chat_id, draft_id, cancel_event):
    with ACTIVE_DRAFTS_LOCK:
        previous = ACTIVE_DRAFTS.get(chat_id)
        if previous:
            cancel_stream(previous[1])
        ACTIVE_DRAFTS[chat_id] = (draft_id, cancel_event)


def unregister_active_draft(chat_id, draft_id):
    with ACTIVE_DRAFTS_LOCK:
        current = ACTIVE_DRAFTS.get(chat_id)
        if current and current[0] == draft_id:
            ACTIVE_DRAFTS.pop(chat_id, None)


def cancel_active_draft(chat_id, draft_id=None):
    with ACTIVE_DRAFTS_LOCK:
        current = ACTIVE_DRAFTS.get(chat_id)
        if not current or (draft_id is not None and current[0] != draft_id):
            return False
        cancel_stream(current[1])
        return True


def cancel_stream(cancel_event):
    cancel_event.set()
    with ACTIVE_STREAM_RESPONSES_LOCK:
        response = ACTIVE_STREAM_RESPONSES.get(cancel_event)
    if response is not None:
        with contextlib.suppress(Exception):
            response.close()


async def stopped_generation_handler(update, context):
    stopped = (getattr(update, "api_kwargs", None) or {}).get("stopped_message_generation")
    if not isinstance(stopped, dict):
        return
    chat = stopped.get("chat") or {}
    chat_id, draft_id = chat.get("id"), stopped.get("draft_id")
    if isinstance(chat_id, int):
        cancel_active_draft(chat_id, draft_id if isinstance(draft_id, int) else None)


async def stream_answer_to_telegram(update, context, text):
    """Stream one ephemeral draft, then persist exactly one formatted final answer."""
    chat_id = update.effective_chat.id
    draft_id, cancelled = _new_draft_id(), threading.Event()
    register_active_draft(chat_id, draft_id, cancelled)
    queue, loop = asyncio.Queue(), asyncio.get_running_loop()

    def produce():
        try:
            for event in stream_agent_response(chat_id, text, cancelled):
                loop.call_soon_threadsafe(queue.put_nowait, event)
        except Exception as exc:
            loop.call_soon_threadsafe(queue.put_nowait, exc)
        finally:
            loop.call_soon_threadsafe(queue.put_nowait, None)

    threading.Thread(target=produce, name=f"telegram-stream-{chat_id}", daemon=True).start()
    throttle = AdaptiveDraftThrottle(TELEGRAM_DRAFT_MIN_INTERVAL, TELEGRAM_DRAFT_MAX_INTERVAL, TELEGRAM_DRAFT_MIN_CHARS)
    accumulated, final = "", ""
    draft_available = True
    try:
        try:
            await context.bot.send_message_draft(chat_id, draft_id, "", api_kwargs={"can_stop": True, "keep_on_stop": False})
        except Exception:
            draft_available = False
        next_watchdog_at = time.monotonic() + 8
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=max(.1, next_watchdog_at - time.monotonic()))
            except asyncio.TimeoutError:
                if draft_available:
                    with contextlib.suppress(Exception):
                        await context.bot.send_message_draft(
                            chat_id, draft_id, "Готовлю ответ…",
                            api_kwargs={"can_stop": True, "keep_on_stop": False},
                        )
                next_watchdog_at = time.monotonic() + 8
                continue
            if event is None:
                break
            if isinstance(event, Exception):
                raise event
            kind = event.get("type")
            if kind == "delta":
                accumulated += event.get("text", "")
                if draft_available and throttle.should_send(accumulated):
                    try:
                        await context.bot.send_message_draft(
                            chat_id, draft_id, accumulated[-4096:],
                            api_kwargs={"can_stop": True, "keep_on_stop": False},
                        )
                    except Exception:
                        draft_available = False
            elif kind == "done":
                final = event.get("text") or accumulated
            elif kind == "tool" and draft_available:
                # ``tool`` is emitted only after the canonical core actually
                # executed it, so this status is truthful.
                with contextlib.suppress(Exception):
                    await context.bot.send_message_draft(
                        chat_id, draft_id, "Действие выполнено, готовлю ответ…",
                        api_kwargs={"can_stop": True, "keep_on_stop": False},
                    )
            elif kind == "cancelled":
                cancelled.set()
        if cancelled.is_set():
            return False
        final = final or accumulated
        if draft_available and final and throttle.should_send(final, force=True):
            with contextlib.suppress(Exception):
                await context.bot.send_message_draft(chat_id, draft_id, final[-4096:], api_kwargs={"can_stop": False})
        await send_answer(update, final, context=context, voice_in=False, force_voice=wants_voice(text))
        return True
    finally:
        cancelled.set()
        unregister_active_draft(chat_id, draft_id)



def call_or(chat_id, messages,tools=None,tool_choice="auto"):

    selected=model_router().resolve(chat_id, "chat")
    primary, fallback = selected["primary"], selected["fallback"]
    # Explicit FALLBACK_MODELS has priority. If it is not configured, the
    # preset catalogue is still a useful automatic fallback chain.
    candidates = [fallback] + FALLBACK_MODELS + AVAILABLE_MODELS
    models = [primary] + [m for m in candidates if m and m != primary and m not in [primary]]
    models = list(dict.fromkeys(models))

    last=None

    for model in models:

        recovered_key = False

        for attempt in range(2):

            started = time.perf_counter()
            r=request_chat(chat_id, model, messages, tools, tool_choice)
            record_runtime_metric("llm_total_ms", (time.perf_counter()-started)*1000)
            print(f"LLM request chat_id={chat_id} model={model} seconds={time.perf_counter()-started:.2f} status={r.status_code}")

            if r.ok:
                data = r.json()
                record_usage(chat_id, api_key_for_chat(chat_id)[1], model, data)
                choice = data["choices"][0]
                if choice.get("finish_reason") == "length":
                    print(f"LLM truncation chat_id={chat_id} model={model}")
                return choice["message"]

            last=(r.status_code,r.text)

            if not recovered_key and recover_missing_managed_key(chat_id, r):
                recovered_key = True
                continue

            if model != models[-1]:
                print(f"LLM fallback chat_id={chat_id} from={model} status={r.status_code}")

            if r.status_code==429 or 500<=r.status_code<600:

                if attempt==0: time.sleep(1.2); continue

            break

    # If tool_choice was "required" and all models rejected it, retry with "auto"

    if tool_choice=="required":

        for model in models:

            fallback_started = time.perf_counter()
            r=request_chat(chat_id, model, messages, tools, "auto")
            record_runtime_metric("llm_total_ms", (time.perf_counter()-fallback_started)*1000)

            if r.ok:
                data = r.json()
                record_usage(chat_id, api_key_for_chat(chat_id)[1], model, data)
                return data["choices"][0]["message"]

            last=(r.status_code,r.text)

    raise RuntimeError("MODEL_BUSY" if last and last[0]==429 else "MODEL_ERROR")



def write_confirmation(results):

    parts=[]

    for r in results:

        if not r.get("ok"): continue

        n=r.get("tool")

        if n=="set_timezone": parts.append(f'Часовой пояс изменён: {r["timezone"]}.')

        elif n=="add_expense": parts.append(f'Записала расход: {r["amount"]:g} {r["currency"]} — {r["description"]}.')

        elif n=="add_income": parts.append(f'Записала поступление: {r["amount"]:g} {r["currency"]} — {r["description"]}.')

        elif n=="update_last_expense": parts.append(f'Обновила расход: {r["amount"]:g} {r["currency"]} — {r["category"]}, {r["description"]}.')

        elif n=="person_upsert": parts.append(f'Сохранила данные о {r["name"]}.')

        elif n=="person_interaction": parts.append(f'Записала взаимодействие с {r["name"]}.')

        elif n=="set_reminder": parts.append(f'Напоминание поставлено на {r["local_time"]}.')

        elif n=="add_task": parts.append(f'Задача добавлена: {r["text"]}.')

        elif n=="save_note": parts.append("Заметка сохранена.")

        elif n=="save_behavior_rule": parts.append("Правило добавлено в настройки.")

        elif n=="update_behavior_rule": parts.append("Правило обновлено.")

        elif n=="delete_behavior_rule": parts.append("Правило удалено.")

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



    context_started = time.perf_counter()
    msgs=[{"role":"system","content":system_prompt(chat_id)}]

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

    msgs+=conversation_context(chat_id)+[{"role":"user","content":text}]
    record_runtime_metric("context_build_ms", (time.perf_counter()-context_started)*1000)

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



def request_vision(chat_id, model, messages):

    payload={"model":model,"messages":messages,"temperature":0.3,"max_tokens":2000}

    key, _ = api_key_for_chat(chat_id)
    return requests.post(CHAT_URL,headers={"Authorization":f"Bearer {key}","Content-Type":"application/json"},

                         json=payload,timeout=180)



def describe_image(chat_id, image_path,mime="image/jpeg",caption=""):

    b64=base64.b64encode(Path(image_path).read_bytes()).decode()

    parts=[vision_prompt()]

    if caption: parts.insert(0,f"Подпись пользователя: {caption}\n")

    parts.append("Опиши детально, что изображено на изображении.")

    content=[{"type":"text","text":" ".join(parts)},

             {"type":"image_url","image_url":{"url":f"data:{mime};base64,{b64}"}}]

    msgs=[{"role":"user","content":content}]

    models=vision_models_for(chat_id)

    last=None

    for model in models:

        for attempt in range(2):

            r=request_vision(chat_id, model, msgs)

            if r.ok:

                data = r.json()
                record_usage(chat_id, api_key_for_chat(chat_id)[1], model, data)
                msg=data["choices"][0]["message"]

                content=msg.get("content","") or ""

                return content.strip()

            last=(r.status_code,r.text)
            if recover_missing_managed_key(chat_id, r):
                r=request_chat(chat_id, model, messages, tools, "auto")
                if r.ok:
                    data = r.json()
                    record_usage(chat_id, api_key_for_chat(chat_id)[1], model, data)
                    return data["choices"][0]["message"]
                last=(r.status_code,r.text)

            if recover_missing_managed_key(chat_id, r) and attempt == 0:
                continue

            if r.status_code==429 or 500<=r.status_code<600:

                if attempt==0: time.sleep(1.2); continue

            break

    raise RuntimeError("MODEL_ERROR")



# ---------- VOICE ----------

def _transcribe_unmeasured(chat_id, path):

    b64=base64.b64encode(Path(path).read_bytes()).decode()
    suffix = Path(path).suffix.lower().lstrip(".")
    # Mini App records WebM/MP4 while Telegram voice notes are OGG. Never mislabel audio to STT.
    audio_format = {"ogg": "ogg", "oga": "ogg", "webm": "webm", "mp4": "mp4", "m4a": "m4a", "wav": "wav"}.get(suffix, "webm")

    # A Noema-managed key is a complete private balance: speech-to-text,
    # chat and Vision all belong to the same Telegram user.
    source = ""
    r = None
    for attempt in range(2):
        key, source = api_key_for_chat(chat_id)
        r=requests.post(STT_URL,headers={"Authorization":f"Bearer {key}","Content-Type":"application/json"},
                        json={"model":STT_MODEL,"input_audio":{"data":b64,"format":audio_format},"language":"ru"},timeout=180)
        if r.ok or attempt or not recover_missing_managed_key(chat_id, r):
            break

    if not r.ok: raise RuntimeError("STT_BUSY" if r.status_code==429 else "STT_ERROR")

    data = r.json()
    record_usage(chat_id, source, STT_MODEL, data)
    text=data.get("text","").strip()

    if not text: raise RuntimeError("STT_EMPTY")

    return text


def transcribe(chat_id, path):
    """Keep STT behavior unchanged while recording a numeric end-to-end duration."""
    started = time.perf_counter()
    try:
        return _transcribe_unmeasured(chat_id, path)
    finally:
        record_runtime_metric("stt_final_ms", (time.perf_counter() - started) * 1000)



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



async def send_answer(update,answer,context=None,voice_in=False,force_voice=False):

    mode=get_mode(update.effective_chat.id)

    eff="voice_and_text" if force_voice else (("voice_and_text" if voice_in else "text") if mode=="auto" else mode)

    if eff in ("text","voice_and_text"):
        total_emoji_limit = reply_emoji_limit(len(str(answer or "")))
        for index, chunk in enumerate(TelegramRenderer.chunks(answer)):
            # The reply keyboard belongs to a lasting answer, never to the
            # temporary activity card which is deleted after processing.
            kwargs = {"parse_mode": TelegramRenderer.parse_mode}
            # Reserve one animation for the leading marker on the first part.
            available = max(0, total_emoji_limit - (1 if index == 0 else 0))
            chunk, used = animate_configured_emojis(chunk, available)
            total_emoji_limit -= used
            if index == 0:
                kwargs["reply_markup"] = main_keyboard()
                chunk = reply_emoji_prefix(update.effective_chat.id) + chunk
                total_emoji_limit -= 1
            sent = await update.effective_message.reply_text(chunk, **kwargs)
            if context and is_ephemeral_confirmation(answer):
                schedule_ephemeral_delete(context, sent)

    if eff in ("voice","voice_and_text"):

        p=await make_voice(answer)

        try:

            with p.open("rb") as f: await update.effective_message.reply_voice(voice=f)

        finally: p.unlink(missing_ok=True)



async def safe_error(update,e):

    code=str(e)
    LOGGER.exception("Request failed for chat %s: %s", update.effective_chat.id if update.effective_chat else "?", code, exc_info=e)

    msg={"MODEL_BUSY":"Сейчас модель перегружена. Повтори сообщение через минуту.",

         "MODEL_ERROR":"Не удалось получить ответ. Попробуй ещё раз.",

         "STT_BUSY":"Распознавание речи временно перегружено. Попробуй ещё раз.",

         "STT_ERROR":"Не удалось распознать голосовое.",

         "STT_EMPTY":"Не удалось распознать голосовое."}.get(code,"Не удалось выполнить запрос.")

    await update.effective_message.reply_text(msg)


def activity_labels(text):
    # A tool has not run yet when this card appears, so never infer a search
    # or retrieval merely from the wording of the request.
    return ["🧠 Готовлю ответ…", "🧠 Ответ ещё готовится…"]


async def begin_activity(message, labels):
    """One temporary, unobtrusive progress card for operations lasting seconds."""
    first = live_ui_text(labels[0])
    card = await message.reply_text(first, parse_mode="HTML" if first != labels[0] else None)
    stopped = asyncio.Event()

    async def animate():
        index = 1
        while not stopped.is_set():
            try:
                await asyncio.wait_for(stopped.wait(), timeout=2.2)
                break
            except asyncio.TimeoutError:
                try:
                    label = labels[min(index, len(labels) - 1)]
                    rendered = live_ui_text(label)
                    await card.edit_text(rendered, parse_mode="HTML" if rendered != label else None)
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
    chat_id = update.effective_chat.id
    register_bot_user(chat_id, getattr(update, "effective_user", None))
    previous_id = active_ui_message_id(chat_id)
    if previous_id:
        with contextlib.suppress(Exception):
            await context.bot.delete_message(chat_id=chat_id, message_id=previous_id)
        set_active_ui_message_id(chat_id, 0)
    # Provisioning happens in the background: /start remains instant even if
    # OpenRouter is temporarily slow.
    if OR_MANAGEMENT_KEY:
        asyncio.create_task(asyncio.to_thread(provision_managed_api_key, chat_id))
    await update.effective_message.reply_text(
        "<b>Привет, я Noema — твой личный ассистент.</b>\n\n"
        "Напиши или скажи голосом, что нужно: напомнить о деле, сохранить мысль, "
        "записать расход или найти ответ. Фото и файлы тоже можно присылать сюда.\n\n"
        "Я помогу держать важное под рукой. Начнём?",
        reply_markup=main_keyboard(), parse_mode="HTML")
    initialize_briefing(chat_id)


def telemetry_command_allowed(update):
    """Commands expose aggregates only, and only to the already configured admins."""
    chat = getattr(update, "effective_chat", None)
    return bool(chat and chat.id in ADMIN_CHAT_IDS)


def telemetry_counts_text(exported):
    return "\n".join(
        f"<b>{heading}</b>\n" + "\n".join(
            f"<code>{name}</code>: {exported['metrics'][name]['count']}"
            for name in exported["groups"][group]
        )
        for heading, group in (("Latency", "latency"), ("Voice robustness", "voice_robustness"))
    )


def telemetry_report_text(exported):
    lines = [
        "<b>Telemetry report</b>",
        f"Collection: <b>{'enabled' if exported['enabled'] else 'disabled'}</b>",
        f"Started: <code>{exported['started_at'] or 'not reset in this process'}</code>",
        "",
    ]
    for heading, group in (("Latency", "latency"), ("Voice robustness", "voice_robustness")):
        lines.extend((f"<b>{heading}</b>",))
        for name in exported["groups"][group]:
            metric = exported["metrics"][name]
            lines.append(
                f"<code>{name}</code> — count {metric['count']} · avg {metric['avg']} ms · "
                f"p50 {metric['p50']} ms · p95 {metric['p95']} ms · max {metric['max']} ms"
            )
    return "\n".join(lines)


async def telemetry_reset_command(update, context):
    if not telemetry_command_allowed(update):
        return
    started_at = reset_runtime_metric_series()
    await update.effective_message.reply_text(
        "<b>Telemetry reset</b>\n"
        f"Collection: <b>{'enabled' if TELEMETRY_ENABLED else 'disabled'}</b>\n"
        f"Started: <code>{started_at}</code>\n\n"
        "Only in-memory numeric latency samples were cleared.",
        parse_mode="HTML",
    )


async def telemetry_status_command(update, context):
    if not telemetry_command_allowed(update):
        return
    exported = runtime_metric_export()
    await update.effective_message.reply_text(
        "<b>Telemetry status</b>\n"
        f"Collection: <b>{'enabled' if exported['enabled'] else 'disabled'}</b>\n"
        f"Started: <code>{exported['started_at'] or 'not reset in this process'}</code>\n\n"
        "<b>Samples</b>\n" + telemetry_counts_text(exported),
        parse_mode="HTML",
    )


async def telemetry_report_command(update, context):
    if not telemetry_command_allowed(update):
        return
    await update.effective_message.reply_text(telemetry_report_text(runtime_metric_export()), parse_mode="HTML")


async def set_today_emoji(update, context):
    """Save a custom emoji supplied by the bot owner as the Today button icon."""
    chat_id = update.effective_chat.id
    if chat_id not in ADMIN_CHAT_IDS:
        return await update.effective_message.reply_text("Эта настройка доступна владельцу Noema.")
    entity = next((item for item in (update.effective_message.entities or [])
                   if item.type == MessageEntity.CUSTOM_EMOJI and item.custom_emoji_id), None)
    if not entity:
        return await update.effective_message.reply_text(
            "Пришли команду и живой эмодзи в одном сообщении:\n<code>/todayemoji 📆</code>\n\n"
            "Важно: выбери именно анимированный премиум-эмодзи из панели Telegram, а не обычный символ.",
            parse_mode="HTML")
    set_app_setting("interface_today_custom_emoji_id", entity.custom_emoji_id)
    return await update.effective_message.reply_text(
        "Готово — живой календарь установлен на кнопку «Сегодня».", reply_markup=main_keyboard())


async def set_interface_emoji(update, context):
    """Capture a premium custom emoji for a supported Noema interface slot."""
    chat_id = update.effective_chat.id
    if chat_id not in ADMIN_CHAT_IDS:
        return await update.effective_message.reply_text("Эта настройка доступна владельцу Noema.")
    slots = EMOJI_SLOT_NAMES
    slot = (context.args[0].lower() if context.args else "")
    if slot not in slots:
        return await update.effective_message.reply_text(
            "Сначала отправь <code>/emojihelp</code> — там все доступные слоты.", parse_mode="HTML")
    entity = next((item for item in (update.effective_message.entities or [])
                   if item.type == MessageEntity.CUSTOM_EMOJI and item.custom_emoji_id), None)
    if not entity:
        return await update.effective_message.reply_text("Не вижу живого эмодзи. Выбери его из Premium-панели Telegram и отправь команду ещё раз.")
    key = f"task_{slot}_custom_emoji_id" if slot in {"open", "done", "failed"} else f"interface_{slot}_custom_emoji_id"
    set_app_setting(key, entity.custom_emoji_id)
    return await update.effective_message.reply_text(f"Готово — установлен значок «{slots[slot]}».", reply_markup=main_keyboard())


async def emoji_help(update, context):
    if update.effective_chat.id not in ADMIN_CHAT_IDS:
        return
    return await update.effective_message.reply_text(
        "<b>Живые эмодзи Noema</b>\n\n"
        "Главное меню: <code>today</code>, <code>briefing</code>, <code>settings</code>, <code>more</code>.\n"
        "Раздел «Ещё»: <code>tasks</code>, <code>reminders</code>, <code>people</code>, <code>notes</code>, <code>budget</code>.\n"
        "Настройки: <code>model</code>, <code>vision</code>, <code>replymode</code>, <code>rules</code>, <code>iphone</code>, <code>keys</code>, <code>status</code>, <code>clear</code>.\n"
        "Задачи: <code>open</code>, <code>done</code>, <code>failed</code>.\n\n"
        "Формат: <code>/setemoji done</code>, затем в том же сообщении выбери живой эмодзи из Premium-панели.\n\n"
        "Для ответов: отправь <code>/replyemoji</code> и любые живые эмодзи в том же сообщении. Список — «Настройки → ✨ Эмодзи».",
        parse_mode="HTML")


async def add_reply_emoji(update, context):
    """Add one owner-selected custom emoji to Noema's reply palette."""
    if update.effective_chat.id not in ADMIN_CHAT_IDS:
        return await update.effective_message.reply_text("Эта настройка доступна владельцу Noema.")
    message = update.effective_message
    entities = [item for item in (message.entities or [])
                if item.type == MessageEntity.CUSTOM_EMOJI and item.custom_emoji_id]
    if not entities:
        return await message.reply_text(
            "Отправь <code>/replyemoji</code> и выбери живой эмодзи в этом же сообщении.", parse_mode="HTML")
    palette = reply_emoji_palette()
    added = 0
    for entity in entities:
        alt = message.parse_entity(entity) or "✨"
        if not is_live_emoji_fallback(alt):
            continue
        if not any(item["id"] == entity.custom_emoji_id for item in palette):
            palette.append({"id": entity.custom_emoji_id, "alt": alt})
            added += 1
    set_app_setting("reply_custom_emoji_palette", json.dumps(palette, ensure_ascii=False))
    return await message.reply_text(f"Добавлено: {added}. В палитре ответов: {len(palette)}.")


async def clear_reply_emojis(update, context):
    if update.effective_chat.id not in ADMIN_CHAT_IDS:
        return
    set_app_setting("reply_custom_emoji_palette", "[]")
    return await update.effective_message.reply_text("Палитра живых эмодзи для ответов очищена.")



def people_page(chat_id):
    d=get_people(chat_id)
    lines=["👥 Люди:"]

    if not d["people"]:
        lines.append("Людей пока нет.")

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

    markup = InlineKeyboardMarkup([[InlineKeyboardButton("‹ Назад", callback_data="menu:more")]])
    return "\n".join(lines), live_markup(markup)


async def list_people(update,context):
    text, markup = people_page(update.effective_chat.id)
    return await replace_active_ui(update, context, text, markup)



def money(amount):
    return f'{abs(float(amount)):,.0f}'.replace(',', ' ') + " ₽"


def budget_period_label(chat_id, date_from, date_to):
    today = datetime.now(timezone_for(chat_id)).date()
    if date_from == date_to == today.isoformat():
        return "Сегодня"
    if date_from == date_to == (today - timedelta(days=1)).isoformat():
        return "Вчера"
    if date_from == (today - timedelta(days=6)).isoformat() and date_to == today.isoformat():
        return "7 дней"
    if date_from == today.replace(day=1).isoformat() and date_to == today.isoformat():
        return "За месяц"
    return f'{date_from[8:10]}.{date_from[5:7]}–{date_to[8:10]}.{date_to[5:7]}.{date_to[2:4]}'


def budget_page(chat_id, date_from, date_to, page=0, page_size=6):
    rows = get_expenses(chat_id, date_from, date_to, "")["items"]
    income = sum(float(row["amount"]) for row in rows if row.get("kind") == "income")
    expense = sum(float(row["amount"]) for row in rows if row.get("kind") != "income")
    today = datetime.now(timezone_for(chat_id)).date().isoformat()
    today_rows = get_expenses(chat_id, today, today, "")["items"]
    today_income = sum(float(row["amount"]) for row in today_rows if row.get("kind") == "income")
    today_expense = sum(float(row["amount"]) for row in today_rows if row.get("kind") != "income")
    pages = max(1, (len(rows) + page_size - 1) // page_size)
    page = max(0, min(page, pages - 1))
    shown = rows[page * page_size:(page + 1) * page_size]
    balance = income - expense
    today_balance = today_income - today_expense
    lines = [f'💳 <b>{budget_period_label(chat_id, date_from, date_to)}: {"+" if balance >= 0 else "−"}{money(balance)}</b>',
             f'Приход: +{money(income)}    Расход: −{money(expense)}',
             f'<b>Сегодня: {"+" if today_balance >= 0 else "−"}{money(today_balance)}</b>', ""]
    if not shown:
        lines.append("Операций за этот период нет.")
    shown_rows = []
    for row in shown:
        icon = "➕" if row.get("kind") == "income" else "➖"
        try:
            date_label = datetime.fromisoformat(str(row["spent_at"])).strftime("%d.%m.%y")
        except ValueError:
            date_label = str(row["spent_at"])[:10]
        label = (row["description"] or row["category"] or "—").replace("\n", " ")[:34]
        shown_rows.append(("+" if icon == "➕" else "−", money(row["amount"]), label, date_label))
    if shown:
        amount_width = max(len("Сумма"), *(len(row[1]) for row in shown_rows))
        label_width = max(len("За что"), *(len(row[2]) for row in shown_rows))
        lines.extend(["<pre>", f'{"±":<1}  {"Сумма":>{amount_width}}  {"За что":<{label_width}}  Дата'])
        lines.append("─" * (amount_width + label_width + 14))
        for sign, amount, label, date_label in shown_rows:
            lines.append(f'{sign:<1}  {amount:>{amount_width}}  {html.escape(label):<{label_width}}  {date_label}')
        lines.append("</pre>")
    selected = budget_period_label(chat_id, date_from, date_to)
    def period_button(label, action):
        return InlineKeyboardButton(("● " if selected == label else "") + label, callback_data=action)
    controls = [
        [period_button("Сегодня", "budget:today"), period_button("Вчера", "budget:yesterday"), period_button("7 дней", "budget:week")],
        [period_button("За месяц", "budget:month"), InlineKeyboardButton("📅 Период", callback_data="budget:pick")],
    ]
    if pages > 1:
        nav = []
        if page > 0: nav.append(InlineKeyboardButton("‹", callback_data=f"budget:range:{date_from}:{date_to}:{page-1}"))
        nav.append(InlineKeyboardButton(f"{page + 1}/{pages}", callback_data="budget:noop"))
        if page + 1 < pages: nav.append(InlineKeyboardButton("›", callback_data=f"budget:range:{date_from}:{date_to}:{page+1}"))
        controls.append(nav)
    controls.append([InlineKeyboardButton("‹ Назад", callback_data="menu:more")])
    return "\n".join(lines), live_markup(InlineKeyboardMarkup(controls))


async def list_expenses(update,context):
    today = datetime.now(timezone_for(update.effective_chat.id)).date()
    text, markup = budget_page(update.effective_chat.id, today.replace(day=1).isoformat(), today.isoformat())
    return await replace_active_ui(update, context, text, markup)



def button_rows(buttons, width=4):
    return [buttons[index:index + width] for index in range(0, len(buttons), width)]


def reminders_page(chat_id, page=0, page_size=6):
    chat_tz = timezone_for(chat_id)
    with conn() as c:
        rs = c.execute("SELECT id,text,remind_at_utc,followup_count FROM reminders WHERE chat_id=? AND acknowledged=0 ORDER BY remind_at_utc",
                       (chat_id,)).fetchall()
    if not rs:
        return "⏰ Активных напоминаний нет.", live_markup(InlineKeyboardMarkup([
            [InlineKeyboardButton("‹ Назад", callback_data="menu:more")]
        ]))
    pages = max(1, (len(rs) + page_size - 1) // page_size)
    page = max(0, min(page, pages - 1))
    rs = rs[page * page_size:(page + 1) * page_size]
    lines = [f"⏰ Напоминания · {page + 1}/{pages}"]
    buttons = []
    for r in rs[:20]:
        dt = datetime.fromisoformat(r["remind_at_utc"]).astimezone(chat_tz)
        suffix = f" · повторов: {r['followup_count']}" if r["followup_count"] else ""
        lines.append(f'⏰ <code>#{r["id"]}</code> — {dt:%d.%m %H:%M} — {html.escape(r["text"])}{suffix}')
        buttons.append(InlineKeyboardButton(f'🗑 #{r["id"]}', callback_data=f'delremask:{r["id"]}:{page}'))
    rows = button_rows(buttons)
    if pages > 1:
        nav = []
        if page > 0: nav.append(InlineKeyboardButton("‹", callback_data=f"reminders:page:{page-1}"))
        nav.append(InlineKeyboardButton(f"{page + 1}/{pages}", callback_data="reminders:noop"))
        if page + 1 < pages: nav.append(InlineKeyboardButton("›", callback_data=f"reminders:page:{page+1}"))
        rows.append(nav)
    rows.append([InlineKeyboardButton("‹ Назад", callback_data="menu:more")])
    return "\n".join(lines), live_markup(InlineKeyboardMarkup(rows))


async def reminders(update,context):
    text, markup = reminders_page(update.effective_chat.id)
    return await replace_active_ui(update, context, text, markup)



def tasks_page(chat_id, page=0, page_size=8):
    with conn() as c:
        total = c.execute("SELECT COUNT(*) FROM tasks WHERE chat_id=?", (chat_id,)).fetchone()[0]
        pages = max(1, (total + page_size - 1) // page_size)
        page = max(0, min(page, pages - 1))
        rows = c.execute("""SELECT id,text,due_date,status FROM tasks WHERE chat_id=?
                         ORDER BY CASE WHEN status='open' THEN 0 ELSE 1 END, due_date, id DESC
                         LIMIT ? OFFSET ?""", (chat_id, page_size, page * page_size)).fetchall()
    if not rows:
        return "✅ Задач пока нет.", live_markup(InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ Добавить задачу", callback_data="tasks:add")],
            [InlineKeyboardButton("‹ Назад", callback_data="menu:more")],
        ]))
    lines = [f"✅ Задачи · {page + 1}/{pages}"]
    delete_buttons = []
    for row in rows:
        icon = "◻️" if row["status"] == "open" else "✅"
        date = f" · {row['due_date'][8:10]}.{row['due_date'][5:7]}" if len(row["due_date"] or "") >= 10 else ""
        lines.append(f"{icon} <code>#{row['id']}</code> — {html.escape(row['text'])}{date}")
        delete_buttons.append(InlineKeyboardButton(f"🗑 #{row['id']}", callback_data=f"deltaskask:{row['id']}:{page}"))
    buttons = button_rows(delete_buttons)
    if pages > 1:
        nav = []
        if page > 0: nav.append(InlineKeyboardButton("‹", callback_data=f"tasks:page:{page - 1}"))
        nav.append(InlineKeyboardButton(f"{page + 1}/{pages}", callback_data="tasks:noop"))
        if page + 1 < pages: nav.append(InlineKeyboardButton("›", callback_data=f"tasks:page:{page + 1}"))
        buttons.append(nav)
    buttons.append([InlineKeyboardButton("➕ Добавить задачу", callback_data="tasks:add")])
    buttons.append([InlineKeyboardButton("‹ Назад", callback_data="menu:more")])
    return "\n".join(lines), live_markup(InlineKeyboardMarkup(buttons))


async def tasks(update, context, page=0):
    text, markup = tasks_page(update.effective_chat.id, page)
    return await replace_active_ui(update, context, text, markup)


def notes_page(chat_id, page=0, page_size=6):
    with conn() as c:
        total = c.execute("SELECT COUNT(*) FROM notes WHERE chat_id=?", (chat_id,)).fetchone()[0]
        pages = max(1, (total + page_size - 1) // page_size)
        page = max(0, min(page, pages - 1))
        rows = c.execute("SELECT id,title,text FROM notes WHERE chat_id=? ORDER BY id DESC LIMIT ? OFFSET ?",
                         (chat_id, page_size, page * page_size)).fetchall()
    if not rows:
        return "📝 Заметок пока нет.", live_markup(InlineKeyboardMarkup([
            [InlineKeyboardButton("‹ Назад", callback_data="menu:more")]
        ]))
    lines = [f"📝 Заметки · {page + 1}/{pages}"]
    delete_buttons = []
    for row in rows:
        lines.append(f"<code>#{row['id']}</code> — {html.escape(row['text'])}")
        delete_buttons.append(InlineKeyboardButton(f"🗑 #{row['id']}", callback_data=f"delnoteask:{row['id']}:{page}"))
    buttons = button_rows(delete_buttons)
    nav = []
    if page > 0: nav.append(InlineKeyboardButton("‹", callback_data=f"notes:page:{page-1}"))
    if page + 1 < pages: nav.append(InlineKeyboardButton("›", callback_data=f"notes:page:{page+1}"))
    if nav: buttons.append(nav)
    buttons.append([InlineKeyboardButton("‹ Назад", callback_data="menu:more")])
    return "\n".join(lines), live_markup(InlineKeyboardMarkup(buttons))


async def notes(update,context, page=0):
    text, markup = notes_page(update.effective_chat.id, page)
    return await replace_active_ui(update, context, text, markup)



def plan_page(chat_id, day, page=0, page_size=12):
    d = get_plan_for_date(chat_id, day)
    selected = datetime.fromisoformat(day).date()
    today = datetime.now(timezone_for(chat_id)).date()
    heading = "📅 Сегодня" if selected == today else f"📅 {selected:%d.%m.%Y}"
    entries = [("task", task) for task in d["tasks"]] + [("reminder", reminder) for reminder in d["reminders"]]
    def entry_sort_key(item):
        kind, value = item
        completed = value.get("status") != "open" if kind == "task" else bool(value.get("acknowledged"))
        when = (value.get("due_date") or "") if kind == "task" else value.get("time") or ""
        return (1 if completed else 0, when, value.get("id", 0))
    entries.sort(key=entry_sort_key)
    pages = max(1, (len(entries) + page_size - 1) // page_size)
    page = max(0, min(page, pages - 1))
    entries = entries[page * page_size:(page + 1) * page_size]
    lines = [f"{heading} · {page + 1}/{pages}"]
    buttons = []
    task_toggle_buttons = []
    active_heading_added = False
    completed_heading_added = False
    for entry_type, entry in entries:
        if entry_type == "task":
            task = entry
            status = task["status"]
            is_completed = status != "open"
            if is_completed and not completed_heading_added:
                lines.append("\n<b>Выполнено</b>")
                completed_heading_added = True
            elif not is_completed and not active_heading_added:
                lines.append("\n<b>Предстоящие</b>")
                active_heading_added = True
            marker = {"open": "◻️", "done": "✅", "failed": "❌"}.get(status, "◻️")
            late = " · просрочено" if status == "open" and task["due_date"] and task["due_date"] < today.isoformat() else ""
            lines.append(f'{marker} <code>#{task["id"]}</code> · задача — {html.escape(task["text"])}{late}')
            toggle_icon = "✅" if status == "done" else "◻️"
            if status == "done":
                emoji_id = app_setting("interface_tasks_custom_emoji_id") or emoji_id_for("✅")
            else:
                emoji_id = app_setting("task_open_custom_emoji_id") or emoji_id_for("◻️")
            task_toggle_buttons.append(InlineKeyboardButton(
                f"#{task['id']}" if emoji_id else f"{toggle_icon} #{task['id']}",
                callback_data=f"tasktoggle:{task['id']}:{day}:{page}", icon_custom_emoji_id=emoji_id or None))
        else:
            reminder = entry
            is_completed = bool(reminder["acknowledged"])
            if is_completed and not completed_heading_added:
                lines.append("\n<b>Выполнено</b>")
                completed_heading_added = True
            elif not is_completed and not active_heading_added:
                lines.append("\n<b>Предстоящие</b>")
                active_heading_added = True
            # Reminders always use the reminder icon. Their completion state is
            # represented by the section, while tasks keep their own checkmark.
            marker = "⏰"
            lines.append(f'{marker} {reminder["time"]} — {html.escape(reminder["text"])}')
    if task_toggle_buttons:
        buttons.extend(button_rows(task_toggle_buttons, 4))
    if len(lines) == 1:
        lines.append("Пока ничего нет.")
    previous = (selected - timedelta(days=1)).isoformat()
    following = (selected + timedelta(days=1)).isoformat()
    if pages > 1:
        page_nav = []
        if page > 0: page_nav.append(InlineKeyboardButton("‹", callback_data=f"plan:{day}:{page-1}"))
        page_nav.append(InlineKeyboardButton(f"{page + 1}/{pages}", callback_data="plan:noop"))
        if page + 1 < pages: page_nav.append(InlineKeyboardButton("›", callback_data=f"plan:{day}:{page+1}"))
        buttons.append(page_nav)
    buttons.append([InlineKeyboardButton("‹ День", callback_data=f"plan:{previous}:0"),
                    InlineKeyboardButton("🔎 Дата", callback_data="plan:pick"),
                    InlineKeyboardButton("Вперёд ›", callback_data=f"plan:{following}:0")])
    buttons.append([InlineKeyboardButton("‹ Назад", callback_data="ui:close")])
    return "\n".join(lines), live_markup(InlineKeyboardMarkup(buttons))


async def today_plan(update,context, day=None):
    day = day or datetime.now(timezone_for(update.effective_chat.id)).date().isoformat()
    text, markup = plan_page(update.effective_chat.id, day)
    return await replace_active_ui(update, context, text, markup)



async def callback(update,context):

    callback_started=time.perf_counter()
    raw_query=update.callback_query; await raw_query.answer()
    callback_ack_ms=(time.perf_counter()-callback_started)*1000
    record_runtime_metric("callback_ack_ms", callback_ack_ms)
    LOGGER.debug("telemetry callback_ack_ms=%.1f", callback_ack_ms)
    if not str(raw_query.data or "").startswith("ackrem:"):
        await adopt_active_ui(raw_query)
    q=LiveCallbackQuery(raw_query)
    register_bot_user(q.message.chat_id, getattr(update, "effective_user", None))

    if q.data == "ui:close":
        with contextlib.suppress(Exception):
            await raw_query.message.delete()
        set_active_ui_message_id(q.message.chat_id, 0)
        return

    if q.data == "menu:more":
        text, markup = more_page()
        return await q.edit_message_text(text, reply_markup=markup, parse_mode="HTML")

    # Model and key management is a platform setting now. Older inline
    # messages may still contain these buttons, so protect those too.
    admin_only = ("settings:model", "settings:vision", "settings:keys", "model:", "vision:", "keys:")
    if q.data.startswith(admin_only) and q.message.chat_id not in ADMIN_CHAT_IDS:
        return await q.edit_message_text("Модели и ключи уже настроены Noema.", reply_markup=settings_keyboard(q.message.chat_id))

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

    if q.data == "settings:vision":
        cid = q.message.chat_id
        personal = has_personal_api_key(cid)
        active = vision_models_for(cid)[0]
        if personal:
            text = ("<b>👁 Vision-модель</b>\n"
                    f"Личный ключ активен. Изображения будут обработаны через <code>{html.escape(active)}</code>.\n\n"
                    "Можно указать любую Vision-модель OpenRouter, доступную вашему ключу.")
            buttons = [
                [InlineKeyboardButton("✏️ Изменить Vision-модель", callback_data="vision:personal:add")],
                [InlineKeyboardButton("↺ Стандартная модель", callback_data="vision:personal:reset")],
            ]
            if cid in ADMIN_CHAT_IDS:
                buttons.append([InlineKeyboardButton("⚙️ Общая Vision-модель", callback_data="vision:shared:add")])
            buttons.append([InlineKeyboardButton("‹ Настройки", callback_data="settings:back")])
        else:
            text = ("<b>👁 Vision-модель</b>\n"
                    f"Сейчас используется общая стандартная: <code>{html.escape(active)}</code>.\n\n"
                    "Подключите личный ключ, чтобы выбрать свою модель и оплачивать распознавание отдельно.")
            buttons = []
            if cid in ADMIN_CHAT_IDS:
                buttons.append([InlineKeyboardButton("⚙️ Изменить общую модель", callback_data="vision:shared:add")])
            buttons += [[InlineKeyboardButton("🔐 API-ключи", callback_data="settings:keys")],
                        [InlineKeyboardButton("‹ Настройки", callback_data="settings:back")]]
        return await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode="HTML")

    if q.data == "vision:personal:add":
        context.user_data["awaiting_vision_model"] = "personal"
        return await q.edit_message_text(
            "Пришлите точный ID Vision-модели OpenRouter, например:\n<code>google/gemini-2.5-flash</code>",
            parse_mode="HTML", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("‹ Назад", callback_data="settings:vision")]]))

    if q.data == "vision:shared:add":
        if q.message.chat_id not in ADMIN_CHAT_IDS:
            return await q.answer("Нет доступа.", show_alert=True)
        context.user_data["awaiting_vision_model"] = "shared"
        return await q.edit_message_text(
            "Пришлите точный ID общей Vision-модели. Она будет использоваться всеми, у кого нет личного ключа.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("‹ Назад", callback_data="settings:vision")]]))

    if q.data == "vision:personal:reset":
        model_router().set_vision(q.message.chat_id, "")
        return await q.edit_message_text(
            f"Vision-модель возвращена к стандартной: <code>{html.escape(shared_vision_model())}</code>.",
            parse_mode="HTML", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("‹ Vision", callback_data="settings:vision")]]))

    if q.data.startswith("model:set:"):
        model = q.data.split(":", 2)[2]
        if model not in available_models_for(q.message.chat_id):
            return await q.edit_message_text("Модель недоступна.", reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("‹ Назад", callback_data="settings:model")]
            ]))
        model_router().set_primary(q.message.chat_id, model)
        await q.edit_message_text(
            f"<b>🧠 Модель выбрана</b>\n{html.escape(model)}\n\nСледующее сообщение сразу будет обработано этой моделью.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("Изменить", callback_data="settings:model")],
                [InlineKeyboardButton("‹ Назад", callback_data="settings:back")],
            ]),
        )
        return

    if q.data == "model:add":
        context.user_data["awaiting_model"] = True
        await q.edit_message_text(
            "Пришлите точный ID модели OpenRouter, например:\n<code>deepseek/deepseek-v4-flash-0731</code>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("‹ Назад", callback_data="settings:model")]]),
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
            return await q.edit_message_text("Модель уже удалена.", reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("‹ Назад", callback_data="settings:model")]
            ]))
        set_chat_model(q.message.chat_id, model, enabled=False)
        if model_router().resolve(q.message.chat_id, "chat")["primary"] == model:
            model_router().set_primary(q.message.chat_id, "")
        await q.edit_message_text(f"Модель удалена из списка этого чата:\n{html.escape(model)}",
                                  reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("К моделям", callback_data="settings:model")]]))
        return

    if q.data == "settings:back":
        await q.edit_message_text("⚙️ Настройки", reply_markup=settings_keyboard(q.message.chat_id))
        return

    if q.data in ("menu:mode", "settings:mode"):
        await q.edit_message_text("🔊 Режим ответа", reply_markup=mode_keyboard(q.message.chat_id))
        return

    if q.data.startswith("mode:set:"):
        mode = q.data.split(":", 2)[2]
        if mode not in ("text", "voice", "voice_and_text"):
            return await q.edit_message_text("Неизвестный режим.", reply_markup=mode_keyboard(q.message.chat_id))
        set_mode(q.message.chat_id, mode)
        labels = {"text": "💬 Текст", "voice": "🎙 Голос", "voice_and_text": "🔊 Голос + текст"}
        await q.edit_message_text(f"Режим: {labels[mode]}.", reply_markup=mode_keyboard(q.message.chat_id))
        return

    if q.data == "menu:reminders":
        text, markup = reminders_page(q.message.chat_id)
        return await q.edit_message_text(text, reply_markup=markup, parse_mode="HTML")
    if q.data == "menu:tasks":
        text, markup = tasks_page(q.message.chat_id)
        return await q.edit_message_text(text, reply_markup=markup, parse_mode="HTML")
    if q.data == "tasks:add":
        context.user_data["awaiting_task_text"] = True
        return await q.edit_message_text(
            "Напишите задачу. Можно добавить дату: <code>12.09 — позвонить врачу</code>. Без даты поставлю на сегодня.",
            parse_mode="HTML", reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("‹ Назад", callback_data="menu:tasks")]
            ]))
    if q.data.startswith("tasks:page:"):
        page = int(q.data.rsplit(":", 1)[1])
        text, markup = tasks_page(q.message.chat_id, page)
        return await q.edit_message_text(live_ui_text(text), reply_markup=markup, parse_mode="HTML")
    if q.data.startswith("deltaskask:"):
        _, task_id, page = q.data.split(":")
        return await q.edit_message_text(f"Удалить задачу #{task_id}?", reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🗑 Удалить", callback_data=f"deltask:{task_id}:{page}"),
             InlineKeyboardButton("‹ Назад", callback_data=f"tasks:page:{page}")],
        ]))
    if q.data.startswith("deltask:"):
        _, task_id, page = q.data.split(":")
        delete_task(q.message.chat_id, int(task_id))
        text, markup = tasks_page(q.message.chat_id, int(page))
        return await q.edit_message_text(live_ui_text(text), reply_markup=markup, parse_mode="HTML")
    if q.data in ("menu:expenses", "menu:budget"):
        today = datetime.now(timezone_for(q.message.chat_id)).date()
        text, markup = budget_page(q.message.chat_id, today.replace(day=1).isoformat(), today.isoformat())
        return await q.edit_message_text(text, reply_markup=markup, parse_mode="HTML")
    if q.data.startswith("budget:"):
        action = q.data.split(":", 1)[1]
        today = datetime.now(timezone_for(q.message.chat_id)).date()
        if action == "noop":
            return
        if action == "pick":
            context.user_data["awaiting_budget_range"] = True
            return await q.edit_message_text(
                "Напишите период: 01.09.2026–07.09.2026. Можно указать и одну дату.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("‹ Назад", callback_data="budget:month")]
                ]))
        if action == "today":
            date_from = date_to = today.isoformat(); page = 0
        elif action == "yesterday":
            date_from = date_to = (today - timedelta(days=1)).isoformat(); page = 0
        elif action == "week":
            date_from = (today - timedelta(days=6)).isoformat(); date_to = today.isoformat(); page = 0
        elif action == "month":
            date_from = today.replace(day=1).isoformat(); date_to = today.isoformat(); page = 0
        elif action.startswith("range:"):
            _, date_from, date_to, page = action.split(":")
            page = int(page)
        else:
            return
        text, markup = budget_page(q.message.chat_id, date_from, date_to, page)
        try:
            return await q.edit_message_text(live_ui_text(text), reply_markup=markup, parse_mode="HTML")
        except BadRequest as error:
            # Pressing the already selected period is a harmless no-op, not an
            # application error worthy of a traceback in the operator log.
            if "Message is not modified" in str(error):
                return
            raise
    if q.data == "menu:briefing":
        return await q.edit_message_text(
            live_ui_text(build_briefing(q.message.chat_id)), parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("‹ Назад", callback_data="menu:more")]]))
    if q.data.startswith("plan:"):
        value = q.data.split(":", 1)[1]
        if value == "noop":
            return
        if value == "pick":
            context.user_data["awaiting_plan_date"] = True
            today = datetime.now(timezone_for(q.message.chat_id)).date().isoformat()
            return await q.edit_message_text(
                "Напишите дату в формате ДД.ММ.ГГГГ, например 15.09.2026.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("‹ Назад", callback_data=f"plan:{today}:0")]
                ]))
        try:
            day, page = value.split(":") if ":" in value else (value, "0")
            text, markup = plan_page(q.message.chat_id, day, int(page))
        except ValueError:
            return await q.answer("Не удалось прочитать дату.", show_alert=True)
        return await q.edit_message_text(live_ui_text(text), reply_markup=markup, parse_mode="HTML")
    if q.data.startswith(("taskdone:", "taskfail:")):
        action, task_id, day = q.data.split(":")
        # Buttons sent by older bot versions are still in chats.  Treat their
        # press as the same reversible toggle as the current UI.
        toggle_task_status(q.message.chat_id, int(task_id))
        text, markup = plan_page(q.message.chat_id, day)
        return await q.edit_message_text(live_ui_text(text), reply_markup=markup, parse_mode="HTML")
    if q.data.startswith("tasktoggle:"):
        _, task_id, day, page = q.data.split(":")
        toggle_task_status(q.message.chat_id, int(task_id))
        text, markup = plan_page(q.message.chat_id, day, int(page))
        return await q.edit_message_text(live_ui_text(text), reply_markup=markup, parse_mode="HTML")
    if q.data.startswith("remtoggle:"):
        _, reminder_id, day, page = q.data.split(":")
        with conn() as c:
            row = c.execute("SELECT acknowledged,sent FROM reminders WHERE id=? AND chat_id=?", (int(reminder_id), q.message.chat_id)).fetchone()
            if row:
                acknowledged = 0 if row["acknowledged"] else 1
                next_followup = "" if acknowledged else ((datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat() if row["sent"] else "")
                c.execute("UPDATE reminders SET acknowledged=?, next_followup_at=? WHERE id=? AND chat_id=?",
                          (acknowledged, next_followup, int(reminder_id), q.message.chat_id))
        text, markup = plan_page(q.message.chat_id, day, int(page))
        return await q.edit_message_text(live_ui_text(text), reply_markup=markup, parse_mode="HTML")
    if q.data.startswith("remdone:"):
        _, reminder_id, day, page = q.data.split(":")
        with conn() as c:
            c.execute("UPDATE reminders SET acknowledged=1, next_followup_at='' WHERE id=? AND chat_id=?",
                      (int(reminder_id), q.message.chat_id))
        text, markup = plan_page(q.message.chat_id, day, int(page))
        return await q.edit_message_text(live_ui_text(text), reply_markup=markup, parse_mode="HTML")
    if q.data == "menu:people":
        text, markup = people_page(q.message.chat_id)
        return await q.edit_message_text(text, reply_markup=markup, parse_mode="HTML")
    if q.data == "menu:notes":
        text, markup = notes_page(q.message.chat_id)
        return await q.edit_message_text(text, reply_markup=markup, parse_mode="HTML")
    if q.data.startswith("notes:page:"):
        page = int(q.data.rsplit(":", 1)[1])
        text, markup = notes_page(q.message.chat_id, page)
        return await q.edit_message_text(live_ui_text(text), reply_markup=markup, parse_mode="HTML")
    if q.data.startswith("delnoteask:"):
        _, note_id, page = q.data.split(":")
        return await q.edit_message_text(
            f"Удалить заметку #{note_id}? Это действие нельзя отменить.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🗑 Удалить", callback_data=f"delnote:{note_id}:{page}"),
                                                InlineKeyboardButton("‹ Назад", callback_data=f"notes:page:{page}")]]))
    if q.data.startswith("delnote:"):
        _, note_id, page = q.data.split(":")
        delete_note(q.message.chat_id, int(note_id))
        text, markup = notes_page(q.message.chat_id, int(page))
        return await q.edit_message_text(live_ui_text(text), reply_markup=markup, parse_mode="HTML")
    if q.data == "settings:rules":
        text, markup = rules_page(q.message.chat_id)
        return await q.edit_message_text(text, reply_markup=markup, parse_mode="HTML")
    if q.data.startswith("rule:edit:"):
        rule_id = int(q.data.rsplit(":", 1)[1])
        if not any(rule["id"] == rule_id for rule in behavior_rules_for(q.message.chat_id)):
            return await q.edit_message_text("Правило не найдено.", reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("‹ Назад", callback_data="settings:rules")]
            ]))
        context.user_data["awaiting_rule_edit"] = rule_id
        return await q.edit_message_text("Пришлите новую формулировку правила.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("‹ Назад", callback_data="settings:rules")]]))
    if q.data.startswith("rule:deleteask:"):
        rule_id = int(q.data.rsplit(":", 1)[1])
        return await q.edit_message_text(f"Удалить правило <code>#{rule_id}</code>?", parse_mode="HTML", reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🗑 Удалить", callback_data=f"rule:delete:{rule_id}"), InlineKeyboardButton("‹ Назад", callback_data="settings:rules")],
        ]))
    if q.data.startswith("rule:delete:"):
        rule_id = int(q.data.rsplit(":", 1)[1])
        delete_behavior_rule(q.message.chat_id, rule_id=rule_id)
        text, markup = rules_page(q.message.chat_id)
        return await q.edit_message_text(text, reply_markup=markup, parse_mode="HTML")
    if q.data == "rule:deleteallask":
        return await q.edit_message_text("Удалить все правила? Напоминания и другие данные не затрону.", reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🗑 Удалить все", callback_data="rule:deleteall"), InlineKeyboardButton("‹ Назад", callback_data="settings:rules")],
        ]))
    if q.data == "rule:deleteall":
        delete_behavior_rule(q.message.chat_id, all=True)
        text, markup = rules_page(q.message.chat_id)
        return await q.edit_message_text(text, reply_markup=markup, parse_mode="HTML")
    if q.data == "settings:iphone":
        text, markup = iphone_settings_page(q.message.chat_id)
        return await q.edit_message_text(text, reply_markup=markup, parse_mode="HTML")
    if q.data == "iphone:add":
        existing = quick_action_devices(q.message.chat_id)
        if existing:
            buttons = [[InlineKeyboardButton(f'⚙️ Открыть {item["name"]}', callback_data=f'iphone:device:{item["id"]}')]
                       for item in existing]
            buttons += [[InlineKeyboardButton("➕ Подключить ещё один iPhone", callback_data="iphone:add:force")],
                        [InlineKeyboardButton("‹ iPhone", callback_data="settings:iphone")]]
            return await q.edit_message_text(
                "📱 У вас уже есть подключённое устройство. Откройте его для настройки или отключите — новый ключ без необходимости создавать не нужно.",
                reply_markup=InlineKeyboardMarkup(buttons))
    if q.data in ("iphone:add", "iphone:add:force"):
        device = create_quick_action_device(q.message.chat_id)
        text = (
            f'📱 <b>{html.escape(device["name"])} подключён</b>\n\n'
            'Выберите, что хотите подключить. Для обычной голосовой команды нужен один ключ — его можно назначить на любую кнопку iPhone.'
        )
        return await q.edit_message_text(text, parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⚡ Быстрая команда", callback_data=f'iphone:quick:{device["id"]}'),
                                                InlineKeyboardButton("📤 Поделиться в Noema", callback_data=f'iphone:share:{device["id"]}')],
                                                [InlineKeyboardButton("⚙️ Разные действия для кнопок", callback_data=f'iphone:device:{device["id"]}')],
                                                [InlineKeyboardButton("‹ iPhone", callback_data="settings:iphone")]]))
    if q.data.startswith("iphone:quick:"):
        device_id = q.data.split(":", 2)[2]
        device = next((item for item in quick_action_devices(q.message.chat_id) if item["id"] == device_id), None)
        secret = device_quick_action_secret(q.message.chat_id, device_id) if device else ""
        if not secret:
            return await q.edit_message_text("Устройство не найдено. Выпустите новое подключение.", reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("‹ Назад", callback_data="settings:iphone")]
            ]))
        token = quick_action_token(device_id, "action", secret)
        text = (
            "⚡ <b>Быстрая команда</b>\n\n"
            "Скопируйте ключ и вставьте его в поле <b>token</b> готовой команды Noema на iPhone.\n\n"
            "<b>Ключ</b>:\n"
            f"<code>{quick_action_token(device_id, 'action', secret)}</code>"
        )
        return await q.edit_message_text(text, parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬇️ Установить голосовую команду", url="https://www.icloud.com/shortcuts/a5175e667221475685ca604ef11a7a4e")],
                                                [InlineKeyboardButton("⚙️ Разные действия для кнопок", callback_data=f"iphone:device:{device_id}")],
                                                [InlineKeyboardButton("‹ iPhone", callback_data=f"iphone:shortcut:{device_id}")]]))
    if q.data.startswith("iphone:share:"):
        device_id = q.data.split(":", 2)[2]
        device = next((item for item in quick_action_devices(q.message.chat_id) if item["id"] == device_id), None)
        secret = device_quick_action_secret(q.message.chat_id, device_id) if device else ""
        if not secret:
            return await q.edit_message_text("Устройство не найдено. Выпустите новое подключение.", reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("‹ Назад", callback_data="settings:iphone")]
            ]))
        text = (
            "📤 <b>Поделиться в Noema</b>\n\n"
            "Эта команда появляется в системном меню «Поделиться». Через неё можно отправить в Noema фото, скриншот, PDF, файл, ссылку или выделенный текст. Материал попадёт в тот же чат и обработается как обычное вложение Telegram.\n\n"
            "<b>Ключ</b> — нажмите на строку, чтобы скопировать:\n"
            f"<code>{quick_action_token(device_id, 'share', secret)}</code>"
        )
        return await q.edit_message_text(text, parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬇️ Установить «Поделиться в Noema»", url="https://www.icloud.com/shortcuts/b06c60cf744b43f6a766b5ff0395ca07")],
                                                [InlineKeyboardButton("‹ iPhone", callback_data=f"iphone:shortcut:{device_id}")]]))
    if q.data.startswith("iphone:shortcut:"):
        device_id = q.data.split(":", 2)[2]
        device = next((item for item in quick_action_devices(q.message.chat_id) if item["id"] == device_id), None)
        if not device:
            return await q.edit_message_text("Устройство не найдено или отключено.", reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("‹ Назад", callback_data="settings:iphone")]
            ]))
        text = (
            '<b>iPhone и Noema</b>\n\n'
            '1. Подключите одну голосовую команду к кнопке iPhone или Back Tap.\n'
            '2. При необходимости добавьте «Поделиться в Noema» в системное меню.\n\n'
            'Вам не нужно создавать отдельный ключ для каждой кнопки. Разные кнопки нужны только если вы хотите, чтобы они делали разное.'
        )
        return await q.edit_message_text(text, parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⚡ Быстрая команда", callback_data=f'iphone:quick:{device_id}'),
                                                InlineKeyboardButton("📤 Поделиться", callback_data=f'iphone:share:{device_id}')],
                                                [InlineKeyboardButton("⚙️ Разные действия для кнопок", callback_data=f'iphone:device:{device_id}')],
                                                [InlineKeyboardButton("🔑 Выпустить новый ключ", callback_data=f'iphone:rotateask:{device_id}')],
                                                [InlineKeyboardButton("‹ iPhone", callback_data="settings:iphone")]]))
    if q.data.startswith("iphone:device:"):
        device_id = q.data.split(":", 2)[2]
        text, markup = iphone_device_page(q.message.chat_id, device_id)
        return await q.edit_message_text(text, reply_markup=markup, parse_mode="HTML")
    if q.data.startswith("iphone:bind:"):
        _, _, device_id, trigger = q.data.split(":")
        labels = {"action": "Action Button", "double": "Double Back Tap", "triple": "Triple Back Tap"}
        buttons = [[InlineKeyboardButton(label, callback_data=f'iphone:set:{device_id}:{trigger}:{action}')]
                   for action, label in QUICK_ACTIONS.items()]
        buttons.append([InlineKeyboardButton("‹ Назад", callback_data=f'iphone:device:{device_id}')])
        return await q.edit_message_text(f'📱 {labels.get(trigger, "Кнопка")}\nВыберите действие:', reply_markup=InlineKeyboardMarkup(buttons))
    if q.data.startswith("iphone:set:"):
        _, _, device_id, trigger, action = q.data.split(":")
        if not set_quick_action_binding(q.message.chat_id, device_id, trigger, action):
            return await q.edit_message_text("Не удалось изменить действие.", reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("‹ Назад", callback_data=f"iphone:device:{device_id}")]
            ]))
        text, markup = iphone_device_page(q.message.chat_id, device_id)
        return await q.edit_message_text(text, reply_markup=markup, parse_mode="HTML")
    if q.data.startswith("iphone:rotateask:"):
        device_id = q.data.split(":", 2)[2]
        return await q.edit_message_text("Выпустить новый ключ? Старые команды Shortcut сразу перестанут работать.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔑 Выпустить", callback_data=f"iphone:rotate:{device_id}")],
                                                [InlineKeyboardButton("‹ Назад", callback_data=f"iphone:shortcut:{device_id}")]]))
    if q.data.startswith("iphone:rotate:"):
        device_id = q.data.split(":", 2)[2]
        secret = rotate_quick_action_secret(q.message.chat_id, device_id)
        if not secret:
            return await q.edit_message_text("Не удалось выпустить ключ. Проверьте USER_SECRETS_MASTER_KEY.", reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("‹ Назад", callback_data=f"iphone:shortcut:{device_id}")]
            ]))
        return await q.edit_message_text("🔑 <b>Новый ключ выпущен</b>\nСтарые быстрые команды сразу отключены. Откройте нужную команду ниже и вставьте новый ключ в Shortcut.",
            parse_mode="HTML", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📱 Открыть подключение", callback_data=f"iphone:shortcut:{device_id}")],
                                                                    [InlineKeyboardButton("‹ iPhone", callback_data="settings:iphone")]]))
    if q.data.startswith("iphone:revokeask:"):
        device_id = q.data.split(":", 2)[2]
        return await q.edit_message_text("Отключить iPhone? Команды с этого устройства сразу перестанут работать.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🗑 Отключить", callback_data=f'iphone:revoke:{device_id}'),
                                                InlineKeyboardButton("‹ Назад", callback_data=f'iphone:device:{device_id}')]]))
    if q.data.startswith("iphone:revoke:"):
        device_id = q.data.split(":", 2)[2]
        revoke_quick_action_device(q.message.chat_id, device_id)
        text, markup = iphone_settings_page(q.message.chat_id)
        return await q.edit_message_text(text, reply_markup=markup, parse_mode="HTML")
    if q.data.startswith("ackrem:"):
        reminder_id = int(q.data.split(":", 1)[1])
        with conn() as c:
            c.execute("UPDATE reminders SET acknowledged=1, next_followup_at='' WHERE id=? AND chat_id=?",
                      (reminder_id, q.message.chat_id))
        sent = await raw_query.edit_message_text(
            live_ui_text("⏰ Напоминание выполнено."),
            parse_mode="HTML", reply_markup=InlineKeyboardMarkup([]))
        schedule_ephemeral_delete(context, raw_query.message)
        return sent
    if q.data == "settings:status":
        return await q.edit_message_text(
            status_text(q.message.chat_id), parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("‹ Назад", callback_data="settings:back")]]))
    if q.data == "settings:keys":
        text, markup = api_keys_page(q.message.chat_id)
        return await q.edit_message_text(text, reply_markup=markup, parse_mode="HTML")
    if q.data == "settings:emoji":
        if q.message.chat_id not in ADMIN_CHAT_IDS:
            return await q.answer("Нет доступа.", show_alert=True)
        text, markup = emoji_palette_page()
        return await q.edit_message_text(text, reply_markup=markup, parse_mode="HTML")
    if q.data == "emoji:slots":
        return await q.edit_message_text("🎛 <b>Настройка кнопок</b>\n\nВыбери раздел.", parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🏠 Главное меню", callback_data="emoji:group:main"), InlineKeyboardButton("☰ Ещё", callback_data="emoji:group:more")],
                [InlineKeyboardButton("⚙️ Настройки", callback_data="emoji:group:settings"), InlineKeyboardButton("✅ Задачи", callback_data="emoji:group:tasks")],
                [InlineKeyboardButton("‹ Эмодзи", callback_data="settings:emoji")],
            ]))
    if q.data.startswith("emoji:group:"):
        group = q.data.rsplit(":", 1)[1]
        if group not in EMOJI_SLOT_GROUPS:
            return await q.answer("Раздел не найден.", show_alert=True)
        buttons = [[InlineKeyboardButton(label, callback_data=f"emoji:pick:{slot}")] for slot, label in EMOJI_SLOT_GROUPS[group]]
        buttons.append([InlineKeyboardButton("‹ Разделы", callback_data="emoji:slots")])
        return await q.edit_message_text("Выбери кнопку, затем отправь живой эмодзи.", reply_markup=InlineKeyboardMarkup(buttons))
    if q.data.startswith("emoji:pick:"):
        slot = q.data.rsplit(":", 1)[1]
        if slot not in EMOJI_SLOT_NAMES:
            return await q.answer("Кнопка не найдена.", show_alert=True)
        context.user_data["awaiting_interface_emoji"] = slot
        return await q.edit_message_text(
            f"Выбрано: <b>{html.escape(EMOJI_SLOT_NAMES[slot])}</b>.\n\nТеперь просто отправь один живой эмодзи из Premium-панели.",
            parse_mode="HTML", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("‹ Назад", callback_data="emoji:slots")]]))
    if q.data.startswith("emoji:page:"):
        if q.message.chat_id not in ADMIN_CHAT_IDS:
            return await q.answer("Нет доступа.", show_alert=True)
        page = q.data.rsplit(":", 1)[1]
        if page == "noop":
            return
        text, markup = emoji_palette_page(int(page))
        return await q.edit_message_text(text, reply_markup=markup, parse_mode="HTML")
    if q.data == "emoji:clearask":
        return await q.edit_message_text("Очистить всю палитру живых эмодзи для ответов?", reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🗑 Очистить", callback_data="emoji:clear")],
            [InlineKeyboardButton("‹ Назад", callback_data="settings:emoji")],
        ]))
    if q.data == "emoji:clear":
        set_app_setting("reply_custom_emoji_palette", "[]")
        text, markup = emoji_palette_page()
        return await q.edit_message_text(text, reply_markup=markup, parse_mode="HTML")
    if q.data == "keys:add":
        if not secrets_cipher():
            return await q.answer("Сначала нужен USER_SECRETS_MASTER_KEY на сервере.", show_alert=True)
        context.user_data["awaiting_personal_api_key"] = True
        return await q.edit_message_text("🔐 Пришлите личный ключ OpenRouter одним сообщением. После сохранения я удалю это сообщение из чата.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("‹ Назад", callback_data="settings:keys")]]))
    if q.data == "keys:remove":
        remove_user_api_key(q.message.chat_id)
        text, markup = api_keys_page(q.message.chat_id)
        return await q.edit_message_text(text, reply_markup=markup, parse_mode="HTML")
    if q.data == "keys:usage":
        text = usage_text(usage_summary(q.message.chat_id), "📊 <b>Мой расход за 30 дней</b>")
        return await q.edit_message_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("‹ API-ключи", callback_data="settings:keys")]]))
    if q.data == "keys:admin_usage":
        if q.message.chat_id not in ADMIN_CHAT_IDS:
            return await q.answer("Нет доступа.", show_alert=True)
        text, markup = shared_usage_users_page()
        return await q.edit_message_text(text, parse_mode="HTML", reply_markup=markup)
    if q.data == "keys:sync_labels":
        if q.message.chat_id not in ADMIN_CHAT_IDS:
            return await q.answer("Нет доступа.", show_alert=True)
        result = await asyncio.to_thread(sync_managed_key_labels)
        if not result.get("ok"):
            return await q.answer("Не удалось связаться с OpenRouter. Попробуйте позже.", show_alert=True)
        text, markup = api_keys_page(q.message.chat_id)
        await q.answer(
            f"Создано: {int(result.get('created') or 0)} · синхронизировано: {int(result.get('updated') or 0)}"
        )
        return await q.edit_message_text(text, parse_mode="HTML", reply_markup=markup)
    if q.data.startswith("keys:admin_users:"):
        if q.message.chat_id not in ADMIN_CHAT_IDS:
            return await q.answer("Нет доступа.", show_alert=True)
        page = q.data.rsplit(":", 1)[1]
        if page == "noop":
            return
        text, markup = shared_usage_users_page(int(page))
        return await q.edit_message_text(text, parse_mode="HTML", reply_markup=markup)
    if q.data.startswith("keys:admin_user:"):
        if q.message.chat_id not in ADMIN_CHAT_IDS:
            return await q.answer("Нет доступа.", show_alert=True)
        _, _, user_number, page = q.data.split(":")
        with conn() as c:
            user = c.execute("SELECT user_number,chat_id,username,display_name FROM bot_users WHERE user_number=?", (int(user_number),)).fetchone()
        if not user:
            return await q.edit_message_text("Пользователь не найден.", reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("‹ Назад", callback_data="keys:admin_usage")]
            ]))
        user = dict(user)
        rows = [row for row in usage_summary(user["chat_id"])
                if row.get("source") in {"shared", "managed"}]
        text = usage_text(rows, f'📊 <b>Ключ Noema · #{int(user["user_number"]):03d} {html.escape(user_caption(user))}</b>')
        text += managed_key_lifecycle_text(user["chat_id"])
        return await q.edit_message_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("‹ Пользователи", callback_data=f"keys:admin_users:{page}")],
            [InlineKeyboardButton("‹ API-ключи", callback_data="settings:keys")],
        ]))
    if q.data == "settings:clear":
        clear_history(q.message.chat_id)
        return await q.edit_message_text(
            "Контекст диалога очищен. Заметки, люди, файлы и знания сохранены.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("‹ Назад", callback_data="settings:back")]]))

    if q.data.startswith("delremask:"):
        _, rid, page = q.data.split(":")
        return await q.edit_message_text(
            f"Удалить напоминание #{rid}? Это действие нельзя отменить.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🗑 Удалить", callback_data=f"delrem:{rid}:{page}"),
                                                InlineKeyboardButton("‹ Назад", callback_data=f"reminders:page:{page}")]]))
    if q.data == "reminders:noop":
        return
    if q.data.startswith("reminders:page:"):
        page = int(q.data.rsplit(":", 1)[1])
        text, markup = reminders_page(q.message.chat_id, page)
        return await q.edit_message_text(text, reply_markup=markup, parse_mode="HTML")
    if q.data == "reminders:show":
        text, markup = reminders_page(q.message.chat_id)
        return await q.edit_message_text(text, reply_markup=markup, parse_mode="HTML")
    if q.data.startswith("delrem:"):
        _, rid, page = q.data.split(":")
        with conn() as c:
            c.execute("DELETE FROM reminders WHERE id=? AND chat_id=?",(rid,q.message.chat_id))
        text, markup = reminders_page(q.message.chat_id, int(page))
        return await q.edit_message_text(text, reply_markup=markup, parse_mode="HTML")


def more_page():
    markup = InlineKeyboardMarkup([
        [interface_inline_button("tasks", "✅", "Задачи", "menu:tasks"),
         interface_inline_button("reminders", "⏰", "Напоминания", "menu:reminders")],
        [interface_inline_button("people", "👥", "Люди", "menu:people"),
         interface_inline_button("notes", "📝", "Заметки", "menu:notes")],
        [interface_inline_button("budget", "💳", "Бюджет", "menu:budget")],
        [InlineKeyboardButton("‹ Назад", callback_data="ui:close")],
    ])
    if QUICK_ACTIONS_BASE_URL:
        rows = list(markup.inline_keyboard)
        rows.insert(0, [InlineKeyboardButton("Открыть Noema", web_app=WebAppInfo(url=f"{QUICK_ACTIONS_BASE_URL}/app"))])
        markup = InlineKeyboardMarkup(rows)
    return "✨ Дополнительно:", live_markup(markup)


def settings_keyboard(chat_id=None):
    if chat_id in ADMIN_CHAT_IDS:
        rows = [
            [interface_inline_button("model", "🧠", "Модель", "settings:model"), interface_inline_button("vision", "👁", "Vision", "settings:vision")],
            [interface_inline_button("replymode", "🔊", "Режим ответа", "menu:mode"), interface_inline_button("rules", "📜", "Правила", "settings:rules")],
            [interface_inline_button("iphone", "📱", "iPhone", "settings:iphone"), interface_inline_button("keys", "🔐", "Управление AI", "settings:keys")],
            [InlineKeyboardButton("✨ Эмодзи", callback_data="settings:emoji")],
            [interface_inline_button("status", "⚙️", "Статус", "settings:status"), interface_inline_button("clear", "🧹", "Очистить диалог", "settings:clear")],
        ]
    else:
        rows = [
            [interface_inline_button("replymode", "🔊", "Режим ответа", "menu:mode"), interface_inline_button("rules", "📜", "Правила", "settings:rules")],
            [interface_inline_button("iphone", "📱", "iPhone", "settings:iphone"), interface_inline_button("status", "⚙️", "Статус", "settings:status")],
            [interface_inline_button("clear", "🧹", "Очистить диалог", "settings:clear")],
        ]
    if QUICK_ACTIONS_BASE_URL:
        rows.append([InlineKeyboardButton("🌍 Определить часовой пояс", web_app=WebAppInfo(url=f"{QUICK_ACTIONS_BASE_URL}/timezone"))])
    rows.append([InlineKeyboardButton("‹ Назад", callback_data="ui:close")])
    return live_markup(InlineKeyboardMarkup(rows))


def emoji_palette_page(page=0, page_size=25):
    palette = reply_emoji_palette()
    pages = max(1, (len(palette) + page_size - 1) // page_size)
    page = max(0, min(int(page), pages - 1))
    shown = palette[page * page_size:(page + 1) * page_size]
    lines = ["✨ <b>Живые эмодзи Noema</b>",
             f"В палитре: <b>{len(palette)}</b>. В одном ответе Noema использует до 1 / 3 / 5 / 7 анимаций — по длине текста.",
             "", "Добавить: отправь <code>/replyemoji</code> и любые живые эмодзи в том же сообщении."]
    if shown:
        lines.extend(["", f"<b>Список · {page + 1}/{pages}</b>"])
        for index, item in enumerate(shown, page * page_size + 1):
            lines.append(f'<code>{index:03d}</code> <tg-emoji emoji-id="{item["id"]}">{html.escape(item["alt"])}</tg-emoji>')
    else:
        lines.append("\nПалитра пока пуста.")
    buttons = []
    if pages > 1:
        nav = []
        if page:
            nav.append(InlineKeyboardButton("‹", callback_data=f"emoji:page:{page - 1}"))
        nav.append(InlineKeyboardButton(f"{page + 1}/{pages}", callback_data="emoji:page:noop"))
        if page + 1 < pages:
            nav.append(InlineKeyboardButton("›", callback_data=f"emoji:page:{page + 1}"))
        buttons.append(nav)
    if palette:
        buttons.append([InlineKeyboardButton("🗑 Очистить палитру", callback_data="emoji:clearask")])
    buttons.append([InlineKeyboardButton("🎛 Настроить кнопки", callback_data="emoji:slots")])
    buttons.append([InlineKeyboardButton("‹ Настройки", callback_data="settings:back")])
    return "\n".join(lines), live_markup(InlineKeyboardMarkup(buttons))


def rules_page(chat_id):
    rules = behavior_rules_for(chat_id)
    lines = ["📜 <b>Правила поведения</b>", ""]
    if not rules:
        lines.append("Правил пока нет.")
    else:
        for rule in rules:
            marker = "●" if rule["enabled"] else "○"
            lines.append(f'{marker} <code>#{rule["id"]}</code> {html.escape(rule["description"])}')
    buttons = []
    for rule in rules:
        buttons.append([
            InlineKeyboardButton(f'✏️ #{rule["id"]}', callback_data=f'rule:edit:{rule["id"]}'),
            InlineKeyboardButton(f'🗑 #{rule["id"]}', callback_data=f'rule:deleteask:{rule["id"]}'),
        ])
    if rules:
        buttons.append([InlineKeyboardButton("🗑 Удалить все правила", callback_data="rule:deleteallask")])
    buttons.append([InlineKeyboardButton("‹ Настройки", callback_data="settings:back")])
    return "\n".join(lines), live_markup(InlineKeyboardMarkup(buttons))


def iphone_settings_page(chat_id):
    devices = quick_action_devices(chat_id)
    lines = ["📱 <b>Быстрые действия iPhone</b>", "Одна голосовая команда — на любую кнопку iPhone. Отдельно можно добавить отправку файлов через «Поделиться». Каждое устройство привязано только к своему чату."]
    buttons = [[InlineKeyboardButton("➕ Подключить iPhone", callback_data="iphone:add")]]
    for device in devices:
        lines.append(f'\n• {html.escape(device["name"])} · {"подключён" if device["active"] else "отключён"}')
        buttons.append([InlineKeyboardButton(f'⚙️ {device["name"]}', callback_data=f'iphone:device:{device["id"]}')])
    buttons.append([InlineKeyboardButton("‹ Настройки", callback_data="settings:back")])
    return "\n".join(lines), live_markup(InlineKeyboardMarkup(buttons))


def iphone_device_page(chat_id, device_id):
    device = next((item for item in quick_action_devices(chat_id) if item["id"] == device_id), None)
    if not device:
        return "Устройство не найдено.", InlineKeyboardMarkup([[InlineKeyboardButton("‹ iPhone", callback_data="settings:iphone")]])
    bindings = device["bindings"]
    labels = {"action": "Action Button", "double": "Double Back Tap", "triple": "Triple Back Tap"}
    lines = [f'📱 <b>{html.escape(device["name"])}</b>', "Разные действия необязательны. Если все кнопки должны просто передавать голос в Noema, используйте одну «Быструю команду» на любом числе кнопок.", "\n<b>Отдельные сценарии:</b>"]
    buttons = []
    for trigger in ("action", "double", "triple"):
        action = bindings.get(trigger, "note")
        lines.append(f'• {labels[trigger]}: {QUICK_ACTIONS[action]}')
        buttons.append([InlineKeyboardButton(f'{labels[trigger]}', callback_data=f'iphone:bind:{device_id}:{trigger}')])
    buttons += [[InlineKeyboardButton("⚡ Быстрая команда", callback_data=f'iphone:quick:{device_id}'),
                 InlineKeyboardButton("📤 Поделиться", callback_data=f'iphone:share:{device_id}')],
                [InlineKeyboardButton("🔑 Выпустить новый ключ", callback_data=f'iphone:rotateask:{device_id}')],
                [InlineKeyboardButton("🗑 Отключить iPhone", callback_data=f'iphone:revokeask:{device_id}')],
                [InlineKeyboardButton("‹ Все устройства", callback_data="settings:iphone")]]
    return "\n".join(lines), live_markup(InlineKeyboardMarkup(buttons))


def api_keys_page(chat_id):
    if chat_id not in ADMIN_CHAT_IDS:
        return "Ключи и модели настроены Noema.", InlineKeyboardMarkup([[InlineKeyboardButton("‹ Настройки", callback_data="settings:back")]])
    with conn() as c:
        count = c.execute("SELECT COUNT(*) AS total FROM managed_api_keys WHERE active=1").fetchone()["total"]
    if OR_MANAGEMENT_KEY and secrets_cipher():
        state = f"Автовыдача включена · лимит <b>${USER_MONTHLY_LIMIT_USD:.2f}</b> на пользователя в месяц.\nВыдано ключей: <b>{count}</b>."
    elif not OR_MANAGEMENT_KEY:
        state = "Автовыдача выключена: добавьте <code>OPENROUTER_MANAGEMENT_API_KEY</code> в Secrets Amvera. До этого используется общий ключ."
    else:
        state = "Автовыдача ждёт <code>USER_SECRETS_MASTER_KEY</code> для безопасного хранения ключей."
    buttons = [[InlineKeyboardButton("📊 Расходы пользователей", callback_data="keys:admin_usage")],
               [InlineKeyboardButton("↻ Создать и синхронизировать ключи", callback_data="keys:sync_labels")],
               [InlineKeyboardButton("‹ Настройки", callback_data="settings:back")]]
    return "🔐 <b>Управление AI</b>\n" + state + "\n\nПользователи получают отдельный ключ автоматически и не видят модели или API-ключи.", live_markup(InlineKeyboardMarkup(buttons))


def usage_text(rows, title, show_chats=False):
    if not rows:
        return title + "\n\nЗа последние 30 дней обращений ещё не было."
    total_cost = sum(float(row["cost"] or 0) for row in rows)
    total_requests = sum(int(row["requests"] or 0) for row in rows)
    total_tokens = sum(int(row["input_tokens"] or 0) + int(row["output_tokens"] or 0) for row in rows)
    lines = [title, f"Запросов: <b>{total_requests:,}</b> · Токенов: <b>{total_tokens:,}</b> · Стоимость: <b>${total_cost:.4f}</b>", "",
             "<pre>Модель                    Запр.   Токены        $</pre>"]
    for row in rows[:8]:
        source = {"personal": "личный", "managed": "отдельный"}.get(row["source"], "общий")
        chat = f'чат <code>{row["chat_id"]}</code> · ' if show_chats else ""
        model = str(row["model"] or "—")
        model = (model[:23] + "…") if len(model) > 24 else model
        tokens = int(row["input_tokens"] or 0) + int(row["output_tokens"] or 0)
        lines.append(f'{chat}<pre>{html.escape(model):<25}{int(row["requests"] or 0):>5,}{tokens:>9,}  ${float(row["cost"] or 0):>8.4f}</pre>')
        if not show_chats:
            lines.append(f"  {source} ключ")
    if len(rows) > 8:
        lines.append(f"\nПоказаны 8 из {len(rows)} моделей.")
    return "\n".join(lines)


def managed_key_lifecycle_text(chat_id):
    with conn() as c:
        current = c.execute("SELECT created_at,updated_at FROM managed_api_keys WHERE chat_id=? AND active=1", (chat_id,)).fetchone()
    lines = ["", "🔑 <b>Ключ Noema</b>"]
    if current:
        created = str(current["created_at"] or "")[:10]
        lines.append(f"Текущий · выпущен {created or '—'} · лимит ${USER_MONTHLY_LIMIT_USD:.2f}/мес.")
    else:
        lines.append("Отдельный ключ пока не выпущен.")
    events = managed_key_history(chat_id)
    if events:
        lines.append("Замены:")
        for event in events:
            when = str(event["created_at"] or "")[:16].replace("T", " ")
            lines.append(f"• {when} · новый ключ выпущен ({html.escape(event['reason'])})")
    return "\n".join(lines)


def user_caption(row):
    username = (row.get("username") or "").strip()
    if username:
        return "@" + username
    return (row.get("display_name") or "без username").strip() or "без username"


def shared_usage_users_page(page=0, page_size=8):
    users = shared_usage_users()
    pages = max(1, (len(users) + page_size - 1) // page_size)
    page = max(0, min(int(page), pages - 1))
    shown = users[page * page_size:(page + 1) * page_size]
    if not users:
        return ("📈 <b>Ключи Noema · пользователи</b>\n\nЗа последние 30 дней расхода пока нет.",
                InlineKeyboardMarkup([[InlineKeyboardButton("‹ API-ключи", callback_data="settings:keys")]]))
    total_cost = sum(float(row["cost"] or 0) for row in users)
    total_requests = sum(int(row["requests"] or 0) for row in users)
    lines = ["📈 <b>Ключи Noema · пользователи</b>",
             f"Пользователей: <b>{len(users)}</b> · Запросов: <b>{total_requests:,}</b> · Стоимость: <b>${total_cost:.4f}</b>", ""]
    buttons = []
    for row in shown:
        number = int(row["user_number"])
        caption = user_caption(row)
        lines.append(f'<code>#{number:03d}</code> {html.escape(caption)} · {int(row["requests"]):,} запр. · ${float(row["cost"] or 0):.4f}')
        buttons.append(InlineKeyboardButton(f"#{number:03d} {caption}"[:60], callback_data=f"keys:admin_user:{number}:{page}"))
    markup_rows = button_rows(buttons, 2)
    if pages > 1:
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton("‹", callback_data=f"keys:admin_users:{page - 1}"))
        nav.append(InlineKeyboardButton(f"{page + 1}/{pages}", callback_data="keys:admin_users:noop"))
        if page + 1 < pages:
            nav.append(InlineKeyboardButton("›", callback_data=f"keys:admin_users:{page + 1}"))
        markup_rows.append(nav)
    markup_rows.append([InlineKeyboardButton("‹ API-ключи", callback_data="settings:keys")])
    return "\n".join(lines), live_markup(InlineKeyboardMarkup(markup_rows))


def mode_keyboard(chat_id):
    current = get_mode(chat_id)
    choices = [("text", "💬 Текст"), ("voice", "🎙 Голос"), ("voice_and_text", "🔊 Голос + текст")]
    rows = [
        [InlineKeyboardButton(("● " if current == value else "○ ") + label,
                              callback_data=f"mode:set:{value}")]
        for value, label in choices
    ]
    rows.append([InlineKeyboardButton("‹ Назад", callback_data="settings:back")])
    return live_markup(InlineKeyboardMarkup(rows))


def status_text(chat_id):
    selected = model_router().resolve(chat_id, "chat")
    key_source = "отдельный" if managed_api_key(chat_id) else ("личный" if api_key_status(chat_id) else "общий")
    return ("<b>Noema активна</b>\n"
            f"Model: <code>{html.escape(selected['primary'])}</code>\n"
            f"Vision: <code>{html.escape(vision_models_for(chat_id)[0])}</code>\n"
            f"Ключ для AI: {key_source}\n"
            f"Mode: {html.escape(get_mode(chat_id))}\n"
            f"Timezone: {html.escape(timezone_name_for(chat_id))}")



def set_briefing(chat_id,enabled,time_="08:30",city="",topics=""):

    with conn() as c:

        old=c.execute("SELECT * FROM briefings WHERE chat_id=?",(chat_id,)).fetchone()

        use_city=city or (old["city"] if old else "") or DEFAULT_CITY

        use_topics=topics or (old["topics"] if old else "") or "главные новости мира"

        c.execute("""INSERT INTO briefings(chat_id,enabled,time,city,topics,last_sent_date)

        VALUES(?,?,?,?,?,?) ON CONFLICT(chat_id) DO UPDATE SET enabled=excluded.enabled,time=excluded.time,city=excluded.city,topics=excluded.topics""",

        (chat_id,1 if enabled else 0,time_,use_city,use_topics,old["last_sent_date"] if old else ""))

    return {"enabled":enabled,"time":time_,"city":use_city,"topics":use_topics}


def set_briefing_preferences(chat_id, city="", topics="", time="", enabled=None):
    """Persist only the supported, user-visible briefing choices."""
    with conn() as c:
        old = c.execute("SELECT * FROM briefings WHERE chat_id=?", (chat_id,)).fetchone()
    old = dict(old) if old else {}
    use_time = time or old.get("time") or "08:30"
    if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", use_time):
        return {"ok": False, "tool": "set_briefing_preferences", "error": "time_format"}
    result = set_briefing(chat_id, old.get("enabled", False) if enabled is None else bool(enabled),
                          use_time, city or old.get("city", ""), topics or old.get("topics", ""))
    return {"ok": True, "tool": "set_briefing_preferences", **result}



def build_briefing(chat_id):

    with conn() as c:

        cfg=c.execute("SELECT * FROM briefings WHERE chat_id=?",(chat_id,)).fetchone()

    cfg=dict(cfg) if cfg else {"city":DEFAULT_CITY,"topics":"главные новости, ИИ, бизнес"}

    chat_tz = timezone_for(chat_id)
    now = datetime.now(chat_tz)
    plan=get_today_plan(chat_id); weather=get_weather_live(cfg.get("city") or DEFAULT_CITY)

    topics=[x.strip() for x in (cfg.get("topics") or "главные новости").split(",") if x.strip()]

    news=[]

    for topic in topics[:3]:

        d=web_search_live(topic,3,True)

        if d.get("ok"): news += d["results"][:2]

    lines=[f"🌅 Брифинг · {now:%d.%m %H:%M}"]

    if plan["tasks"] or plan["reminders"]:

        lines.append("\n📅 Осталось на сегодня")

        for t in plan["tasks"][:5]:
            if t["status"] == "open":
                due = str(t.get("due_date") or "")
                # A date-only task remains relevant for the whole day.  A task
                # with an exact time moves to the separate overdue section.
                timed = len(due) > 10
                try:
                    due_dt = datetime.fromisoformat(due.replace("Z", "+00:00")) if timed else None
                    if due_dt and due_dt.tzinfo is None:
                        due_dt = due_dt.replace(tzinfo=chat_tz)
                except ValueError:
                    due_dt = None
                is_old_date = bool(due) and due[:10] < now.date().isoformat()
                if (not due_dt or due_dt.astimezone(chat_tz) >= now) and not is_old_date:
                    time_label = f' · {due_dt.astimezone(chat_tz):%H:%M}' if due_dt else ""
                    lines.append("• "+html.escape(t["text"]) + time_label)

        for r in plan["reminders"][:5]: lines.append(f'• {r["time"]} — {html.escape(r["text"])}')

        overdue = []
        for t in plan["tasks"]:
            if t["status"] != "open" or not str(t.get("due_date") or ""):
                continue
            try:
                due_dt = datetime.fromisoformat(str(t["due_date"]).replace("Z", "+00:00"))
                if due_dt.tzinfo is None:
                    due_dt = due_dt.replace(tzinfo=chat_tz)
                if due_dt.astimezone(chat_tz) < now:
                    overdue.append(t)
            except ValueError:
                pass
        if overdue:
            lines.append("\n⚠️ Не закрыто")
            for t in overdue[:3]:
                lines.append("• " + html.escape(t["text"]))

    if weather.get("ok"):

        c=weather["current"]
        lines.append(f'\n🌤 {html.escape(str(weather["city"]))}: {c["temperature"]}°C, {html.escape(str(c["condition"]))}.')

    if news:

        lines.append("\n📰 Главное")

        for n in news[:5]:

            title = html.escape(n.get("title", "Главная новость"))
            url = (n.get("url") or "").strip()
            # The headline itself is the link, so the briefing remains compact.
            if url.startswith(("https://", "http://")):
                lines.append(f'• <a href="{html.escape(url, quote=True)}">{title}</a>')
            else:
                lines.append("• " + title)
            summary = html.escape((n.get("snippet") or "").strip()[:210])
            if summary:
                lines.append("  " + summary)

    return "\n".join(lines)



async def text_handler(update,context):

    t=update.effective_message.text.strip(); cid=update.effective_chat.id
    register_bot_user(cid, getattr(update, "effective_user", None))

    async def consume_menu_tap():
        """Reply-keyboard taps cannot carry a custom-emoji entity.

        Telegram only sends their plain text back to a bot.  Remove that
        technical message in a private chat; the resulting Noema screen and
        its controls carry the animated icon instead.
        """
        with contextlib.suppress(Exception):
            await update.effective_message.delete()

    emoji_slot = context.user_data.pop("awaiting_interface_emoji", "")
    if emoji_slot:
        entity = next((item for item in (update.effective_message.entities or [])
                       if item.type == MessageEntity.CUSTOM_EMOJI and item.custom_emoji_id), None)
        if not entity:
            context.user_data["awaiting_interface_emoji"] = emoji_slot
            return await update.effective_message.reply_text("Нужен именно живой эмодзи из Premium-панели. Попробуй ещё раз.")
        key = f"task_{emoji_slot}_custom_emoji_id" if emoji_slot in {"open", "done", "failed"} else f"interface_{emoji_slot}_custom_emoji_id"
        set_app_setting(key, entity.custom_emoji_id)
        with contextlib.suppress(Exception):
            await update.effective_message.delete()
        text, markup = emoji_palette_page()
        return await refresh_active_ui(update, context, text, markup)

    rule_id = context.user_data.pop("awaiting_rule_edit", None)
    if rule_id is not None:
        result = update_behavior_rule(cid, int(rule_id), t)
        if not result.get("ok"):
            return await update.effective_message.reply_text("Не удалось обновить правило.")
        with contextlib.suppress(Exception):
            await update.effective_message.delete()
        text, markup = rules_page(cid)
        return await refresh_active_ui(update, context, text, markup)

    if context.user_data.pop("awaiting_task_text", False):
        raw = t.strip()
        chat_tz = timezone_for(cid)
        due_date = datetime.now(chat_tz).date().isoformat()
        match = re.match(r"^(\d{1,2}\.\d{1,2}(?:\.\d{2,4})?)\s*[—–-]\s*(.+)$", raw)
        if match:
            date_text, raw = match.groups()
            try:
                if len(date_text) == 5:
                    due_date = datetime.strptime(date_text + f".{datetime.now(chat_tz).year}", "%d.%m.%Y").date().isoformat()
                else:
                    due_date = datetime.strptime(date_text, "%d.%m.%Y" if len(date_text) == 10 else "%d.%m.%y").date().isoformat()
            except ValueError:
                return await update.effective_message.reply_text("Не поняла дату. Пример: <code>12.09 — позвонить врачу</code>.", parse_mode="HTML")
        if not raw:
            return await update.effective_message.reply_text("Напишите текст задачи.")
        add_task(cid, raw, due_date)
        with contextlib.suppress(Exception):
            await update.effective_message.delete()
        text, markup = tasks_page(cid)
        return await refresh_active_ui(update, context, text, markup)

    if context.user_data.pop("awaiting_personal_api_key", False):
        if not t.startswith("sk-or-") or len(t) < 24:
            return await update.effective_message.reply_text("Это не похоже на ключ OpenRouter. Попробуйте ещё раз или откройте «Настройки → API-ключи».")
        ok, result = save_user_api_key(cid, t)
        with contextlib.suppress(Exception):
            await update.effective_message.delete()
        if not ok:
            return await update.effective_message.reply_text("Не удалось включить личный ключ: администратор не настроил master key.")
        text, markup = api_keys_page(cid)
        return await refresh_active_ui(update, context, text, markup)

    vision_scope = context.user_data.pop("awaiting_vision_model", "")
    if vision_scope:
        model = t.strip()
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9_.:-]+", model):
            return await update.effective_message.reply_text(
                "Не похож на ID модели. Формат: <провайдер>/<модель>, например <code>google/gemini-2.5-flash</code>.",
                parse_mode="HTML")
        if vision_scope == "shared":
            if cid not in ADMIN_CHAT_IDS:
                return await update.effective_message.reply_text("Нет доступа к общей Vision-модели.")
            set_shared_vision_model(model)
            return await update.effective_message.reply_text(
                f"👁 Общая Vision-модель изменена: <code>{html.escape(model)}</code>.", parse_mode="HTML", reply_markup=settings_keyboard(cid))
        if not has_personal_api_key(cid):
            return await update.effective_message.reply_text("Сначала подключите личный API-ключ — тогда Vision будет расходоваться только с него.")
        model_router().set_vision(cid, model)
        return await update.effective_message.reply_text(
            f"👁 Личная Vision-модель изменена: <code>{html.escape(model)}</code>.", parse_mode="HTML", reply_markup=settings_keyboard(cid))

    if context.user_data.pop("awaiting_budget_range", False):
        dates = re.findall(r"\d{1,2}\.\d{1,2}\.\d{2,4}", t)
        if not dates:
            return await update.effective_message.reply_text("Не поняла период. Пример: 01.09.2026–07.09.2026.")
        try:
            parsed = [datetime.strptime(value, "%d.%m.%Y" if len(value) == 10 else "%d.%m.%y").date() for value in dates[:2]]
        except ValueError:
            return await update.effective_message.reply_text("Не поняла дату. Пример: 01.09.2026–07.09.2026.")
        date_from = min(parsed).isoformat(); date_to = max(parsed).isoformat()
        text, markup = budget_page(cid, date_from, date_to)
        with contextlib.suppress(Exception):
            await update.effective_message.delete()
        return await refresh_active_ui(update, context, text, markup)

    if context.user_data.pop("awaiting_plan_date", False):
        parsed = None
        for fmt in ("%d.%m.%Y", "%d.%m.%y", "%Y-%m-%d"):
            try:
                parsed = datetime.strptime(t, fmt).date()
                break
            except ValueError:
                pass
        if not parsed:
            return await update.effective_message.reply_text("Не поняла дату. Пример: 15.09.2026.")
        with contextlib.suppress(Exception):
            await update.effective_message.delete()
        text, markup = plan_page(cid, parsed.isoformat())
        return await refresh_active_ui(update, context, text, markup)

    if context.user_data.pop("awaiting_model", False):
        model = t.strip()
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9_.:-]+", model):
            return await update.effective_message.reply_text(
                "Не похож на ID модели. Формат: <провайдер>/<модель>, например deepseek/deepseek-v4-flash-0731.")
        set_chat_model(cid, model, enabled=True)
        return await update.effective_message.reply_text(
            f"Добавила модель: {model}\nОткройте «⚙️ Настройки → 🧠 Модель» и выберите её.",
            reply_markup=settings_keyboard(cid))

    if t in ("⚙️ Настройки", "Настройки"):
        await consume_menu_tap()
        return await replace_active_ui(update, context, "⚙️ Настройки", settings_keyboard(cid))

    if t in ("☰ Ещё", "Ещё"):
        await consume_menu_tap()
        text, markup = more_page()
        return await replace_active_ui(update, context, text, markup)

    if t=="📚 Знания":
        return await update.effective_message.reply_text("📚 Знания\nНапишите, что найти: проект, человека, ресурс или тему. Например: «где Узел задеплоен?»")

    if t=="🎙 Голос": set_mode(cid,"voice"); return await update.effective_message.reply_text("Режим: голос.")

    if t=="💬 Текст": set_mode(cid,"text"); return await update.effective_message.reply_text("Режим: текст.")

    if t=="🔊 Голос+текст": set_mode(cid,"voice_and_text"); return await update.effective_message.reply_text("Режим: голос + текст.")

    if t in ("📅 Сегодня", "Сегодня"):
        await consume_menu_tap()
        return await today_plan(update,context)

    if t=="⏰ Напоминания":
        await consume_menu_tap()
        return await reminders(update,context)

    if t=="📝 Заметки":
        await consume_menu_tap()
        return await notes(update,context)

    if t=="👥 Люди":
        await consume_menu_tap()
        return await list_people(update,context)

    if t in ("💰 Расходы", "💳 Бюджет"):
        await consume_menu_tap()
        return await list_expenses(update,context)

    if t in ("🌅 Брифинг", "Брифинг"):
        await consume_menu_tap()
        return await update.effective_message.reply_text(live_ui_text(build_briefing(cid)), parse_mode="HTML")

    if t=="🔎 Поиск": return await update.effective_message.reply_text("Напиши: «Найди в интернете ...»")

    if t=="🛍 Товары": return await update.effective_message.reply_text("Напиши: «Хочу купить вазу до 2000 ₽»")

    if t=="⚙️ Статус":

        return await update.effective_message.reply_text(
            status_text(cid), parse_mode="HTML"
        )

    if t=="🧹 Очистить диалог":

        clear_history(cid); return await update.effective_message.reply_text("Контекст очищен. Сохранённые данные не удалены.")



    # simple briefing parser

    low=t.lower()

    if "каждое утро" in low and "бриф" in low:

        m=re.search(r"(\d{1,2}):(\d{2})",t)

        time_=f"{int(m.group(1)):02d}:{m.group(2)}" if m else "08:30"

        city=DEFAULT_CITY

        cm=re.search(r"город\s+([А-ЯA-ZЁ][^,.]+)",t,re.I)

        if cm: city=cm.group(1).strip()

        tm=re.search(r"тем[ыа]\s*[:\-]?\s*(.+)$",t,re.I)

        topics=tm.group(1).strip() if tm else "главные новости, ИИ, бизнес"

        cfg=set_briefing(cid,True,time_,city,topics)

        return await update.effective_message.reply_text(f'Ежедневный брифинг включён на {cfg["time"]}. Город: {cfg["city"]}.')

    if "отключи" in low and "бриф" in low:

        set_briefing(cid,False); return await update.effective_message.reply_text("Ежедневный брифинг отключён.")



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



    if TELEGRAM_DRAFT_STREAMING_ENABLED and update.effective_chat.type == "private":
        try:
            completed = await stream_answer_to_telegram(update, context, t)
            if completed:
                await drain_media_outbox(update, context)
        except Exception as e:
            await safe_error(update, e)
        return

    activity = await begin_activity(update.effective_message, activity_labels(t))
    try:
        a=await asyncio.to_thread(ask,cid,t)
        await send_answer(update, a, context=context, voice_in=False, force_voice=wants_voice(t))
        await drain_media_outbox(update, context)
    except Exception as e:
        await safe_error(update,e)
    finally:
        await end_activity(*activity)



async def voice_handler(update,context):

    register_bot_user(update.effective_chat.id, getattr(update, "effective_user", None))

    fd,n=tempfile.mkstemp(suffix=".ogg"); os.close(fd); p=Path(n)

    activity = await begin_activity(update.effective_message, ["🎙 Расшифровываю голос…", "🧠 Думаю…", "✍️ Готовлю ответ…"])
    try:
        f=await context.bot.get_file(update.effective_message.voice.file_id); await f.download_to_drive(custom_path=str(p))
        txt=await asyncio.to_thread(transcribe, update.effective_chat.id, p)
        a=await asyncio.to_thread(ask,update.effective_chat.id,txt)
        await send_answer(update, a, context=context, voice_in=True, force_voice=wants_voice(txt))
        await drain_media_outbox(update, context)
    except Exception as e:
        await safe_error(update,e)
    finally:
        await end_activity(*activity)
        p.unlink(missing_ok=True)



async def image_handler(update,context):

    cid=update.effective_chat.id
    register_bot_user(cid, getattr(update, "effective_user", None))

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

        status = "🧠 Расшифровываю изображение…"
        rendered_status = live_ui_text(status)
        try:
            status_msg=await msg.reply_text(rendered_status, parse_mode="HTML")
        except TypeError:  # lightweight test/message adapters without kwargs
            status_msg=await msg.reply_text(status)

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

            result=await asyncio.to_thread(get_ingestion_pipeline(cid).ingest, inp)

        except Exception:

            await status_msg.edit_text("Не удалось проанализировать изображение. Попробуй другое.")

            return

        if not result.ok:

            await status_msg.edit_text("Не удалось сохранить изображение. Попробуй другое.")

            return

        pre=result.reply or "Готово."

        final_text=pre

        # Preserve a compact representation of the shared material in the
        # dialogue, so any later follow-up refers to it without phrase-specific
        # matching or a special-case workflow.
        inquiry = build_inquiry_input(result)
        if inquiry:
            add_message(cid, "user", inquiry)
            add_message(cid, "assistant", pre)

        q=caption.strip().lower()

        if q.endswith("?") or any(w in q for w in ("что","какой","какая","какие","какое","сколько","написано","опиши","расскажи","покажи")):

            if inquiry:

                try:

                    status = "🧠 Изучаю материал и готовлю ответ…"
                    await status_msg.edit_text(live_ui_text(status), parse_mode="HTML")
                    model_ans=await asyncio.to_thread(ask,cid,inquiry)

                    if model_ans and model_ans not in (pre,"Готово."):

                        final_text=pre+"\n\n"+model_ans

                except Exception as e: await safe_error(update,e)

        chunks = TelegramRenderer.chunks(final_text or "Готово.")
        try:
            rendered, _ = animate_configured_emojis(chunks[0], reply_emoji_limit(len(chunks[0])))
            await status_msg.edit_text(rendered, parse_mode=TelegramRenderer.parse_mode)
        except TypeError:  # lightweight test/message adapters without kwargs
            await status_msg.edit_text(chunks[0])
        for chunk in chunks[1:]:
            rendered, _ = animate_configured_emojis(chunk, reply_emoji_limit(len(chunk)))
            await msg.reply_text(rendered, parse_mode=TelegramRenderer.parse_mode)

        await drain_media_outbox(update, context)


    except Exception as e: await safe_error(update,e)

    finally: p.unlink(missing_ok=True)



def due_reminder_rows(now_iso):
    with conn() as c:
        initial = [dict(row) for row in c.execute("SELECT id,chat_id,text FROM reminders WHERE sent=0 AND remind_at_utc<=? ORDER BY remind_at_utc LIMIT 50", (now_iso,)).fetchall()]
        followups = [dict(row) for row in c.execute("""SELECT id,chat_id,text,followup_count FROM reminders
                                      WHERE sent=1 AND acknowledged=0 AND followup_count<3
                                      AND next_followup_at<>'' AND next_followup_at<=?
                                      ORDER BY next_followup_at LIMIT 50""", (now_iso,)).fetchall()]
    return initial, followups


def mark_reminder_delivered(reminder_id, message_id, is_followup):
    next_at = (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat()
    with conn() as c:
        c.execute("UPDATE reminders SET sent=1, followup_count=followup_count+?, next_followup_at=?, last_sent_message_id=? WHERE id=?",
                  (1 if is_followup else 0, next_at, message_id, reminder_id))


def mark_reminder_unavailable(row, is_followup):
    set_app_setting(f'telegram_destination_unavailable:{row["chat_id"]}', datetime.now(timezone.utc).isoformat())
    with conn() as c:
        if is_followup:
            c.execute("UPDATE reminders SET acknowledged=1,next_followup_at='' WHERE id=?", (row["id"],))
        else:
            c.execute("UPDATE reminders SET sent=1,next_followup_at='' WHERE id=?", (row["id"],))


def reminder_delivery_payload(row, is_followup):
    prefix = "🔁 Напоминаю ещё раз: " if is_followup else "⏰ Напоминание: "
    return (
        live_ui_text(prefix + row["text"]),
        live_markup(InlineKeyboardMarkup([[
            interface_inline_button("reminders", "⏰", "Выполнено", f"ackrem:{row['id']}")
        ]])),
    )


async def reminder_tick(context):
    started = time.perf_counter()
    now_iso = datetime.now(timezone.utc).isoformat()
    initial, followups = await asyncio.to_thread(due_reminder_rows, now_iso)
    semaphore = asyncio.Semaphore(4)

    async def deliver(row, is_followup=False):
        async with semaphore:
            rendered_text, rendered_markup = await asyncio.to_thread(reminder_delivery_payload, row, is_followup)
            sent_message = await telegram_send_with_retry(
                context.bot, source="reminder_followup" if is_followup else "reminder",
                chat_id=row["chat_id"], text=rendered_text, parse_mode="HTML",
                reply_markup=rendered_markup,
            )
            await asyncio.to_thread(mark_reminder_delivered, row["id"], sent_message.message_id, is_followup)

    async def guarded_deliver(row, is_followup=False):
        try:
            await deliver(row, is_followup)
        except Forbidden:
            await asyncio.to_thread(mark_reminder_unavailable, row, is_followup)
            LOGGER.warning("Telegram destination unavailable chat_id=%s source=%s", row["chat_id"], "reminder_followup" if is_followup else "reminder")
        except (TimedOut, NetworkError):
            LOGGER.warning("Reminder delivery deferred chat_id=%s source=%s", row["chat_id"], "reminder_followup" if is_followup else "reminder")
        except Exception:
            LOGGER.exception("Reminder delivery failed chat_id=%s", row["chat_id"])

    await asyncio.gather(
        *(guarded_deliver(row, False) for row in initial),
        *(guarded_deliver(row, True) for row in followups),
    )
    record_runtime_metric("reminder_tick_ms", (time.perf_counter() - started) * 1000, delivered=len(initial) + len(followups))



def initialize_briefing(chat_id):
    """Persist first-run scheduling so restarts and repeated /start are harmless."""
    key = f"briefing_welcome:{chat_id}"
    if app_setting(key):
        return
    with conn() as c:
        existing = c.execute("SELECT 1 FROM briefings WHERE chat_id=?", (chat_id,)).fetchone()
    if existing:
        set_app_setting(key, "done")
        return
    set_briefing(chat_id, True)
    set_app_setting(key, str(int(time.time()) + 150))


async def briefing_tick(context):
    def load_enabled():
        with conn() as c:
            return [dict(row) for row in c.execute("SELECT * FROM briefings WHERE enabled=1").fetchall()]

    rows = await asyncio.to_thread(load_enabled)

    for row in rows:

        cfg=row
        now = datetime.now(timezone_for(cfg["chat_id"])); today = now.date().isoformat()

        welcome_key = f'briefing_welcome:{cfg["chat_id"]}'
        welcome = app_setting(welcome_key)
        first = welcome.isdigit()
        if first and time.time() < int(welcome):
            continue

        if not first and cfg.get("last_sent_date")==today: continue

        if not first and now.strftime("%H:%M") < (cfg.get("time") or "08:30"): continue

        try:

            text=await asyncio.to_thread(build_briefing,cfg["chat_id"])
            if first:
                text += ("\n\nЭто твой первый брифинг. Если что-то не подходит, смело поменяем: "
                         "скажи, какие темы тебе интересны и во сколько присылать. "
                         f"Пока буду приходить в {cfg.get('time') or '08:30'} по твоему часовому поясу. "
                         "Рассылку можно отключить в любой момент.")

            rendered_text = await asyncio.to_thread(live_ui_text, text)
            await telegram_send_with_retry(
                context.bot, source="briefing", chat_id=cfg["chat_id"],
                text=rendered_text, parse_mode="HTML",
            )

            def mark_sent():
                with conn() as c:
                    c.execute("UPDATE briefings SET last_sent_date=? WHERE chat_id=?", (today, cfg["chat_id"]))
            await asyncio.to_thread(mark_sent)
            if first:
                set_app_setting(welcome_key, "done")

        except Forbidden:
            set_app_setting(f'telegram_destination_unavailable:{cfg["chat_id"]}', datetime.now(timezone.utc).isoformat())
            set_briefing_preferences(cfg["chat_id"], enabled=False)
            LOGGER.warning("Telegram destination unavailable chat_id=%s source=briefing", cfg["chat_id"])
        except (TimedOut, NetworkError):
            LOGGER.warning("Briefing delivery deferred chat_id=%s", cfg["chat_id"])
        except Exception:
            LOGGER.exception("Briefing delivery failed chat_id=%s", cfg["chat_id"])


async def event_loop_lag_tick(context):
    loop = asyncio.get_running_loop()
    expected = context.job.data.get("expected", loop.time())
    lag_ms = max(0.0, (loop.time() - expected) * 1000)
    context.job.data["expected"] = loop.time() + 1.0
    record_runtime_metric("event_loop_lag_ms", lag_ms)
    if lag_ms > 500:
        LOGGER.warning("telemetry event_loop_lag_ms=%.1f", lag_ms)



def quick_action_result(chat_id, action, payload):
    if action == "note":
        text = str(payload.get("text") or "").strip()
        if not text:
            return False, "Не получила текст с iPhone."
        # A voice shortcut is a normal Noema message, not an automatic note.
        # The assistant decides from the spoken phrase whether to answer, save a
        # note, create a task, reminder, budget item, and so on.
        return True, ask(chat_id, text)
    if action == "complete_next":
        plan = get_today_plan(chat_id)
        task = next((item for item in plan["tasks"] if item["status"] == "open"), None)
        if not task:
            return False, "✅ На сегодня нет невыполненных задач."
        set_task_status(chat_id, task["id"], "done")
        return True, f'✅ Выполнено: {task["text"]}'
    if action == "today":
        plan = get_today_plan(chat_id)
        open_tasks = sum(1 for item in plan["tasks"] if item["status"] == "open")
        open_reminders = sum(1 for item in plan["reminders"] if not item["acknowledged"])
        return True, f"📅 На сегодня: задач — {open_tasks}, напоминаний — {open_reminders}."
    if action == "break":
        save_reminder(chat_id, "Вернуться к делам после перерыва", (datetime.now(timezone_for(chat_id)) + timedelta(minutes=10)).isoformat())
        return True, "🧘 Перерыв отмечен. Напомню вернуться к делам через 10 минут."
    return False, "Неизвестное быстрое действие."


def extracted_pdf_text(path):
    try:
        reader = PdfReader(str(path))
        return "\n".join((page.extract_text() or "") for page in reader.pages[:30])[:24000]
    except Exception:
        return ""


def ingest_from_iphone(chat_id, text="", attachment=None):
    """Persist Share Sheet material through the same knowledge pipeline as Telegram."""
    safe_text = str(text or "").strip()[:30000]
    attachments = [attachment] if attachment else []
    if attachment and (attachment.mime_type or "").lower() == "application/pdf":
        pdf_text = extracted_pdf_text(attachment.local_path)
        if pdf_text:
            safe_text = (safe_text + "\n\nТекст PDF:\n" + pdf_text).strip()
    if not safe_text:
        safe_text = "Материал отправлен с iPhone"
    inp = IngestionInput(
        chat_id=chat_id,
        message_id=time.time_ns(),
        user_text=safe_text,
        attachments=attachments,
        timestamp=datetime.now(TZ),
        conversation_context="",
        reply_to_text="",
    )
    return get_ingestion_pipeline(chat_id).ingest(inp)


async def send_quick_action_feedback(request, chat_id, ok, message):
    try:
        telegram_app = request.app["telegram_app"]
        rendered_text = await asyncio.to_thread(live_ui_text, message)
        sent = await telegram_send_with_retry(
            telegram_app.bot, source="quick_action_feedback",
            chat_id=chat_id, text=rendered_text, parse_mode="HTML")
        if is_ephemeral_confirmation(message):
            schedule_ephemeral_delete(telegram_app, sent)
    except Exception:
        return web.json_response({"ok": False, "error": "telegram_delivery_failed"}, status=502)
    return web.json_response({"ok": ok, "message": message})


async def show_iphone_input(request, chat_id, text="", attachment=None):
    """Make iPhone activity visible in Telegram before Noema handles it."""
    bot = request.app["telegram_app"].bot
    if attachment and attachment.local_path:
        caption = live_ui_text("📤 <b>С iPhone</b>")
        try:
            with open(attachment.local_path, "rb") as stream:
                if (attachment.mime_type or "").lower().startswith("image/"):
                    await bot.send_photo(chat_id=chat_id, photo=stream, caption=caption, parse_mode="HTML")
                else:
                    await bot.send_document(chat_id=chat_id, document=stream, caption=caption, parse_mode="HTML")
            return
        except Exception:
            LOGGER.warning("Unable to mirror iPhone attachment into chat %s", chat_id)
    visible = str(text or "").strip()
    if visible:
        rendered_text = await asyncio.to_thread(
            live_ui_text, f"🎙 <b>С iPhone</b>\n{html.escape(visible[:3800])}")
        await telegram_send_with_retry(
            bot, source="iphone_input", chat_id=chat_id,
            text=rendered_text, parse_mode="HTML")


async def quick_actions_run(request):
    try:
        payload = await request.json()
    except Exception:
        return web.json_response({"ok": False, "error": "invalid_json"}, status=400)
    token_parts = parse_quick_action_token(payload.get("token"))
    if token_parts:
        device_id, trigger, secret = token_parts
    else:
        # Keep already configured Shortcuts functional while migrating to the
        # simpler one-token format.
        device_id = str(payload.get("device_id") or "")
        secret = str(payload.get("secret") or "")
        trigger = str(payload.get("trigger") or "")
    if not device_id or not secret or trigger not in ("action", "double", "triple", "share"):
        return web.json_response({"ok": False, "error": "invalid_request"}, status=400)
    if trigger == "share":
        device, error = authenticate_quick_token(payload.get("token"), {"share"})
        if not device:
            return web.json_response({"ok": False, "error": error}, status=401)
        content = payload.get("text") or payload.get("content") or ""
        with contextlib.suppress(Exception):
            await show_iphone_input(request, device["chat_id"], content)
        result = await asyncio.to_thread(ingest_from_iphone, device["chat_id"], content)
        message = result.reply if result.ok else "Не удалось сохранить материал с iPhone."
        return await send_quick_action_feedback(request, device["chat_id"], bool(result.ok), message)
    with conn() as c:
        device = c.execute("SELECT * FROM quick_action_devices WHERE id=? AND active=1", (device_id,)).fetchone()
        if not device or not secrets.compare_digest(device["secret_hash"], hashlib.sha256(secret.encode()).hexdigest()):
            return web.json_response({"ok": False, "error": "unauthorized"}, status=401)
        binding = c.execute("SELECT action FROM quick_action_bindings WHERE device_id=? AND trigger=?", (device_id, trigger)).fetchone()
        if not binding:
            return web.json_response({"ok": False, "error": "not_configured"}, status=409)
        c.execute("UPDATE quick_action_devices SET last_used_at=? WHERE id=?", (datetime.now(timezone.utc).isoformat(), device_id))
    if binding["action"] == "note":
        with contextlib.suppress(Exception):
            await show_iphone_input(request, device["chat_id"], payload.get("text") or payload.get("content") or "")
    ok, message = await asyncio.to_thread(quick_action_result, device["chat_id"], binding["action"], payload)
    return await send_quick_action_feedback(request, device["chat_id"], ok, message)


async def quick_actions_upload(request):
    try:
        form = await request.post()
    except Exception:
        return web.json_response({"ok": False, "error": "invalid_form"}, status=400)
    device, error = authenticate_quick_token(form.get("token"), {"share", "screen"})
    if not device:
        return web.json_response({"ok": False, "error": error}, status=401)
    upload = form.get("file")
    content = str(form.get("text") or form.get("content") or "").strip()
    attachment = None
    temp_path = None
    try:
        if upload and getattr(upload, "file", None):
            original_name = Path(upload.filename or "iphone_attachment").name[:180]
            mime = (upload.headers.get("Content-Type") or mimetypes.guess_type(original_name)[0] or "application/octet-stream").split(";", 1)[0]
            suffix = Path(original_name).suffix[:16] or ".bin"
            fd, raw_path = tempfile.mkstemp(suffix=suffix)
            temp_path = Path(raw_path)
            with os.fdopen(fd, "wb") as dst:
                shutil.copyfileobj(upload.file, dst)
            if temp_path.stat().st_size > MAX_FILE_MB * 1024 * 1024:
                return web.json_response({"ok": False, "error": "file_too_large"}, status=413)
            kind = "image" if mime.startswith("image/") else "document"
            attachment = Attachment(file_id="", local_path=str(temp_path), mime_type=mime, kind=kind, original_name=original_name)
            if not content:
                content = f"Материал с iPhone: {original_name}"
        if not attachment and not content:
            return web.json_response({"ok": False, "error": "empty_share"}, status=400)
        with contextlib.suppress(Exception):
            await show_iphone_input(request, device["chat_id"], content, attachment)
        result = await asyncio.to_thread(ingest_from_iphone, device["chat_id"], content, attachment)
        message = result.reply if result.ok else "Не удалось сохранить материал с iPhone."
        return await send_quick_action_feedback(request, device["chat_id"], bool(result.ok), message)
    finally:
        if temp_path:
            temp_path.unlink(missing_ok=True)


async def health_check(request):
    return web.json_response({"ok": True, "build": BUILD_ID})


def valid_webapp_user(init_data):
    """Return the Telegram Web App user only when Telegram signed the payload."""
    try:
        pairs = dict(parse_qsl(str(init_data or ""), keep_blank_values=True))
        received_hash = pairs.pop("hash")
        check = "\n".join(f"{key}={pairs[key]}" for key in sorted(pairs))
        secret = hmac.new(b"WebAppData", TG.encode(), hashlib.sha256).digest()
        expected = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(received_hash, expected):
            return None
        auth_date = int(pairs.get("auth_date") or 0)
        if auth_date < int(time.time()) - 86400:
            return None
        return json.loads(pairs.get("user") or "{}")
    except Exception:
        return None


async def timezone_page(request):
    return web.Response(text="""<!doctype html><html lang=\"ru\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\"><script src=\"https://telegram.org/js/telegram-web-app.js\"></script><style>body{font:17px -apple-system,BlinkMacSystemFont,sans-serif;background:#17212b;color:#fff;padding:32px;text-align:center}p{opacity:.8}</style><body><h3>Определяю часовой пояс…</h3><p>Это займёт секунду.</p><script>(async()=>{const app=window.Telegram&&Telegram.WebApp;const tz=Intl.DateTimeFormat().resolvedOptions().timeZone;try{const r=await fetch('/api/v1/timezone',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({init_data:app&&app.initData,timezone:tz})});const d=await r.json();document.body.innerHTML=d.ok?'<h3>✓ Часовой пояс сохранён</h3><p>'+d.timezone+'</p>':'<h3>Не удалось определить пояс</h3><p>Закройте окно и попробуйте ещё раз.</p>'}catch(e){document.body.innerHTML='<h3>Нет соединения</h3><p>Попробуйте ещё раз.</p>'}finally{app&&app.ready()}})()</script></body></html>""", content_type="text/html")


async def save_webapp_timezone(request):
    try:
        payload = await request.json()
    except Exception:
        return web.json_response({"ok": False, "error": "invalid_request"}, status=400)
    user = valid_webapp_user(payload.get("init_data"))
    if not user or not user.get("id"):
        return web.json_response({"ok": False, "error": "unauthorized"}, status=401)
    result = set_user_timezone(int(user["id"]), payload.get("timezone"))
    return web.json_response({"ok": bool(result.get("ok")), "timezone": result.get("timezone", "")}, status=200 if result.get("ok") else 400)


async def telegram_error_handler(update, context):
    """Keep unexpected callback errors visible in the operator log."""
    if isinstance(context.error, Forbidden):
        chat = getattr(update, "effective_chat", None)
        if chat:
            set_app_setting(f"telegram_destination_unavailable:{chat.id}", datetime.now(timezone.utc).isoformat())
        LOGGER.warning("Telegram destination unavailable chat_id=%s", getattr(chat, "id", None))
        return
    LOGGER.exception("Unhandled Telegram update", exc_info=context.error)


async def start_quick_actions_server(telegram_app):
    server = web.Application(client_max_size=MAX_FILE_MB * 1024 * 1024)
    server["telegram_app"] = telegram_app
    import sys
    from miniapp_api import register_miniapp
    register_miniapp(server, sys.modules[__name__])
    server.router.add_get("/healthz", health_check)
    server.router.add_get("/timezone", timezone_page)
    server.router.add_post("/api/v1/timezone", save_webapp_timezone)
    server.router.add_post("/api/v1/quick-actions/run", quick_actions_run)
    server.router.add_post("/api/v1/quick-actions/upload", quick_actions_upload)
    runner = web.AppRunner(server)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", int(os.getenv("NOEMA_QUICK_PORT", "8080")))
    await site.start()
    return runner


async def main_async():

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

    app=(Application.builder().token(TG)
         .connection_pool_size(TELEGRAM_CONNECTION_POOL_SIZE)
         .pool_timeout(TELEGRAM_POOL_TIMEOUT)
         .connect_timeout(TELEGRAM_CONNECT_TIMEOUT)
         .read_timeout(TELEGRAM_READ_TIMEOUT)
         .write_timeout(TELEGRAM_WRITE_TIMEOUT)
         .get_updates_connection_pool_size(2)
         .get_updates_pool_timeout(TELEGRAM_POOL_TIMEOUT)
         .get_updates_connect_timeout(TELEGRAM_CONNECT_TIMEOUT)
         .get_updates_read_timeout(30)
         .get_updates_write_timeout(TELEGRAM_WRITE_TIMEOUT)
         .build())

    app.add_error_handler(telegram_error_handler)

    # PTB 22.8 preserves the newest Bot API stop event in Update.api_kwargs.
    # A separate group lets normal message handlers continue unchanged.
    app.add_handler(TypeHandler(Update, stopped_generation_handler), group=-1)

    app.add_handler(CommandHandler("start",start))

    # Measurement-only admin controls. They never expose message, audio,
    # identity, or secret material and do not touch persistent user data.
    app.add_handler(CommandHandler("telemetry_reset", telemetry_reset_command))
    app.add_handler(CommandHandler("telemetry_status", telemetry_status_command))
    app.add_handler(CommandHandler("telemetry_report", telemetry_report_command))

    app.add_handler(CommandHandler("todayemoji", set_today_emoji))

    app.add_handler(CommandHandler("setemoji", set_interface_emoji))

    app.add_handler(CommandHandler("emojihelp", emoji_help))

    app.add_handler(CommandHandler("replyemoji", add_reply_emoji))

    app.add_handler(CommandHandler("clearreplyemojis", clear_reply_emojis))

    # Long-running voice/image work and a slow Telegram send must not delay the
    # acknowledgement of the next inline button.
    app.add_handler(CallbackQueryHandler(callback, block=False))

    app.add_handler(MessageHandler(filters.VOICE,voice_handler, block=False))

    # block=False is required so a stopped_message_generation update can cancel
    # a still-running draft instead of waiting behind the text handler.
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND,text_handler, block=False))

    app.add_handler(MessageHandler(filters.PHOTO | filters.Document.IMAGE,image_handler, block=False))

    app.job_queue.run_repeating(
        reminder_tick, interval=REMINDER_TICK_SECONDS, first=2,
        job_kwargs={"max_instances": 1, "coalesce": True, "misfire_grace_time": REMINDER_TICK_SECONDS},
    )

    app.job_queue.run_repeating(briefing_tick,interval=30,first=10)
    app.job_queue.run_repeating(
        event_loop_lag_tick, interval=1, first=1,
        data={"expected": asyncio.get_running_loop().time() + 1},
        job_kwargs={"max_instances": 1, "coalesce": True, "misfire_grace_time": 5},
    )

    await app.initialize()
    await app.start()
    await app.updater.start_polling(drop_pending_updates=False)
    web_runner = await start_quick_actions_server(app)
    print("QUICK ACTIONS: HTTP server on", os.getenv("NOEMA_QUICK_PORT", "8080"))
    try:
        await asyncio.Event().wait()
    finally:
        await web_runner.cleanup()
        await app.updater.stop()
        await app.stop()
        await app.shutdown()


def main():
    asyncio.run(main_async())



if __name__=="__main__":

    main()

