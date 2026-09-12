"""Measurement-only OpenRouter A/B for Noema's candidate chat models.

The script never changes .env, SQLite, router settings, user data, or tools.
It sends neutral Russian prompts and a synthetic read-only tool schema, retains
only numeric timings/counts/costs in its stdout JSON, and never prints a key or
model response text.
"""
from __future__ import annotations

import json
import math
import os
import time
from collections import Counter

import requests
from dotenv import load_dotenv


CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"
READ_ONLY_TOOL = [{"type": "function", "function": {
    "name": "lookup_today_plan",
    "description": "Тестовый read-only запрос плана на сегодня. Ничего не изменяет.",
    "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
}}]
PLAIN_PROMPTS = [
    "Ответь одним коротким предложением по-русски: что помогает не забыть важную задачу?",
    "Ответь одним коротким предложением по-русски: зачем нужен список дел?",
    "Ответь одним коротким предложением по-русски: как вежливо напомнить о встрече?",
    "Ответь одним коротким предложением по-русски: что полезно записать после звонка?",
    "Ответь одним коротким предложением по-русски: как выбрать главную задачу дня?",
    "Ответь одним коротким предложением по-русски: чем заметка отличается от задачи?",
    "Ответь одним коротким предложением по-русски: почему полезен календарь?",
    "Ответь одним коротким предложением по-русски: как не пропустить дедлайн?",
    "Ответь одним коротким предложением по-русски: что такое спокойный план дня?",
    "Ответь одним коротким предложением по-русски: как завершить задачу?",
]
TOOL_PROMPT = "Используй инструмент lookup_today_plan ровно один раз. Не объясняй выбор и не вызывай другие инструменты."


def env_providers(name: str) -> list[str]:
    return [value.strip() for value in os.getenv(name, "").split(",") if value.strip()]


def env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    return default if value is None else value.strip().lower() in {"1", "true", "yes", "on"}


def provider_routing(prefix: str, default_fallback: bool) -> dict:
    providers = env_providers(f"{prefix}_PROVIDERS")
    if not providers:
        return {}
    return {
        "only": providers, "order": providers,
        "allow_fallbacks": env_bool(f"{prefix}_ALLOW_PROVIDER_FALLBACK", default_fallback),
        "require_parameters": True,
    }


def percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    values = sorted(values)
    return round(values[math.ceil(len(values) * p) - 1], 1)


def summary(values: list[float]) -> dict[str, float | int | None]:
    return {
        "count": len(values),
        "avg": round(sum(values) / len(values), 1) if values else None,
        "p50": percentile(values, 0.50),
        "p95": percentile(values, 0.95),
        "max": round(max(values), 1) if values else None,
    }


def run_request(key: str, model: str, provider: dict, prompt: str, use_tool: bool) -> dict:
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "Ты отвечаешь по-русски, кратко и точно."},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0,
        "max_tokens": 80,
        "stream": True,
        "stream_options": {"include_usage": True},
        "provider": provider,
    }
    if use_tool:
        payload["tools"] = READ_ONLY_TOOL
        # Match the production chat runtime. The prompt still explicitly asks
        # for this one harmless schema, so selection can be measured.
        payload["tool_choice"] = "auto"
    started = time.perf_counter()
    first_ms = None
    usage: dict = {}
    content_parts: list[str] = []
    tool_name = ""
    try:
        response = requests.post(
            CHAT_URL,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json=payload,
            stream=True,
            timeout=(20, 120),
        )
        if not response.ok:
            return {"ok": False, "status": response.status_code, "total_ms": round((time.perf_counter() - started) * 1000, 1)}
        provider_used = response.headers.get("x-openrouter-provider", "")
        for raw in response.iter_lines():
            if not raw or not raw.startswith(b"data: "):
                continue
            if raw == b"data: [DONE]":
                break
            try:
                event = json.loads(raw[6:])
            except json.JSONDecodeError:
                continue
            choices = event.get("choices") or []
            delta = choices[0].get("delta", {}) if choices else {}
            content = delta.get("content") or ""
            calls = delta.get("tool_calls") or []
            if first_ms is None and (content or calls):
                first_ms = round((time.perf_counter() - started) * 1000, 1)
            content_parts.append(content)
            for call in calls:
                tool_name += str(call.get("function", {}).get("name", ""))
            if isinstance(event.get("usage"), dict):
                usage = event["usage"]
        total_ms = round((time.perf_counter() - started) * 1000, 1)
        text = "".join(content_parts)
        return {
            "ok": True,
            "ttft_ms": first_ms,
            "total_ms": total_ms,
            "russian": bool(any("\u0400" <= char <= "\u04ff" for char in text)),
            "tool_success": tool_name == "lookup_today_plan" if use_tool else None,
            "cost_usd": float(usage.get("cost", 0) or 0),
            "provider": provider_used,
        }
    except requests.RequestException:
        return {"ok": False, "status": "network_error", "total_ms": round((time.perf_counter() - started) * 1000, 1)}
    finally:
        if "response" in locals():
            response.close()


def benchmark_candidate(key: str, model: str, provider: dict) -> dict:
    rows = [run_request(key, model, provider, prompt, False) for prompt in PLAIN_PROMPTS]
    rows += [run_request(key, model, provider, TOOL_PROMPT, True) for _ in range(5)]
    succeeded = [row for row in rows if row["ok"]]
    plain = [row for row in succeeded if row["tool_success"] is None]
    tool_rows = [row for row in succeeded if row["tool_success"] is not None]
    return {
        "model": model,
        "provider_request": provider,
        "count": len(rows),
        "successful": len(succeeded),
        "errors": Counter(str(row.get("status")) for row in rows if not row["ok"]),
        "ttft_ms": summary([row["ttft_ms"] for row in succeeded if row.get("ttft_ms") is not None]),
        "total_ms": summary([row["total_ms"] for row in succeeded]),
        "tool_call_success": {"count": sum(bool(row["tool_success"]) for row in tool_rows), "total": len(tool_rows)},
        "russian_plain": {"count": sum(bool(row["russian"]) for row in plain), "total": len(plain)},
        "cost_usd": round(sum(row["cost_usd"] for row in succeeded), 8),
        "providers": dict(Counter(row.get("provider") or "not_exposed" for row in succeeded)),
    }


def main() -> None:
    load_dotenv()
    key = os.getenv("OPENROUTER_API_KEY", "").strip()
    if not key:
        raise SystemExit("OPENROUTER_API_KEY is not configured")
    fast_model = os.getenv("FAST_MODEL", "qwen/qwen3.5-flash-02-23").strip()
    strong_model = os.getenv("STRONG_MODEL", "deepseek/deepseek-v3.2").strip()
    report = {
        "measurement_only": True,
        "requests_per_candidate": 15,
        "fast": benchmark_candidate(key, fast_model, provider_routing("FAST_MODEL", False)),
        "strong": benchmark_candidate(key, strong_model, provider_routing("STRONG_MODEL", True)),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2, default=dict))


if __name__ == "__main__":
    main()
