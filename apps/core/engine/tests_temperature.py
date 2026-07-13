"""The temperature-rejection message.

A 4xx that specifically refuses the `temperature` we send is a permanent config
defect (like the other 4xx buckets), but its message calls out that deterministic
(greedy, temperature=0) scoring is unavailable on this model rather than blaming
structured outputs. Its own module because engine `tests.py` is at the file cap.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest import mock

import httpx
from django.test import SimpleTestCase
from openai import BadRequestError

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
# The real 400 an OpenAI reasoning model returns for a non-default temperature.
_TEMP_400 = (
    "Unsupported value: 'temperature' does not support 0 with this model. Only the default (1) value is supported."
)


def _rejecting_client(*, message: str = "Bad request", body: object | None = None) -> mock.MagicMock:
    """A client whose chat.completions.create raises a 400. A dict `body` is parsed
    by the SDK into exc.param/code (as OpenAI/Azure populate them) ; `message` is what
    str(exc) carries, which is all the text-fallback path has to work with."""
    req = httpx.Request("POST", "http://llm.test/v1/chat/completions")
    exc = BadRequestError(message, response=httpx.Response(400, request=req), body=body)
    client = mock.MagicMock()
    client.chat.completions.create.side_effect = exc
    return client


def _judge_error_text(client: mock.MagicMock) -> str:
    """Run judge() against a rejecting client and return the raised
    EngineRequestRejected message, lowercased (fails if it does not raise)."""
    # Construct INSIDE the patch: the engine builds its OpenAI client eagerly in
    # __init__, so the mock must be in place before construction.
    with mock.patch(f"{ENGINE_MOD}.OpenAI", return_value=client):
        eng = OpenAICompatEngine(base_url="http://llm.test/v1", default_model="m")
        try:
            eng.judge(PAYLOAD, instructions="x")
        except EngineRequestRejected as exc:
            return str(exc).lower()
    raise AssertionError("judge did not raise EngineRequestRejected")


class TemperatureRejectionTests(SimpleTestCase):
    def test_refusal_text_explains_determinism(self) -> None:
        # No param (body=None), but the message is refusal-shaped, so the narrowed
        # text fallback fires. Names temperature + determinism, not json_schema.
        text = _judge_error_text(_rejecting_client(message=_TEMP_400))
        self.assertIn("temperature", text)
        self.assertIn("determin", text)
        self.assertNotIn("json_schema", text)

    def test_param_signal_fires_without_refusal_text(self) -> None:
        # OpenAI/Azure set param="temperature" ; trust that precise signal even when
        # the message text is not refusal-shaped (the primary path).
        text = _judge_error_text(_rejecting_client(message="Invalid request", body={"param": "temperature"}))
        self.assertIn("determin", text)

    def test_unrelated_temperature_mention_falls_through(self) -> None:
        # A 400 that merely echoes "temperature" (no param, no refusal phrase) must NOT
        # misfire the determinism guidance ; it takes the generic (json_schema) branch.
        text = _judge_error_text(_rejecting_client(message="Invalid 'messages': your text mentioned temperature."))
        self.assertNotIn("determin", text)
        self.assertIn("json_schema", text)
