import json
import unittest
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
