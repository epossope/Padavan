"""Same-origin Telegram Mini App API; all data is scoped to signed Telegram identity."""
import asyncio
import contextlib
import json
import os
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path

from aiohttp import web


def register_miniapp(app, core):
    root = Path(__file__).parent / "miniapp"
    locks = {}
    weather_cache = {}
    default_widgets = ["tasks", "next_event", "notes", "reminders", "budget", "recent_saved"]
    widget_types = set(default_widgets) | {"people"}

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
            return web.json_response({"ok": True, "data": result}, headers={"Cache-Control": "no-store"})
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
        return {"day": day, "plan": core.get_plan_for_date(cid, day), "tasks": tasks, "reminders": reminders,
                "notes": core.get_notes(cid, 50)["notes"], "people": core.get_people(cid)["people"],
                "expenses": core.get_expenses(cid)["items"], "files": files,
                "history": core.history(cid, 50), "rules": core.behavior_rules_for(cid),
                "settings": {"timezone": core.timezone_name_for(cid), "mode": core.get_mode(cid),
                             "home_widgets": widgets_for(cid),
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

    async def chat_stream(request):
        payload = await request.json()
        user = core.valid_webapp_user(payload.get("init_data"))
        if not user or not isinstance(user.get("id"), int):
            raise web.HTTPUnauthorized(text="Открой приложение через Telegram.")
        text = str(payload.get("text", "")).strip()
        if not text or len(text) > 12000:
            raise web.HTTPBadRequest(text="Некорректное сообщение")
        cid = user["id"]
        response = web.StreamResponse(status=200, headers={"Content-Type": "application/x-ndjson; charset=utf-8", "Cache-Control": "no-store", "X-Accel-Buffering": "no"})
        await response.prepare(request)
        queue, loop, cancelled = asyncio.Queue(), asyncio.get_running_loop(), threading.Event()

        def produce():
            try:
                for event in core.stream_agent_response(cid, text, cancelled):
                    loop.call_soon_threadsafe(queue.put_nowait, event)
            except Exception as exc:
                core.LOGGER.exception("Mini App stream failed for chat %s", cid)
                loop.call_soon_threadsafe(queue.put_nowait, {"type": "error", "error": str(exc)})
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, None)

        async with locks.setdefault(cid, asyncio.Lock()):
            worker = threading.Thread(target=produce, name=f"miniapp-stream-{cid}", daemon=True)
            worker.start()
            try:
                while True:
                    event = await queue.get()
                    if event is None:
                        break
                    await response.write((json.dumps(event, ensure_ascii=False) + "\n").encode("utf-8"))
            except (ConnectionResetError, asyncio.CancelledError):
                cancelled.set()
            finally:
                cancelled.set()
        with contextlib.suppress(ConnectionResetError):
            await response.write_eof()
        return response

    app.router.add_post("/api/v1/miniapp/voice", voice)
    app.router.add_post("/api/v1/miniapp/chat-stream", chat_stream)
    app.router.add_get("/app", index)
    app.router.add_get("/app/", index)
    app.router.add_static("/app/assets/", root, show_index=False)
    app.router.add_post("/api/v1/miniapp", api)
