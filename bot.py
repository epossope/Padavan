

import asyncio

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



BASE = Path(__file__).resolve().parent

load_dotenv(BASE / ".env")



BUILD_ID = "v8-CLEAN-2026-09-03"

TG = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()

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



DB = BASE / "noema_test.sqlite3"

CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"

STT_URL = "https://openrouter.ai/api/v1/audio/transcriptions"



KB = ReplyKeyboardMarkup([

    ["🎙 Голос", "💬 Текст", "🔊 Голос+текст"],

    ["📅 Сегодня", "⏰ Напоминания", "📝 Заметки"],

    ["👥 Люди", "💰 Расходы", "🌅 Брифинг"],

    ["🔎 Поиск", "🛍 Товары", "⚙️ Статус"],

    ["🧹 Очистить диалог"],

], resize_keyboard=True)



TOOLS = [
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

        "description":"Получить информацию о сохранённом изображении для отправки.",

        "parameters":{"type":"object","properties":{"file_id":{"type":"integer"},"kind":{"type":"string"},"limit":{"type":"integer"}}}

    }},

]



WRITE_TOOLS = {"set_reminder","save_note","add_task","person_upsert","person_interaction","add_expense","update_last_expense","delete_note","delete_expense","delete_task","delete_person","delete_interaction","delete_reminder"}



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



def save_image_to_db(chat_id, original_name, mime_type, local_path, kind, summary=None):

    b64 = base64.b64encode(Path(local_path).read_bytes()).decode() if local_path else ""

    with conn() as c:

        cur = c.execute("INSERT INTO files (chat_id, telegram_file_id, original_name, mime_type, local_path, kind, summary, extracted_text, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",

                        (chat_id, "", original_name, mime_type, local_path, kind, summary, "", datetime.now(timezone.utc).isoformat()))

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



def send_stored_image(chat_id, file_id=None, kind=None, limit=5):

    # Get the stored image from database

    get_result = get_files(chat_id=chat_id, kind=kind, limit=limit)

    if not get_result.get("ok"):

        return {"ok": False, "tool": "send_stored_image", "error": "could_not_retrieve_files"}

    

    files = get_result.get("files", [])

    if not files:

        return {"ok": False, "tool": "send_stored_image", "error": "no_files_found"}

    

    # If file_id is specified, find that specific file

    if file_id:

        target_file = next((f for f in files if f.get("id") == file_id), None)

        if not target_file:

            return {"ok": False, "tool": "send_stored_image", "error": "file_not_found"}

        files = [target_file]

    

    # Return file information for sending

    return {"ok": True, "tool": "send_stored_image", "files_info": files}



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

        "send_stored_image":send_stored_image

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

        rows=list(DDGS().news(query,max_results=n)) if news else list(DDGS().text(query,max_results=n))

    except Exception:

        return {"ok":False,"results":[]}

    out=[]

    for r in rows:

        out.append({"title":r.get("title",""),"url":r.get("url") or r.get("href") or "",

                    "snippet":r.get("body") or r.get("description") or "","source":r.get("source","")})

    return {"ok":True,"results":out}



def clean_product_query(text):

    q=text

    q=re.sub(r"(?i)\b(найди|подбери|покажи|хочу купить|где купить|купить)\b"," ",q)

    q=re.sub(r"(?i)\bдо\s*\d[\d\s]*\s*(?:₽|р|руб(?:лей)?)\b"," ",q)

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

    for label,domain in marketplaces:

        q=f'site:{domain} "{product}" купить'

        if max_price is not None: q+=f" до {max_price:g} рублей"

        if city: q+=f" {city}"

        q+=neg

        try: rows=list(DDGS().text(q,max_results=3))

        except Exception: rows=[]

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

    if any(x in t for x in ("хочу купить","где купить","найди товар","подбери","на озон","на wildberries","на вайлдберриз")):

        max_price=None

        m=re.search(r"(?:до|не дороже)\s*(\d[\d\s]*)\s*(?:₽|р|руб)",t)

        if m:

            try: max_price=float(m.group(1).replace(" ",""))

            except: pass

        city=""

        cm=re.search(r"(?:в|по)\s+(петербург(?:е)?|санкт-петербург(?:е)?|москв(?:е|а)|брянск(?:е)?)",t)

        if cm: city=cm.group(1)

        return format_links("🛍 Нашла варианты",search_products_live(text,max_price,city))

    return None



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

        "Текущие новости/погоду/курс/товары обрабатывает внешний live-router — не выдумывай их самостоятельно. "

        "Никогда не заявляй, что что-то сохранено, если tool не вернул ok=true. "

        "Не раскрывай внутренние модели, OpenRouter или провайдера. "

        "Отвечай коротко, естественно и персонально. "

        f"Сейчас {now.isoformat()}, timezone {TZ_NAME}."

    )



