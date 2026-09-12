import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from miniapp_api import register_miniapp


class MiniAppSecurityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.core = SimpleNamespace(
            valid_webapp_user=lambda value: {"id": 42} if value == "signed" else None,
            set_mode=Mock(), set_app_setting=Mock(), LOGGER=Mock(),
            TELEMETRY_ENABLED=True, ADMIN_CHAT_IDS={42},
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
        app = web.Application()
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
        self.assertIn('"type": "delta"', body)
        self.assertIn('"text": "При"', body)
        self.assertIn('"type": "done"', body)

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
        self.assertIn("audio_format:{encoding:'pcm_s16le',sample_rate:16000}", source)
        self.assertNotIn("алёна", source.lower())
        self.assertNotIn("нина", source.lower())

    async def test_telemetry_accepts_voice_reliability_measurements(self):
        response = await self.client.post('/api/v1/miniapp', json={
            "init_data": "signed", "action": "telemetry_record",
            "args": {"metrics": {
                "vad_engine": 1, "noise_floor_rms": 0.013,
                "realtime_fallback_batch_count": 1, "stt_ws_connect_ms": 245,
            }},
        })
        self.assertEqual(response.status, 200)
        self.core.record_runtime_metric.assert_any_call("vad_engine", 1.0)
        self.core.record_runtime_metric.assert_any_call("noise_floor_rms", 0.013)
        self.core.record_runtime_metric.assert_any_call("realtime_fallback_batch_count", 1.0)
        self.core.record_runtime_metric.assert_any_call("stt_ws_connect_ms", 245.0)
