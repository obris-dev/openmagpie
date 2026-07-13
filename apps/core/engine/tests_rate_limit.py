"""ENGINE_MAX_RETRIES wiring + the distinct 429 warning.

The OpenAI client's own backoff (max_retries) is the smoothing layer under
concurrent draining; a 429 that outlives it surfaces as a transient failure AND a
greppable WARNING (so it stays visible while tuning WATCH_RUN_DRAIN_CONCURRENCY),
never a permanent EngineRequestRejected. Own module: engine tests.py is at the cap.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest import mock

import httpx
from django.test import SimpleTestCase, override_settings
from openai import RateLimitError

from engine import registry
from engine.engines import EngineRequestRejected, OpenAICompatEngine
from sources.payloads import SourcePayload

ENGINE_MOD = "engine.engines.openai_compat.engine"
PAYLOAD = SourcePayload(
    external_id="e1",
    kind="rss_entry",
    occurred_at=datetime(2026, 1, 1, tzinfo=UTC),
    source="rss",
    title="A coach was hired",
    content="...body...",
)


def _rate_limit_error() -> RateLimitError:
    req = httpx.Request("POST", "http://llm.test/v1/chat/completions")
    return RateLimitError("rate limited", response=httpx.Response(429, request=req), body=None)


def _client_raising(exc: Exception) -> mock.MagicMock:
    client = mock.MagicMock()
    client.chat.completions.create.side_effect = exc
    return client


class RateLimitTests(SimpleTestCase):
    def test_429_warns_and_stays_transient(self) -> None:
        with mock.patch(f"{ENGINE_MOD}.OpenAI", return_value=_client_raising(_rate_limit_error())):
            eng = OpenAICompatEngine(base_url="http://llm.test/v1", default_model="m")
            with (
                self.assertLogs("engine", level="WARNING") as logs,
                self.assertRaises(RateLimitError) as ctx,
            ):
                eng.judge(PAYLOAD, instructions="x")
        # Propagates (drain maps it to a retryable FAILED), NOT a permanent rejection.
        self.assertNotIsInstance(ctx.exception, EngineRequestRejected)
        self.assertTrue(any("rate-limited" in line for line in logs.output))

    def test_max_retries_passed_to_client(self) -> None:
        with mock.patch(f"{ENGINE_MOD}.OpenAI") as openai_cls:
            OpenAICompatEngine(base_url="http://llm.test/v1", default_model="m", max_retries=5)
        self.assertEqual(openai_cls.call_args.kwargs["max_retries"], 5)

    @override_settings(ENGINE_BASE_URL="http://llm.test/v1", ENGINE_MODEL="m", ENGINE_API_KEY="", ENGINE_MAX_RETRIES=7)
    def test_registry_build_forwards_the_setting(self) -> None:
        # The "backoff for every backend" claim rides on registry._build passing
        # settings.ENGINE_MAX_RETRIES, not just the constructor accepting it.
        with mock.patch(f"{ENGINE_MOD}.OpenAI") as openai_cls:
            registry._build()
        self.assertEqual(openai_cls.call_args.kwargs["max_retries"], 7)