def request_chat(model,messages,tools=None,tool_choice="auto"):

    payload={"model":model,"messages":messages,"temperature":0.25}

    if tools: payload["tools"]=tools; payload["tool_choice"]=tool_choice

    return requests.post(CHAT_URL,headers={"Authorization":f"Bearer {OR_KEY}","Content-Type":"application/json"},

                         json=payload,timeout=180)



def call_or(messages,tools=None,tool_choice="auto"):

    models=[MODEL]+[m for m in FALLBACK_MODELS if m!=MODEL]

    last=None

    for model in models:

        for attempt in range(2):

            r=request_chat(model,messages,tools,tool_choice)

            if r.ok: return r.json()["choices"][0]["message"]

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

    live=direct_live_request(text)

    if live is not None:

        add_message(chat_id,"user",text); add_message(chat_id,"assistant",live); return live



    msgs=[{"role":"system","content":system_prompt()}]+history(chat_id)+[{"role":"user","content":text}]

    writes=[]

    for _ in range(5):

        tc="required" if _==0 else "auto"

        msg=call_or(msgs,TOOLS,tc)

        calls=msg.get("tool_calls") or []

        if not calls:

            ans=(msg.get("content") or "").strip() or write_confirmation(writes)

            add_message(chat_id,"user",text); add_message(chat_id,"assistant",ans); return ans

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

    if eff in ("text","voice_and_text"): await update.effective_message.reply_text(answer)

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

    if q.data.startswith("delrem:"):

        rid=int(q.data.split(":")[1])

        with conn() as c: c.execute("DELETE FROM reminders WHERE id=? AND chat_id=?",(rid,q.message.chat_id))

        await q.edit_message_text(f"Напоминание #{rid} удалено.")



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

            f"Build: {BUILD_ID}\nPID: {os.getpid()}\nPath: {BASE}\n"

            f"Model: {MODEL} (Noema Model v1)\n"

            f"Vision: {VISION_MODEL}\n"

            f"STT: {STT_MODEL}\n"

            f"Weather: Open-Meteo\n"

            f"Shopping: multi-market\nDefault city: {DEFAULT_CITY}\nMode: {get_mode(cid)}"

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



    try:

        a=await asyncio.to_thread(ask,cid,t); await send_answer(update,a,False,wants_voice(t))

    except Exception as e: await safe_error(update,e)



async def voice_handler(update,context):

    fd,n=tempfile.mkstemp(suffix=".ogg"); os.close(fd); p=Path(n)

    try:

        f=await context.bot.get_file(update.effective_message.voice.file_id); await f.download_to_drive(custom_path=str(p))

        txt=await asyncio.to_thread(transcribe,p); await update.effective_message.reply_text("🎤 "+txt)

        a=await asyncio.to_thread(ask,update.effective_chat.id,txt); await send_answer(update,a,True,wants_voice(txt))

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

        status_msg=await msg.reply_text("🧠 Анализирую изображение...")

        try:

            description=describe_image(p,mime,caption)

            # Save the image to database after successful analysis

            save_result=await asyncio.to_thread(execute_tool,cid,"save_image_to_db",{

                "chat_id":cid,

                "original_name":(msg.document or msg.photo[-1]).file_name or f"image_{datetime.now().strftime("%Y%m%d%H%M%S")}.img",

                "mime_type":mime,

                "local_path":str(p),

                "kind":"user_image",

                "summary":caption or description[:200]
            })


        except Exception:

            await status_msg.edit_text("Не удалось проанализировать изображение. Попробуй другое.")

            return

        combined=description+(f"\n\nПодпись: {caption}" if caption else "")

        answer=await asyncio.to_thread(ask,cid,combined)

        await status_msg.edit_text(answer or "Готово.")

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

    if not TG or "PASTE_" in TG: raise RuntimeError("Вставь TELEGRAM_BOT_TOKEN в .env")

    if not OR_KEY or "PASTE_" in OR_KEY: raise RuntimeError("Вставь OPENROUTER_API_KEY в .env")

    init_db()

    print("="*60)

    print("NOEMA STARTED")

    print("BUILD:", BUILD_ID)

    print("PID:", os.getpid())

    print("PATH:", BASE)

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

