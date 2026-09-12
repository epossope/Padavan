import asyncio
import json
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from miniapp_api import register_miniapp


class MiniAppSecurityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.core = SimpleNamespace(
            valid_webapp_user=lambda value: {"id": 42} if value == "signed" else None,
            set_mode=Mock(), set_app_setting=Mock(), register_bot_user=Mock(), LOGGER=Mock(),
            TELEMETRY_ENABLED=True, ADMIN_CHAT_IDS={42},
            set_admin_runtime_config=Mock(return_value={"fields": {}}),
            reset_admin_runtime_config=Mock(return_value={"fields": {}}),
            record_runtime_metric=Mock(), reset_runtime_metric_series=Mock(),
            runtime_metric_export=Mock(return_value={"enabled": True, "metrics": {}}),
            mint_mistral_realtime_session=Mock(return_value={
                "token": "rt_scoped", "expires_at": "soon",
                "model": "voxtral-mini-transcribe-realtime-2602",
                "url": "wss://api.mistral.ai/v1/audio/transcriptions/realtime",
            }),
            stream_agent_response=lambda cid, text, cancel: iter([
                {"type": "delta", "text": "При"}, {"type": "delta", "text": "вет"},
                {"type": "done", "text": "Привет"},
            ]),
        )
        self.telegram = SimpleNamespace(bot=SimpleNamespace(send_message=AsyncMock()))
        app = web.Application()
        app["telegram_app"] = self.telegram
        register_miniapp(app, self.core)
        self.client = TestClient(TestServer(app))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()

    async def test_unsigned_rejected(self):
        response = await self.client.post('/api/v1/miniapp', json={"action": "mode", "args": {"mode": "text"}})
        self.assertEqual(response.status, 401)
        self.core.set_mode.assert_not_called()

    async def test_owner_cannot_be_overridden(self):
        response = await self.client.post('/api/v1/miniapp', json={"init_data": "signed", "action": "mode", "args": {"mode": "text", "chat_id": 99}})
        self.assertEqual(response.status, 400)
        self.core.set_mode.assert_not_called()

    async def test_signed_owner_used(self):
        response = await self.client.post('/api/v1/miniapp', json={"init_data": "signed", "action": "mode", "args": {"mode": "text"}})
        self.assertEqual(response.status, 200)
        self.assertRegex(response.headers.get("Server-Timing", ""), r"ui_action;dur=\d")
        self.core.register_bot_user.assert_called_once_with(42, {"id": 42})
        self.core.set_mode.assert_called_once_with(42, 'text')

    async def test_invalid_mode_rejected(self):
        response = await self.client.post('/api/v1/miniapp', json={"init_data": "signed", "action": "mode", "args": {"mode": "invalid"}})
        self.assertEqual(response.status, 400)

    async def test_preview_is_public_but_has_no_user_data(self):
        response = await self.client.get('/app')
        self.assertEqual(response.status, 200)
        self.assertIn('Noema', await response.text())

    async def test_vosk_wake_model_is_served_at_runtime_asset_path(self):
        response = await self.client.get('/app/assets/models/vosk-model-small-ru-0.22.tar.gz')
        self.assertEqual(response.status, 200)
        self.assertEqual(response.headers.get('Content-Type'), 'application/gzip')
        self.assertEqual(await response.content.read(2), b'\x1f\x8b')

    async def test_home_layout_is_saved_for_signed_user(self):
        response = await self.client.post('/api/v1/miniapp', json={"init_data": "signed", "action": "home_layout", "args": {"widgets": ["tasks", "people"]}})
        self.assertEqual(response.status, 200)
        self.core.set_app_setting.assert_called_once_with('miniapp_home_widgets:42', '["tasks", "people"]')

    async def test_duplicate_or_unknown_widgets_rejected(self):
        for widgets in [["tasks", "tasks"], ["unknown"], "tasks"]:
            response = await self.client.post('/api/v1/miniapp', json={"init_data": "signed", "action": "home_layout", "args": {"widgets": widgets}})
            self.assertEqual(response.status, 400)
        self.core.set_app_setting.assert_not_called()

    async def test_non_object_payload_rejected(self):
        response = await self.client.post('/api/v1/miniapp', json=[])
        self.assertEqual(response.status, 400)

    async def test_layout_can_hide_all_without_deleting_data(self):
        response = await self.client.post('/api/v1/miniapp', json={"init_data": "signed", "action": "home_layout", "args": {"widgets": []}})
        self.assertEqual(response.status, 200)
        self.core.set_app_setting.assert_called_once_with('miniapp_home_widgets:42', '[]')

    async def test_chat_stream_is_ndjson_and_preserves_deltas(self):
        response = await self.client.post('/api/v1/miniapp/chat-stream', json={"init_data": "signed", "text": "Привет"})
        self.assertEqual(response.status, 200)
        body = await response.text()
        job_id = next(event["job_id"] for event in map(json.loads, body.splitlines()) if event["type"] == "job")
        self.assertIn('"type": "delta"', body)
        self.assertIn('"text": "При"', body)
        self.assertIn('"type": "done"', body)
        status = await self.client.post('/api/v1/miniapp', json={"init_data": "signed", "action": "conversation_job", "args": {"id": job_id}})
        self.assertEqual(status.status, 200)
        self.assertEqual((await status.json())["data"]["status"], "done")

    async def test_realtime_beta_is_explicitly_opt_in(self):
        response = await self.client.post('/api/v1/miniapp', json={"init_data": "signed", "action": "set_experimental_realtime", "args": {"enabled": True}})
        self.assertEqual(response.status, 200)
        self.core.set_app_setting.assert_called_once_with("miniapp_realtime_beta:42", "1")

    async def test_admin_runtime_config_is_admin_only_and_never_accepts_secrets(self):
        response = await self.client.post('/api/v1/miniapp', json={
            "init_data": "signed", "action": "admin_runtime_config_set",
            "args": {"field": "fast_model", "value": "vendor/model"},
        })
        self.assertEqual(response.status, 200)
        self.core.set_admin_runtime_config.assert_called_once_with(42, "fast_model", "vendor/model")
        self.core.ADMIN_CHAT_IDS = set()
        denied = await self.client.post('/api/v1/miniapp', json={
            "init_data": "signed", "action": "admin_runtime_config_reset",
            "args": {"field": "fast_model"},
        })
        self.assertEqual(denied.status, 403)
        self.core.reset_admin_runtime_config.assert_not_called()

    async def test_realtime_beta_and_token_are_admin_only(self):
        self.core.ADMIN_CHAT_IDS = set()
        toggle = await self.client.post('/api/v1/miniapp', json={
            "init_data": "signed", "action": "set_experimental_realtime", "args": {"enabled": True},
        })
        self.assertEqual(toggle.status, 403)
        token = await self.client.post('/api/v1/miniapp/voice/realtime-token', json={"init_data": "signed"})
        self.assertEqual(token.status, 403)
        self.core.set_app_setting.assert_not_called()
        self.core.mint_mistral_realtime_session.assert_not_called()

    async def test_disconnected_miniapp_job_notifies_once_after_completion(self):
        release = threading.Event()

        def slow_stream(_cid, _text, _cancelled):
            yield {"type": "delta", "text": "Готовлю"}
            release.wait(1)
            yield {"type": "done", "text": "Готово"}

        self.core.stream_agent_response = slow_stream
        response = await self.client.post('/api/v1/miniapp/chat-stream', json={"init_data": "signed", "text": "Привет"})
        self.assertEqual(response.status, 200)
        first_line = await response.content.readline()
        self.assertEqual(json.loads(first_line)["type"], "job")
        connection = response.connection
        connection.close()
        response.close()
        await asyncio.sleep(.1)
        release.set()
        for _ in range(20):
            if self.telegram.bot.send_message.await_count:
                break
            await asyncio.sleep(.05)
        self.telegram.bot.send_message.assert_awaited_once_with(
            chat_id=42, text="Ответ готов. Открой Noema, чтобы продолжить разговор."
        )

    async def test_realtime_token_requires_signed_miniapp_user(self):
        response = await self.client.post('/api/v1/miniapp/voice/realtime-token', json={"init_data": "bad"})
        self.assertEqual(response.status, 401)
        self.core.mint_mistral_realtime_session.assert_not_called()

    async def test_realtime_token_returns_only_scoped_secret(self):
        response = await self.client.post('/api/v1/miniapp/voice/realtime-token', json={"init_data": "signed"})
        self.assertEqual(response.status, 200)
        payload = await response.json()
        self.assertEqual(payload["data"]["token"], "rt_scoped")
        self.assertNotIn("api_key", json.dumps(payload).lower())

    async def test_telemetry_accepts_only_numeric_allowlisted_latency(self):
        response = await self.client.post('/api/v1/miniapp', json={
            "init_data": "signed", "action": "telemetry_record",
            "args": {"metrics": {"tts_first_start_ms": 123.4, "total_ms": 456}},
        })
        self.assertEqual(response.status, 200)
        self.core.record_runtime_metric.assert_any_call("tts_first_start_ms", 123.4)
        self.core.record_runtime_metric.assert_any_call("total_ms", 456.0)

        voice_diagnostic = await self.client.post('/api/v1/miniapp', json={
            "init_data": "signed", "action": "telemetry_record",
            "args": {"metrics": {"barge_in_duration_ms": 250, "barge_in_vad_probability": 0.82}},
        })
        self.assertEqual(voice_diagnostic.status, 200)
        self.core.record_runtime_metric.assert_any_call("barge_in_duration_ms", 250.0)
        self.core.record_runtime_metric.assert_any_call("barge_in_vad_probability", 0.82)

        rejected = await self.client.post('/api/v1/miniapp', json={
            "init_data": "signed", "action": "telemetry_record",
            "args": {"metrics": {"text": "private message"}},
        })
        self.assertEqual(rejected.status, 400)

    async def test_telemetry_export_and_reset_are_admin_only(self):
        exported = await self.client.post('/api/v1/miniapp', json={"init_data": "signed", "action": "telemetry_export", "args": {}})
        self.assertEqual(exported.status, 200)
        self.core.runtime_metric_export.assert_called_once()
        reset = await self.client.post('/api/v1/miniapp', json={"init_data": "signed", "action": "telemetry_reset", "args": {}})
        self.assertEqual(reset.status, 200)
        self.core.reset_runtime_metric_series.assert_called_once()

    def test_voice_metrics_finalize_on_total_ms_and_strip_payload_fields(self):
        source = (Path(__file__).parent.parent / "miniapp" / "voice-conversation.js").read_text(encoding="utf-8")
        self.assertIn("if(name==='total_ms')", source)
        self.assertNotIn("if(name==='total')metrics.push", source)
        self.assertIn("const sample={};for(const metricName of latencyMetrics)", source)
        self.assertIn("class SileroVADProvider", source)
        self.assertIn("bargeStableMs:250", source)
        self.assertIn("speakingVadProbability:.82", source)
        self.assertIn("echoCancellation:true,noiseSuppression:true,autoGainControl:true", source)
        self.assertIn("barge_in_reason_code", source)
        self.assertIn("wakeWords=['эма','эмма']", source)
        self.assertIn("model_load_failed", source)
        self.assertIn("recognizerReady", source)
        self.assertIn("AdaptiveNoiseFloor", source)
        self.assertIn("preRollMs:400", source)
        self.assertIn("realtime_fallback_batch_count", source)
        self.assertIn("batch_fallback_success_count", source)
        self.assertIn("vad_fallback_reason_code", source)
        self.assertIn("vosk_load_ms", source)
        self.assertIn("silero_load_ms", source)
        self.assertIn("conversation_ready_ms", source)
        self.assertIn("this.setState('PREPARING')", source)
        self.assertIn("streamIsLive", source)
        self.assertIn("pagehide", source)
        self.assertIn("realtime_beta=1", source)
        self.assertIn("[data-realtime-conversation]", source)
        self.assertIn("wakeEnabled", source)
        self.assertIn("prepareAssets", source)
        self.assertIn("if(!this.wakeEnabled())", source)
        self.assertIn("audio_format:{encoding:'pcm_s16le',sample_rate:16000}", source)
        self.assertNotIn("алёна", source.lower())
        self.assertNotIn("нина", source.lower())

    def test_production_voice_is_global_batch_path_and_realtime_is_beta_only(self):
        root = Path(__file__).parent.parent / "miniapp"
        app_source = (root / "app.js").read_text(encoding="utf-8")
        screen_source = (root / "screens.js").read_text(encoding="utf-8")
        realtime_source = (root / "voice-conversation.js").read_text(encoding="utf-8")
        self.assertIn("class VoiceController", app_source)
        self.assertIn("/voice/transcribe", app_source)
        self.assertIn("class SentenceChunker", app_source)
        self.assertIn("/miniapp/speech", app_source)
        self.assertIn("experimental_realtime", app_source)
        self.assertIn("experimental_realtime_available", app_source)
        self.assertIn("set_experimental_wake", app_source)
        self.assertIn("data.settings?.mode!=='voice'||m.role!=='assistant'", app_source)
        self.assertIn("await voiceController.ask(text)", app_source)
        self.assertIn("chat-voice-orb", app_source)
        self.assertIn("sanitizeSpeechText", app_source)
        self.assertIn("beginResponse(started)", app_source)
        self.assertIn("this.route='browser'", app_source)
        self.assertIn("X-Noema-TTS-Voice", (Path(__file__).parent.parent / "miniapp_api.py").read_text(encoding="utf-8"))
        self.assertNotIn("chat-ambient", screen_source)
        self.assertNotIn("data-conversation", screen_source)
        self.assertIn("realtime_beta=1", realtime_source)

    async def test_production_tts_metrics_are_numeric_allowlisted(self):
        response = await self.client.post('/api/v1/miniapp', json={
            "init_data": "signed", "action": "telemetry_record",
            "args": {"metrics": {
                "tts_queue_wait_ms": 12, "tts_prepare_ms": 34,
                "tts_first_chunk_ms": 56, "tts_voice_name": 101,
                "tts_engine_name": 1, "speech_text_length_chars": 78,
            }},
        })
        self.assertEqual(response.status, 200)
        self.core.record_runtime_metric.assert_any_call("tts_queue_wait_ms", 12.0)
        self.core.record_runtime_metric.assert_any_call("tts_prepare_ms", 34.0)
        self.core.record_runtime_metric.assert_any_call("tts_voice_name", 101.0)
        self.core.record_runtime_metric.assert_any_call("speech_text_length_chars", 78.0)

    def test_miniapp_and_telegram_share_the_canonical_streaming_pipeline(self):
        api_source = (Path(__file__).parent.parent / "miniapp_api.py").read_text(encoding="utf-8")
        bot_source = (Path(__file__).parent.parent / "bot.py").read_text(encoding="utf-8")
        self.assertIn("core.stream_agent_response(cid, text, cancelled)", api_source)
        self.assertIn("for event in stream_agent_response(chat_id, text, cancelled)", bot_source)

    async def test_telemetry_accepts_voice_reliability_measurements(self):
        response = await self.client.post('/api/v1/miniapp', json={
            "init_data": "signed", "action": "telemetry_record",
            "args": {"metrics": {
                "vad_engine": 1, "noise_floor_rms": 0.013,
                "realtime_fallback_batch_count": 1, "stt_ws_connect_ms": 245,
                "vosk_load_ms": 420, "conversation_ready_ms": 650,
            }},
        })
        self.assertEqual(response.status, 200)
        self.core.record_runtime_metric.assert_any_call("vad_engine", 1.0)
        self.core.record_runtime_metric.assert_any_call("noise_floor_rms", 0.013)
        self.core.record_runtime_metric.assert_any_call("realtime_fallback_batch_count", 1.0)
        self.core.record_runtime_metric.assert_any_call("stt_ws_connect_ms", 245.0)
        self.core.record_runtime_metric.assert_any_call("vosk_load_ms", 420.0)
        self.core.record_runtime_metric.assert_any_call("conversation_ready_ms", 650.0)
