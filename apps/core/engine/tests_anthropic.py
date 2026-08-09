"""Anthropic Engine unit tests."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest import mock

from django.test import SimpleTestCase
from anthropic import APIConnectionError, APIStatusError, AuthenticationError, NotFoundError, RateLimitError

from engine.engines import EngineRequestRejected
from engine.engines.anthropic import AnthropicEngine
from openmagpie_schema.watch_actions import ExtractField
from sources.payloads import SourcePayload

ENGINE_MOD = "engine.engines.anthropic.engine"

PAYLOAD = SourcePayload(
    external_id="e1",
    kind="rss_entry",
    occurred_at=datetime(2026, 1, 1, tzinfo=UTC),
    source="rss",
    title="A coach was hired",
    content="...body...",
)

def _engine(**kw) -> AnthropicEngine:
    return AnthropicEngine(base_url="http://anthropic.test/v1", default_model="m", api_key="test-key", **kw)

def _patch_client(client: mock.MagicMock):
    """Patch the engine module's `Anthropic` so the engine's eagerly-built `self._client` is our mock."""
    return mock.patch(f"{ENGINE_MOD}.Anthropic", return_value=client)

def _client(*, content: str | None = "{}", create_error=None) -> mock.MagicMock:
    """A mock `Anthropic` client."""
    c = mock.MagicMock()
    if create_error is not None:
        c.messages.create.side_effect = create_error
    else:
        if content is None:
            c.messages.create.return_value = mock.MagicMock(content=[])
        else:
            block = mock.MagicMock(type="tool_use")
            block.name = "judgment"
            import json
            block.input = json.loads(content)
            c.messages.create.return_value = mock.MagicMock(content=[block])
    return c

class AnthropicJudgeTests(SimpleTestCase):
    def test_parses_choices_content_into_judgment_result(self) -> None:
        with _patch_client(_client(content='{"score": 0.82, "reason": "on topic"}')):
            out = _engine().judge(PAYLOAD, instructions="coach hires")
        self.assertEqual(out.score, 0.82)
        self.assertEqual(out.reason, "on topic")
        self.assertEqual(out.model, "m")

    def test_per_call_model_override_wins(self) -> None:
        with _patch_client(_client(content='{"score": 0.1, "reason": "no"}')):
            out = _engine().judge(PAYLOAD, instructions="x", model="claude-3-5-sonnet")
        self.assertEqual(out.model, "claude-3-5-sonnet")

    def test_empty_choices_fails_validation_transient(self) -> None:
        with _patch_client(_client(content=None)), self.assertRaises(Exception) as ctx:
            _engine().judge(PAYLOAD, instructions="x")
        self.assertNotIsInstance(ctx.exception, EngineRequestRejected)

    def test_judge_requests_tool_schema(self) -> None:
        c = _client(content='{"score": 1, "reason": "y"}')
        with _patch_client(c):
            _engine().judge(PAYLOAD, instructions="x")
        tools = c.messages.create.call_args.kwargs["tools"]
        self.assertEqual(len(tools), 1)
        self.assertEqual(tools[0]["name"], "judgment")

    def test_no_model_anywhere_is_permanent(self) -> None:
        eng = AnthropicEngine(base_url="http://anthropic.test/v1", default_model="")
        with self.assertRaises(EngineRequestRejected) as ctx:
            eng.judge(PAYLOAD, instructions="x")
        self.assertIn("ENGINE_MODEL", str(ctx.exception))

class AnthropicJudgeErrorBucketTests(SimpleTestCase):
    def _judge_raising(self, exc):
        with _patch_client(_client(create_error=exc)):
            _engine().judge(PAYLOAD, instructions="x")

    def test_401_is_permanent_auth_error(self) -> None:
        import httpx
        req = httpx.Request("POST", "http://x")
        resp = httpx.Response(401, request=req)
        with self.assertRaises(EngineRequestRejected) as ctx:
            self._judge_raising(AuthenticationError("auth error", response=resp, body={}))
        self.assertIn("rejected request (401)", str(ctx.exception))

    def test_404_is_permanent_error(self) -> None:
        import httpx
        req = httpx.Request("POST", "http://x")
        resp = httpx.Response(404, request=req)
        with self.assertRaises(EngineRequestRejected) as ctx:
            self._judge_raising(NotFoundError("not found", response=resp, body={}))
        self.assertIn("rejected request (404)", str(ctx.exception))

    def test_429_is_transient(self) -> None:
        import httpx
        req = httpx.Request("POST", "http://x")
        resp = httpx.Response(429, request=req)
        with self.assertRaises(RateLimitError):
            self._judge_raising(RateLimitError("too many requests", response=resp, body={}))

    def test_connection_error_is_transient(self) -> None:
        with self.assertRaises(APIConnectionError):
            self._judge_raising(APIConnectionError(request=mock.MagicMock()))

class AnthropicExtractTests(SimpleTestCase):
    FIELDS = [ExtractField(name="person", description="who"), ExtractField(name="org", description="where")]

    def _client_extract(self, content: str) -> mock.MagicMock:
        c = mock.MagicMock()
        import json
        block = mock.MagicMock(type="tool_use")
        block.name = "extraction"
        block.input = json.loads(content)
        c.messages.create.return_value = mock.MagicMock(content=[block])
        return c

    def test_parses_declared_fields(self) -> None:
        with _patch_client(self._client_extract('{"person": "Pat", "org": "Acme"}')):
            out = _engine().extract(PAYLOAD, fields=self.FIELDS)
        self.assertEqual(out.extracted, {"person": "Pat", "org": "Acme"})

    def test_missing_field_coerced_to_empty(self) -> None:
        with _patch_client(self._client_extract('{"person": "Pat"}')):
            out = _engine().extract(PAYLOAD, fields=self.FIELDS)
        self.assertEqual(out.extracted, {"person": "Pat", "org": ""})

    def test_extra_keys_dropped_and_values_stringified(self) -> None:
        with _patch_client(self._client_extract('{"person": "Pat", "org": 5, "extra": "x"}')):
            out = _engine().extract(PAYLOAD, fields=self.FIELDS)
        self.assertEqual(out.extracted, {"person": "Pat", "org": "5"})
