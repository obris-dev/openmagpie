"""Engine unit tests: the OpenAI-compatible engine (driven by a mocked `openai`
client), the standalone model probe, and the thin registry. No live LLM - the
engine's client is patched and the probe's httpx is stubbed."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest import mock

import httpx
from django.test import SimpleTestCase
from openai import (
    APIConnectionError,
    AuthenticationError,
    BadRequestError,
    InternalServerError,
    NotFoundError,
    PermissionDeniedError,
    RateLimitError,
    UnprocessableEntityError,
)

from engine import checks, registry
from engine.engines import EngineRequestRejected, OpenAICompatEngine
from engine.scripts import probe
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


def _engine(**kw) -> OpenAICompatEngine:
    return OpenAICompatEngine(base_url="http://llm.test/v1", default_model="m", **kw)


# ── openai client mocks ────────────────────────────────────────────────────
def _patch_client(client: mock.MagicMock):
    """Patch the engine module's `OpenAI` so `_client()` returns our mock."""
    return mock.patch(f"{ENGINE_MOD}.OpenAI", return_value=client)


def _client(*, content: str | None = "{}", create_error=None, models=(), list_error=None) -> mock.MagicMock:
    """A mock `OpenAI` client. chat.completions.create returns a completion with
    `content` (or raises `create_error`); with_options(...).models.list returns
    `models` ids (or raises `list_error`). content=None -> empty choices."""
    c = mock.MagicMock()
    if create_error is not None:
        c.chat.completions.create.side_effect = create_error
    else:
        choices = [mock.MagicMock(message=mock.MagicMock(content=content))] if content is not None else []
        c.chat.completions.create.return_value = mock.MagicMock(choices=choices)
    scoped = c.with_options.return_value
    if list_error is not None:
        scoped.models.list.side_effect = list_error
    else:
        scoped.models.list.return_value = mock.MagicMock(data=[mock.MagicMock(id=i) for i in models])
    return c


def _api_error(cls, status: int):
    """Build an openai APIStatusError subclass with the given HTTP status."""
    req = httpx.Request("POST", "http://llm.test/v1/chat/completions")
    return cls("boom", response=httpx.Response(status, request=req), body=None)


def _conn_error() -> APIConnectionError:
    return APIConnectionError(request=httpx.Request("GET", "http://llm.test/v1/models"))


class JudgeTests(SimpleTestCase):
    def test_parses_choices_content_into_judgment_result(self) -> None:
        with _patch_client(_client(content='{"score": 0.82, "reason": "on topic"}')):
            out = _engine().judge(PAYLOAD, instructions="coach hires")
        self.assertEqual(out.score, 0.82)
        self.assertEqual(out.reason, "on topic")
        self.assertEqual(out.model, "m")  # default_model used when no override

    def test_per_call_model_override_wins(self) -> None:
        with _patch_client(_client(content='{"score": 0.1, "reason": "no"}')):
            out = _engine().judge(PAYLOAD, instructions="x", model="llama3.1:70b")
        self.assertEqual(out.model, "llama3.1:70b")

    def test_empty_choices_fails_validation_transient(self) -> None:
        # No choices -> empty content -> JudgmentJSON validation error (recoverable
        # FAILED), NOT a permanent EngineRequestRejected.
        with _patch_client(_client(content=None)), self.assertRaises(Exception) as ctx:
            _engine().judge(PAYLOAD, instructions="x")
        self.assertNotIsInstance(ctx.exception, EngineRequestRejected)

    def test_judge_requests_strict_json_schema(self) -> None:
        c = _client(content='{"score": 1, "reason": "y"}')
        with _patch_client(c):
            _engine().judge(PAYLOAD, instructions="x")
        rf = c.chat.completions.create.call_args.kwargs["response_format"]
        self.assertEqual(rf["type"], "json_schema")
        self.assertTrue(rf["json_schema"]["strict"])

    def test_no_model_anywhere_is_permanent(self) -> None:
        # ENGINE_MODEL unset + no per-action pin -> permanent, before any HTTP call.
        eng = OpenAICompatEngine(base_url="http://llm.test/v1", default_model="")
        with self.assertRaises(EngineRequestRejected) as ctx:
            eng.judge(PAYLOAD, instructions="x")
        self.assertIn("ENGINE_MODEL", str(ctx.exception))


class JudgeErrorBucketTests(SimpleTestCase):
    """Permanent 4xx config defects (the SDK's typed exceptions) -> EngineRequestRejected;
    429/5xx/conn -> propagate as the SDK error (the drain maps it to recoverable FAILED)."""

    def _judge_raising(self, exc):
        with _patch_client(_client(create_error=exc)):
            _engine().judge(PAYLOAD, instructions="x")

    def test_401_is_permanent_auth_error(self) -> None:
        with self.assertRaises(EngineRequestRejected) as ctx:
            self._judge_raising(_api_error(AuthenticationError, 401))
        self.assertIn("ENGINE_API_KEY", str(ctx.exception))

    def test_403_is_permanent_auth_error(self) -> None:
        with self.assertRaises(EngineRequestRejected):
            self._judge_raising(_api_error(PermissionDeniedError, 403))

    def test_404_points_at_url_and_model(self) -> None:
        with self.assertRaises(EngineRequestRejected) as ctx:
            self._judge_raising(_api_error(NotFoundError, 404))
        self.assertIn("ENGINE_BASE_URL", str(ctx.exception))

    def test_400_is_permanent(self) -> None:
        with self.assertRaises(EngineRequestRejected):
            self._judge_raising(_api_error(BadRequestError, 400))

    def test_422_is_permanent(self) -> None:
        with self.assertRaises(EngineRequestRejected):
            self._judge_raising(_api_error(UnprocessableEntityError, 422))

    def test_429_is_transient_not_rejected(self) -> None:
        with self.assertRaises(RateLimitError):
            self._judge_raising(_api_error(RateLimitError, 429))

    def test_500_is_transient_not_rejected(self) -> None:
        with self.assertRaises(InternalServerError):
            self._judge_raising(_api_error(InternalServerError, 500))

    def test_connection_error_is_transient(self) -> None:
        with self.assertRaises(APIConnectionError):
            self._judge_raising(_conn_error())


