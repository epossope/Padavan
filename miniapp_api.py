"""Same-origin Telegram Mini App API; all data is scoped to signed Telegram identity."""
import asyncio
import contextlib
import json
import os
import tempfile
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from aiohttp import web


def register_miniapp(app, core):
    root = Path(__file__).parent / "miniapp"
    locks = {}
    weather_cache = {}
    default_widgets = ["tasks", "next_event", "notes", "reminders", "budget", "recent_saved"]
    widget_types = set(default_widgets) | {"people"}
    client_latency_metrics = {
        "wake_ms", "stt_first_partial_ms", "stt_final_ms", "llm_ttft_ms",
        "tts_queue_wait_ms", "tts_prepare_ms", "tts_first_start_ms",
        "tts_first_chunk_ms", "tts_voice_name", "tts_engine_name",
        "speech_text_length_chars", "total_response_start_ms", "total_ms",
    }
    client_voice_robustness_metrics = {
        "barge_in_reason_code", "barge_in_duration_ms", "barge_in_peak_rms",
        "barge_in_rms", "barge_in_vad_probability", "audio_capture_sample_rate_hz",
        "stt_stream_sample_rate_hz", "vad_engine", "vad_engine_name",
        "vad_fallback_reason", "vad_fallback_reason_code",
        "noise_floor_rms", "speech_start_probability", "speech_start_rms",
        "realtime_empty_final_count", "realtime_fallback_batch_count",
        "batch_fallback_success_count", "stt_ws_connect_ms", "vosk_load_ms",
        "silero_load_ms", "get_user_media_ms", "mic_permission_ms",
        "conversation_ready_ms",
    }
    client_telemetry_metrics = client_latency_metrics | client_voice_robustness_metrics
    jobs = {}
    job_locks = {}
    jobs_lock = threading.Lock()

    def ensure_jobs_table():
        if not callable(getattr(core, "conn", None)):
            return
        with core.conn() as c:
            c.execute("""CREATE TABLE IF NOT EXISTS miniapp_jobs(
                id TEXT PRIMARY KEY, chat_id INTEGER NOT NULL, status TEXT NOT NULL,
                created_at TEXT NOT NULL, completed_at TEXT, notified_at TEXT,
                error_code TEXT NOT NULL DEFAULT ''
            )""")
            columns = {row["name"] for row in c.execute("PRAGMA table_info(miniapp_jobs)")}
            if "notified_at" not in columns:
                c.execute("ALTER TABLE miniapp_jobs ADD COLUMN notified_at TEXT")

    ensure_jobs_table()

    def persist_job(job_id, cid, status, error_code=""):
        now = datetime.now(timezone.utc).isoformat()
        completed_at = now if status in {"done", "error"} else None
        with jobs_lock:
            previous = jobs.get(job_id, {})
            jobs[job_id] = {
                "id": job_id, "chat_id": cid, "status": status,
                "created_at": previous.get("created_at", now),
                "completed_at": completed_at, "notified_at": previous.get("notified_at"),
                "error_code": error_code,
            }
        if not callable(getattr(core, "conn", None)):
            return
        with core.conn() as c:
            if status == "running":
                c.execute("INSERT INTO miniapp_jobs(id,chat_id,status,created_at,error_code) VALUES(?,?,?,?,?)",
                          (job_id, cid, status, now, ""))
            else:
                c.execute("UPDATE miniapp_jobs SET status=?,completed_at=?,error_code=? WHERE id=? AND chat_id=?",
                          (status, completed_at, error_code[:80], job_id, cid))

    def job_status(job_id, cid):
        if callable(getattr(core, "conn", None)):
            with core.conn() as c:
                row = c.execute("SELECT id,status,created_at,completed_at,error_code FROM miniapp_jobs WHERE id=? AND chat_id=?",
                                (job_id, cid)).fetchone()
            if row:
                return dict(row)
        with jobs_lock:
            job = jobs.get(job_id)
        if not job or job["chat_id"] != cid:
            raise ValueError("Запрос не найден")
        return {key: value for key, value in job.items() if key != "chat_id"}

    def claim_completion_notification(job_id, cid):
        """Reserve one generic completion notice without ever storing answer text."""
        now = datetime.now(timezone.utc).isoformat()
        if callable(getattr(core, "conn", None)):
            with core.conn() as c:
                cursor = c.execute(
                    "UPDATE miniapp_jobs SET notified_at=? WHERE id=? AND chat_id=? "
                    "AND status='done' AND (notified_at IS NULL OR notified_at='')",
                    (now, job_id, cid),
                )
            return bool(cursor.rowcount)
        with jobs_lock:
            job = jobs.get(job_id)
            if not job or job["chat_id"] != cid or job["status"] != "done" or job.get("notified_at"):
                return False
            job["notified_at"] = now
            return True

    async def notify_completion(job_id, cid):
        telegram_app = app.get("telegram_app")
        bot = getattr(telegram_app, "bot", None)
        if bot is None or not claim_completion_notification(job_id, cid):
            return
        try:
            await bot.send_message(chat_id=cid, text="Ответ готов. Открой Noema, чтобы продолжить разговор.")
        except Exception:
            core.LOGGER.warning("Mini App completion notice failed for chat %s", cid)

    def telemetry_values(args):
        values = args.get("metrics")
        if not isinstance(values, dict) or len(values) > len(client_telemetry_metrics):
            raise ValueError("Некорректная телеметрия")
        clean = {}
        for name, value in values.items():
            if name not in client_telemetry_metrics or isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError("Некорректная телеметрия")
            value = float(value)
            if not 0 <= value <= 900000:
                raise ValueError("Некорректная телеметрия")
            clean[name] = value
        return clean

    def widgets_for(cid):
        try:
            value = json.loads(core.app_setting(f"miniapp_home_widgets:{cid}", "null"))
            if isinstance(value, list) and all(isinstance(x, str) and x in widget_types for x in value) and len(set(value)) == len(value):
                return value
        except (ValueError, TypeError):
            pass
        return list(default_widgets)

    def budget_for(cid, args):
        today = datetime.now(core.timezone_for(cid)).date()
        start = str(args.get("date_from") or today.replace(day=1).isoformat())
        end = str(args.get("date_to") or today.isoformat())
        first, last = datetime.strptime(start, "%Y-%m-%d"), datetime.strptime(end, "%Y-%m-%d")
        if not 0 <= (last - first).days <= 3660:
            raise ValueError("Некорректный период")
        with core.conn() as c:
            items = [dict(row) for row in c.execute(
                "SELECT id,amount,currency,category,description,merchant,spent_at,kind FROM expenses "
                "WHERE chat_id=? AND substr(spent_at,1,10)>=? AND substr(spent_at,1,10)<=? ORDER BY spent_at DESC,id DESC",
                (cid, start, end))]
        totals = {}
        for item in items:
            total = totals.setdefault(item["currency"] or "RUB", {"income": 0, "expense": 0})
            total["income" if item["kind"] == "income" else "expense"] += abs(float(item["amount"]))
        for total in totals.values():
            total.update(income=round(total["income"], 2), expense=round(total["expense"], 2))
            total["balance"] = round(total["income"] - total["expense"], 2)
        # Do not add different currencies into a misleading combined amount.
        summary = next(iter(totals.values())) if len(totals) == 1 else {"income": 0, "expense": 0, "balance": 0}
        return {"items": items, "count": len(items), "date_from": start, "date_to": end,
                "currency_totals": totals, "mixed_currencies": len(totals) > 1, **summary}

    async def index(request):
        return web.FileResponse(root / "index.html", headers={"Cache-Control": "no-cache"})

    async def api(request):
        started = time.perf_counter()
        try:
            payload = await request.json()
            if not isinstance(payload, dict):
                raise ValueError("Некорректный запрос")
            user = core.valid_webapp_user(payload.get("init_data"))
            if not user or not isinstance(user.get("id"), int):
                raise web.HTTPUnauthorized(text="Открой приложение через Telegram.")
            cid = user["id"]
            action = payload.get("action", "state")
            args = payload.get("args", {})
            if not isinstance(args, dict):
                raise ValueError("Некорректные параметры")
            # Never accept a client-supplied owner, even inside tool arguments.
            if "chat_id" in args:
                raise ValueError("Некорректные параметры")
            if action == "state":
                result = await asyncio.to_thread(state, cid, args)
            elif action == "telemetry_record":
                # Store numeric timings only.  No user text, audio, identifiers or secrets enter telemetry.
                for name, value in telemetry_values(args).items():
                    core.record_runtime_metric(name, value)
                result = {"enabled": bool(getattr(core, "TELEMETRY_ENABLED", False))}
            elif action in {"telemetry_export", "telemetry_reset"}:
                if cid not in getattr(core, "ADMIN_CHAT_IDS", set()):
                    raise web.HTTPForbidden(text="Недостаточно прав")
                if action == "telemetry_reset":
                    core.reset_runtime_metric_series()
                    result = {"ok": True}
                else:
                    result = core.runtime_metric_export()
            elif action == "home_layout":
                widgets = args.get("widgets")
                if not isinstance(widgets, list) or len(widgets) > len(widget_types) or any(not isinstance(x, str) or x not in widget_types for x in widgets) or len(set(widgets)) != len(widgets):
                    raise ValueError("Некорректные виджеты")
                core.set_app_setting(f"miniapp_home_widgets:{cid}", json.dumps(widgets))
                result = {"widgets": widgets}
            elif action == "budget":
                result = await asyncio.to_thread(budget_for, cid, args)
            elif action == "weather":
                city = args.get("city", "")
                if not isinstance(city, str) or len(city) > 120:
                    raise ValueError("Некорректный город")
                if not city.strip():
                    with core.conn() as c:
                        cfg = c.execute("SELECT city FROM briefings WHERE chat_id=?", (cid,)).fetchone()
                    city = (cfg["city"] if cfg else "") or ""
                key = (cid, city.strip())
                cached = weather_cache.get(key)
                if cached and time.monotonic() - cached[0] < 300:
                    result = cached[1]
                else:
                    result = await asyncio.to_thread(core.get_weather, cid, city.strip())
                    if len(weather_cache) >= 256:
                        weather_cache.clear()
                    weather_cache[key] = (time.monotonic(), result)
            elif action == "archive_search":
                query = args.get("query", "")
                limit = args.get("limit", 20)
                if not isinstance(query, str) or not 1 <= len(query.strip()) <= 1000 or type(limit) is not int or not 1 <= limit <= 20:
                    raise ValueError("Некорректный поиск")
                found = await asyncio.to_thread(core.knowledge_search_tool, cid, query=query.strip(), limit=limit)
                allowed = {"id", "title", "summary", "content", "text", "category", "created_at", "updated_at", "type", "project", "tags"}
                results = [{k: v for k, v in item.items() if k in allowed} for item in found.get("results", [])]
                result = {"query": query.strip(), "count": len(results), "results": results}
            elif action == "file":
                with core.conn() as c:
                    row = c.execute("SELECT local_path,original_name FROM files WHERE chat_id=? AND id=?", (cid, int(args["id"]))).fetchone()
                if not row or not row["local_path"] or not Path(row["local_path"]).is_file():
                    raise web.HTTPNotFound(text="Файл недоступен")
                return web.FileResponse(row["local_path"], headers={"Cache-Control": "no-store", "Content-Disposition": "attachment"})
            elif action == "chat":
                text = str(args.get("text", "")).strip()
                if not text or len(text) > 12000:
                    raise ValueError("Напиши сообщение длиной до 12 000 символов")
                async with locks.setdefault(cid, asyncio.Lock()):
                    result = {"answer": await asyncio.to_thread(core.ask, cid, text)}
            elif action == "mode":
                if args.get("mode") not in {"text", "voice", "voice_and_text"}:
                    raise ValueError("Неизвестный режим")
                core.set_mode(cid, args["mode"])
                result = {"ok": True}
            elif action in {"set_experimental_realtime", "set_experimental_wake"}:
                if cid not in getattr(core, "ADMIN_CHAT_IDS", set()):
                    raise web.HTTPForbidden(text="Недостаточно прав")
                if type(args.get("enabled")) is not bool:
                    raise ValueError("Некорректный режим Beta")
                key = "miniapp_realtime_beta" if action == "set_experimental_realtime" else "miniapp_wake_enabled"
                core.set_app_setting(f"{key}:{cid}", "1" if args["enabled"] else "0")
                result = {"enabled": args["enabled"]}
            elif action in {"admin_runtime_config_set", "admin_runtime_config_reset"}:
                if cid not in getattr(core, "ADMIN_CHAT_IDS", set()):
                    raise web.HTTPForbidden(text="Недостаточно прав")
                field = args.get("field")
                if not isinstance(field, str) or len(field) > 80:
                    raise ValueError("Некорректная настройка")
                if action == "admin_runtime_config_set":
                    if "value" not in args:
                        raise ValueError("Некорректное значение")
                    result = await asyncio.to_thread(core.set_admin_runtime_config, cid, field, args["value"])
                else:
                    result = await asyncio.to_thread(core.reset_admin_runtime_config, cid, field)
            elif action == "conversation_job":
                job_id = args.get("id", "")
                try:
                    uuid.UUID(str(job_id))
                except (ValueError, TypeError, AttributeError):
                    raise ValueError("Некорректный запрос")
                result = job_status(str(job_id), cid)
            elif action == "task_toggle":
                result = core.toggle_task_status(cid, int(args["id"]))
            elif action == "clear_history":
                core.clear_history(cid)
                result = {"ok": True}
            elif action == "reminder_done":
                with core.conn() as c:
                    c.execute("UPDATE reminders SET acknowledged=1,next_followup_at='' WHERE chat_id=? AND id=?", (cid, int(args["id"])))
                result = {"ok": True}
            elif action in {"add_task", "update_task", "delete_task", "save_note", "update_note", "delete_note", "set_reminder", "update_reminder", "delete_reminder",
                            "add_expense", "add_income", "update_expense", "delete_expense", "person_upsert", "update_person", "delete_person",
                            "save_behavior_rule", "update_behavior_rule", "delete_behavior_rule",
                            "set_timezone", "set_briefing_preferences"}:
                result = await asyncio.to_thread(core.execute_tool, cid, action, args)
                if isinstance(result, dict) and result.get("ok") is False:
                    raise ValueError("Не удалось сохранить изменение. Проверь поля.")
            else:
                raise ValueError("Неизвестное действие")
            duration_ms = (time.perf_counter() - started) * 1000
            return web.json_response(
                {"ok": True, "data": result},
                headers={"Cache-Control": "no-store", "Server-Timing": f"ui_action;dur={duration_ms:.1f}"},
            )
        except web.HTTPException:
            raise
        except (ValueError, TypeError, KeyError):
            return web.json_response({"ok": False, "error": "Проверь введённые данные."}, status=400)
        except Exception:
            core.LOGGER.exception("Mini App request failed")
            return web.json_response({"ok": False, "error": "Не удалось выполнить запрос. Попробуй ещё раз."}, status=503)

    def state(cid, args):
        day = str(args.get("day") or datetime.now(core.timezone_for(cid)).date().isoformat())
        datetime.strptime(day, "%Y-%m-%d")
        with core.conn() as c:
            tasks = [dict(r) for r in c.execute("SELECT id,text,due_date,priority,status FROM tasks WHERE chat_id=? ORDER BY status,due_date,id DESC LIMIT 200", (cid,))]
            reminders = [dict(r) for r in c.execute("SELECT id,text,remind_at_utc,acknowledged FROM reminders WHERE chat_id=? ORDER BY remind_at_utc DESC LIMIT 200", (cid,))]
            cfg = c.execute("SELECT enabled,time,city,topics FROM briefings WHERE chat_id=?", (cid,)).fetchone()
        files = core.get_files(cid, limit=100)["files"]
        for item in files:
            item.pop("local_path", None)
        beta_available = cid in getattr(core, "ADMIN_CHAT_IDS", set())
        realtime_beta = wake_enabled = False
        if beta_available and callable(getattr(core, "app_setting", None)):
            realtime_beta = core.app_setting(f"miniapp_realtime_beta:{cid}", "0") == "1"
            wake_enabled = core.app_setting(f"miniapp_wake_enabled:{cid}", "0") == "1"
        admin_runtime_config = None
        if beta_available and callable(getattr(core, "runtime_config_snapshot", None)):
            admin_runtime_config = core.runtime_config_snapshot()
        voice_runtime = {}
        if callable(getattr(core, "runtime_config_values", None)):
            runtime = core.runtime_config_values()
            voice_runtime = {key: runtime[key] for key in ("tts_provider", "tts_fallback_provider", "tts_voice")}
        return {"day": day, "plan": core.get_plan_for_date(cid, day), "tasks": tasks, "reminders": reminders,
                "notes": core.get_notes(cid, 50)["notes"], "people": core.get_people(cid)["people"],
                "expenses": core.get_expenses(cid)["items"], "files": files,
                "history": core.history(cid, 50), "rules": core.behavior_rules_for(cid),
                "settings": {"timezone": core.timezone_name_for(cid), "mode": core.get_mode(cid),
                             "home_widgets": widgets_for(cid),
                             "experimental_realtime": realtime_beta,
                             "experimental_realtime_available": beta_available,
                             "experimental_wake_enabled": wake_enabled,
                             "admin_runtime_config": admin_runtime_config,
                             "voice_runtime": voice_runtime,
                             "telemetry_enabled": bool(getattr(core, "TELEMETRY_ENABLED", False)),
                             "briefing": dict(cfg) if cfg else {"enabled": False, "time": "08:30", "topics": "главные новости мира", "city": ""}}}

    async def voice(request):
        form = await request.post()
        user = core.valid_webapp_user(form.get("init_data"))
        if not user or not isinstance(user.get("id"), int):
            raise web.HTTPUnauthorized()
        upload = form.get("audio")
        if not getattr(upload, "file", None):
            raise web.HTTPBadRequest()
        suffix = ".mp4" if "mp4" in str(upload.content_type) else ".webm"
        fd, name = tempfile.mkstemp(suffix=suffix)
        try:
            with os.fdopen(fd, "wb") as output:
                content = upload.file.read(20 * 1024 * 1024 + 1)
                if len(content) > 20 * 1024 * 1024:
                    raise web.HTTPRequestEntityTooLarge(max_size=20 * 1024 * 1024, actual_size=len(content))
                output.write(content)
            cid = user["id"]
            async with locks.setdefault(cid, asyncio.Lock()):
                text = await asyncio.to_thread(core.transcribe, cid, Path(name))
                answer = await asyncio.to_thread(core.ask, cid, text)
            return web.json_response({"ok": True, "data": {"answer": answer}}, headers={"Cache-Control": "no-store"})
        except RuntimeError as exc:
            code = str(exc)
            message = "Речь не распознана. Попробуй ещё раз." if code == "STT_EMPTY" else "Распознавание временно недоступно."
            return web.json_response({"ok": False, "error": message, "code": code}, status=422 if code == "STT_EMPTY" else 503)
        finally:
            Path(name).unlink(missing_ok=True)

    async def voice_transcribe(request):
        form = await request.post()
        user = core.valid_webapp_user(form.get("init_data"))
        if not user or not isinstance(user.get("id"), int):
            raise web.HTTPUnauthorized()
        upload = form.get("audio")
        if not getattr(upload, "file", None):
            raise web.HTTPBadRequest()
        suffix = ".mp4" if "mp4" in str(upload.content_type) else ".webm"
        fd, name = tempfile.mkstemp(suffix=suffix)
        try:
            with os.fdopen(fd, "wb") as output:
                content = upload.file.read(20 * 1024 * 1024 + 1)
                if len(content) > 20 * 1024 * 1024:
                    raise web.HTTPRequestEntityTooLarge(max_size=20 * 1024 * 1024, actual_size=len(content))
                output.write(content)
            text = await asyncio.to_thread(core.transcribe, user["id"], Path(name))
            return web.json_response({"ok": True, "data": {"text": text}}, headers={"Cache-Control": "no-store"})
        except RuntimeError as exc:
            code = str(exc)
            message = "Речь не распознана. Попробуй ещё раз." if code == "STT_EMPTY" else "Распознавание временно недоступно."
            return web.json_response({"ok": False, "error": message, "code": code}, status=422 if code == "STT_EMPTY" else 503)
        finally:
            Path(name).unlink(missing_ok=True)

    async def speech(request):
        try:
            payload = await request.json()
        except (json.JSONDecodeError, TypeError):
            raise web.HTTPBadRequest()
        user = core.valid_webapp_user(payload.get("init_data")) if isinstance(payload, dict) else None
        if not user or not isinstance(user.get("id"), int):
            raise web.HTTPUnauthorized()
        text = str(payload.get("text", "")).strip()
        if not text or len(text) > 2000:
            raise web.HTTPBadRequest(text="Некорректный текст")
        path = await core.make_voice(text)
        try:
            body = await asyncio.to_thread(path.read_bytes)
            # These are server-configured route labels, never user data or secrets.
            # They let one client response lock one stable voice without exposing
            # the underlying provider configuration.
            runtime = core.runtime_config_values() if callable(getattr(core, "runtime_config_values", None)) else {}
            voice_name = "".join(char for char in str(runtime.get("tts_voice") or getattr(core, "VOICE", "edge") or "edge") if char.isprintable() and char not in "\r\n")[:120] or "edge"
            engine = str(runtime.get("tts_provider") or "edge").lower()
            return web.Response(body=body, content_type="audio/mpeg", headers={
                "Cache-Control": "no-store",
                "X-Noema-TTS-Engine": engine,
                "X-Noema-TTS-Voice": voice_name,
            })
        finally:
            Path(path).unlink(missing_ok=True)

    async def chat_stream(request):
        payload = await request.json()
        user = core.valid_webapp_user(payload.get("init_data"))
        if not user or not isinstance(user.get("id"), int):
            raise web.HTTPUnauthorized(text="Открой приложение через Telegram.")
        text = str(payload.get("text", "")).strip()
        if not text or len(text) > 12000:
            raise web.HTTPBadRequest(text="Некорректное сообщение")
        cid, job_id = user["id"], str(uuid.uuid4())
        response = web.StreamResponse(status=200, headers={"Content-Type": "application/x-ndjson; charset=utf-8", "Cache-Control": "no-store", "X-Accel-Buffering": "no"})
        await response.prepare(request)
        queue, loop, cancelled = asyncio.Queue(), asyncio.get_running_loop(), threading.Event()
        subscribed = threading.Event()
        subscribed.set()
        persist_job(job_id, cid, "running")

        def miniapp_is_open():
            transport = request.transport
            return subscribed.is_set() and transport is not None and not transport.is_closing()

        def produce():
            completed = False
            notify_after_disconnect = False
            try:
                with job_locks.setdefault(cid, threading.Lock()):
                    for event in core.stream_agent_response(cid, text, cancelled):
                        if subscribed.is_set():
                            loop.call_soon_threadsafe(queue.put_nowait, event)
                        if event.get("type") == "done":
                            persist_job(job_id, cid, "done")
                            completed = True
                            notify_after_disconnect = not miniapp_is_open()
                        elif event.get("type") == "cancelled":
                            persist_job(job_id, cid, "error", "CANCELLED")
                            completed = True
            except Exception as exc:
                core.LOGGER.exception("Mini App stream failed for chat %s", cid)
                persist_job(job_id, cid, "error", type(exc).__name__)
                completed = True
                if subscribed.is_set():
                    loop.call_soon_threadsafe(queue.put_nowait, {"type": "error", "error": str(exc)})
            finally:
                # A well-behaved canonical stream emits ``done``.  Keep the
                # durable job from being stranded in ``running`` if a legacy
                # stream returns normally without that terminal marker.
                if not completed:
                    persist_job(job_id, cid, "done")
                    notify_after_disconnect = not miniapp_is_open()
                if notify_after_disconnect:
                    loop.call_soon_threadsafe(lambda: asyncio.create_task(notify_completion(job_id, cid)))
                if subscribed.is_set():
                    loop.call_soon_threadsafe(queue.put_nowait, None)

        threading.Thread(target=produce, name=f"miniapp-job-{job_id[:8]}", daemon=True).start()
        try:
            await response.write((json.dumps({"type": "job", "job_id": job_id}) + "\n").encode("utf-8"))
            next_watchdog_at = time.monotonic() + 8
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=max(.1, next_watchdog_at - time.monotonic()))
                except asyncio.TimeoutError:
                    # This is intentionally generic: no unsupported claim about
                    # a search or a tool is shown before the core emits one.
                    await response.write(json.dumps({"type": "progress", "text": "Ответ ещё готовится…"}, ensure_ascii=False).encode("utf-8") + b"\n")
                    next_watchdog_at = time.monotonic() + 8
                    continue
                if event is None:
                    break
                if event.get("type") == "tool":
                    await response.write(json.dumps({"type": "progress", "text": "Действие выполнено, готовлю ответ…"}, ensure_ascii=False).encode("utf-8") + b"\n")
                await response.write((json.dumps(event, ensure_ascii=False) + "\n").encode("utf-8"))
        except (ConnectionResetError, asyncio.CancelledError):
            # The worker deliberately keeps running: its final answer is added by
            # the canonical agent pipeline even if this screen has gone away.
            subscribed.clear()
            with contextlib.suppress(ValueError):
                if job_status(job_id, cid).get("status") == "done":
                    await notify_completion(job_id, cid)
        with contextlib.suppress(ConnectionResetError, asyncio.CancelledError):
            await response.write_eof()
        return response

    async def realtime_token(request):
        try:
            payload = await request.json()
        except (json.JSONDecodeError, TypeError):
            raise web.HTTPBadRequest(text="Некорректный запрос")
        user = core.valid_webapp_user(payload.get("init_data")) if isinstance(payload, dict) else None
        if not user or not isinstance(user.get("id"), int):
            raise web.HTTPUnauthorized(text="Открой приложение через Telegram.")
        if user["id"] not in getattr(core, "ADMIN_CHAT_IDS", set()):
            raise web.HTTPForbidden(text="Недостаточно прав")
        try:
            session = await asyncio.to_thread(core.mint_mistral_realtime_session)
        except RuntimeError as exc:
            code = str(exc)
            status = 503 if code in {"MISTRAL_NOT_CONFIGURED", "MISTRAL_SESSION_ERROR"} else 500
            return web.json_response({"ok": False, "code": code, "error": "Realtime-распознавание временно недоступно."}, status=status)
        # Deliberately return only Mistral's scoped rt_* secret, never the server API key.
        return web.json_response({"ok": True, "data": session}, headers={"Cache-Control": "no-store"})

    app.router.add_post("/api/v1/miniapp/voice", voice)
    app.router.add_post("/api/v1/miniapp/voice/transcribe", voice_transcribe)
    app.router.add_post("/api/v1/miniapp/speech", speech)
    app.router.add_post("/api/v1/miniapp/chat-stream", chat_stream)
    app.router.add_post("/api/v1/miniapp/voice/realtime-token", realtime_token)
    app.router.add_get("/app", index)
    app.router.add_get("/app/", index)
    app.router.add_static("/app/assets/", root, show_index=False)
    app.router.add_post("/api/v1/miniapp", api)