class StatusTests(SimpleTestCase):
    def test_reachable_lists_sorted_models(self) -> None:
        with _patch_client(_client(models=["b", "a"])):
            st = _engine().status()
        self.assertTrue(st.available)
        self.assertEqual(st.available_models, ["a", "b"])

    def test_unreachable_is_not_available_with_reason(self) -> None:
        with _patch_client(_client(list_error=_conn_error())):
            st = _engine().status()
        self.assertFalse(st.available)
        self.assertIn("unreachable", st.unreachable_reason or "")


class ProbeTests(SimpleTestCase):
    """The standalone, Django-free /v1/models probe the quickstart shells out to."""

    def test_lists_model_ids(self) -> None:
        resp = httpx.Response(200, json={"data": [{"id": "a"}, {"id": "b"}]}, request=httpx.Request("GET", "http://x"))
        with mock.patch("engine.scripts.probe.httpx.get", return_value=resp):
            self.assertEqual(probe.probe_models("http://llm.test/v1"), ["a", "b"])

    def test_unreachable_is_empty(self) -> None:
        with mock.patch("engine.scripts.probe.httpx.get", side_effect=httpx.ConnectError("refused")):
            self.assertEqual(probe.probe_models("http://llm.test/v1"), [])

    def test_classify_success_returns_models_and_no_reason(self) -> None:
        resp = httpx.Response(200, json={"data": [{"id": "a"}]}, request=httpx.Request("GET", "http://x"))
        with mock.patch("engine.scripts.probe.httpx.get", return_value=resp):
            self.assertEqual(probe._classify("http://llm.test/v1", "", 5.0), (["a"], None))

    def test_classify_reports_why_each_failure_happened(self) -> None:
        # The quickstart surfaces this reason so a failed probe isn't a blank
        # miss: each distinct cause gets a distinct, human-readable substring.
        req = httpx.Request("GET", "http://x")
        responses = {
            "no models": httpx.Response(200, json={"data": []}, request=req),
            "401": httpx.Response(401, json={}, request=req),
            "/v1": httpx.Response(404, text="nope", request=req),
        }
        for needle, resp in responses.items():
            with mock.patch("engine.scripts.probe.httpx.get", return_value=resp):
                models, reason = probe._classify("http://llm.test/v1", "", 5.0)
            self.assertEqual(models, [])
            self.assertIsNotNone(reason)
            self.assertIn(needle, reason or "")
        with mock.patch("engine.scripts.probe.httpx.get", side_effect=httpx.ConnectError("refused")):
            models, reason = probe._classify("http://llm.test/v1", "", 5.0)
        self.assertEqual(models, [])
        self.assertIn("could not connect", reason or "")


class ChecksTests(SimpleTestCase):
    """engine.W001 warns (never errors) when ENGINE_MODEL is unset, so `up` boots."""

    def test_warns_when_model_unset(self) -> None:
        with self.settings(ENGINE_MODEL=""):
            self.assertEqual([w.id for w in checks.engine_model_configured()], ["engine.W001"])

    def test_no_warning_when_model_set(self) -> None:
        with self.settings(ENGINE_MODEL="qwen2.5:7b"):
            self.assertEqual(checks.engine_model_configured(), [])


class RegistryTests(SimpleTestCase):
    def setUp(self) -> None:
        # register() swaps the eagerly-built module-global engine; restore the
        # real one after any test that overrides it.
        self.addCleanup(setattr, registry, "_engine", registry._engine)

    def test_only_default_kind_is_recognized(self) -> None:
        self.assertEqual(registry.kinds(), ["openai_compat"])

    def test_empty_kind_resolves_to_the_engine(self) -> None:
        self.assertIsInstance(registry.get(""), OpenAICompatEngine)

    def test_default_kind_returns_the_same_instance(self) -> None:
        self.assertIs(registry.get("openai_compat"), registry.get(""))

    def test_unknown_kind_raises_keyerror(self) -> None:
        with self.assertRaises(KeyError):
            registry.get("ollama")

    def test_register_overrides(self) -> None:
        fake = _engine()
        registry.register(fake)
        self.assertIs(registry.get(""), fake)


class ExternalContentPromptTests(SimpleTestCase):
    """external_content (a fetched linked article) is folded into the judge
    prompt only when the caller provides it."""

    def _user_prompt(self, **kw) -> str:
        params = _engine()._chat_params(model="m", instructions="rust", payload=PAYLOAD, **kw)
        return params["messages"][1]["content"]

    def test_external_content_included_when_given(self) -> None:
        prompt = self._user_prompt(external_content="THE FETCHED ARTICLE BODY")
        self.assertIn("THE FETCHED ARTICLE BODY", prompt)
        self.assertIn("[LINKED_ARTICLE]", prompt)

    def test_no_linked_article_section_without_external_content(self) -> None:
        self.assertNotIn("[LINKED_ARTICLE]", self._user_prompt())
