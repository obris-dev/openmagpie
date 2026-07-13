"""OpenAICompatEngine: relevance scoring against any OpenAI-compatible LLM server,
driven by the OFFICIAL `openai` client pointed at the backend's `/v1` base URL.

Every backend (Ollama's `/v1`, vLLM, OpenAI, llama.cpp's server, LM Studio, hosted
providers) documents the same integration: construct `OpenAI(base_url=...,
api_key=...)` and call it. So we consume them THROUGH that client - it owns
endpoint construction (`/chat/completions`, `/models`), request/response shapes,
and a typed exception hierarchy - rather than hand-rolling HTTP or trying to
identify "which backend." The operator gives us an endpoint and (maybe) a key;
the OpenAI spec handles the rest, and `models.list()` is the "can I talk to your
LLM?" validate call.

Why this is safe for ANYTHING claiming OpenAI compatibility (the claim is a fuzzy
promise, not a contract):
  1. We depend only on the universal subset - chat-completions with `messages` +
     `temperature`, and `models.list()` - never the long tail (logprobs,
     tool_choice, n, ...) that backends commonly leave unimplemented.
  2. We don't assume the fuzzy parts work - the JSON schema is ALSO in the system
     prompt, and `JudgmentJSON.model_validate_json` validates the reply whether or
     not the backend honored `response_format`. A backend that silently ignores it
     still works; one that rejects it surfaces a clear `EngineRequestRejected`.
  3. Structured output goes through the overridable `_apply_json_schema`
     hook, so a backend that ever needs a different MECHANISM (e.g. vLLM `guided_*`
     via `extra_body`) is a one-method override - not a rewrite.
"""

import json
import time
from typing import Any, cast

from openai import OpenAI, OpenAIError

from openmagpie_schema.engine import EngineStatus
from openmagpie_schema.watch_actions import ExtractField
from sources.payloads import SourcePayload

from ..base import EngineRequestRejected, ExtractionResult, JudgmentJSON, JudgmentResult
from .errors import raise_for_completion_error
from .prompts import (
    CONTENT_TRUNCATE,
    EXTRACT_INSTRUCTIONS_TEMPLATE,
    EXTRACT_SYSTEM_PROMPT,
    EXTRACT_USER_TEMPLATE,
    SYSTEM_PROMPT,
    USER_PROMPT_TEMPLATE,
    render_extract_fields,
    render_linked_article,
)
from .request import ChatMessage, ChatRequest
from .schemas import extraction_schema, judgment_schema

# Reachability/model-list probe timeout (s); the chat call gets a longer one
# since a local model can be slow to judge.
_PROBE_TIMEOUT = 5.0
_CHAT_TIMEOUT = 120.0

# Names for the structured-output `json_schema` blocks (the OpenAI `json_schema.name`
# label), one per output path. Free-form, but named here so the two call sites don't
# carry bare literals.
_JUDGMENT_SCHEMA_NAME = "judgment"
_EXTRACTION_SCHEMA_NAME = "extraction"


class OpenAICompatEngine:
    """Calls an OpenAI-compatible endpoint via the `openai` client with structured
    JSON output; the one engine that reaches any such backend."""

    kind = "openai_compat"

    def __init__(self, *, base_url: str, default_model: str, api_key: str = "", max_retries: int = 0) -> None:
        self.base_url = base_url.rstrip("/")
        # `default_model` is the fallback when a caller's config leaves
        # `engine.model` empty. Named "default_model" (not "model") because
        # judge() also takes a per-call `model` override; `model or self.model`
        # would read ambiguously.
        self.default_model = default_model
        self.api_key = api_key
        # Built once here, not lazily: the engine is constructed at registry init on the
        # startup thread before any drain worker exists, so eager construction is race-free
        # where a lazy check-then-set would let N worker threads each build a client (N-1
        # orphaned). The client opens no sockets until first use. max_retries
        # (ENGINE_MAX_RETRIES) is the SDK's own exponential backoff for transient failures
        # and 429s, so a rate-limit burst makes a worker wait rather than fast-fail and let
        # the drain instantly claim the next (also-doomed) run, burning attempts queue-wide.
        # A blank api_key becomes a placeholder because the SDK requires a non-empty one;
        # local servers ignore it.
        self._openai = OpenAI(
            base_url=self.base_url,
            api_key=self.api_key or "noauth",
            max_retries=max_retries,
            timeout=_CHAT_TIMEOUT,
        )

    def _apply_json_schema(self, params: dict[str, Any], *, name: str, schema: dict[str, Any]) -> None:
        """Force a JSON shape via the standard OpenAI `response_format`
        (json_schema, strict) - the way every current mainstream backend accepts.
        THE override point: a backend that ever needs a different mechanism (native
        grammar via `extra_body`) overrides this whole method. Strict structured
        outputs need additionalProperties:false + all fields required; callers pass
        a schema already satisfying that."""
        params["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": name, "schema": schema, "strict": True},
        }

    def _resolve_model(self, model: str | None) -> str:
        """The per-call model, or the instance default. No model anywhere
        (ENGINE_MODEL unset + no per-action pin) is a permanent config defect ->
        EngineRequestRejected (ERRORED), not a model="" request the backend 4xx's on."""
        resolved_model = model or self.default_model
        if not resolved_model:
            raise EngineRequestRejected(
                "no model configured: set ENGINE_MODEL (or the action's engine.model). "
                "List your LLM's models with: python -m engine.scripts.probe <ENGINE_BASE_URL>"
            )
        return resolved_model

    def _complete(self, params: dict[str, Any]) -> tuple[str, int]:
        """Run one chat completion and return (reply_text, elapsed_ms). An SDK error is
        classified by `raise_for_completion_error`: a permanent 4xx config defect becomes
        EngineRequestRejected (ERRORED, not retried), a transient one (RateLimit / 5xx /
        timeout / connection) is re-raised -> the drain's recoverable FAILED. A loosely
        compatible backend may omit choices / the message / its content; each missing
        piece becomes an empty string so the caller's parse fails into a transient FAILED,
        not an AttributeError."""
        started = time.perf_counter()
        try:
            completion = self._openai.chat.completions.create(**params)
        except OpenAIError as exc:
            raise_for_completion_error(exc, base_url=self.base_url, params=params)
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        choice = completion.choices[0] if completion.choices else None
        message = choice.message if choice else None
        content = (message.content if message else None) or ""
        return content, elapsed_ms

    def _base_params(self, *, model: str, system_prompt: str, user_prompt: str) -> dict[str, Any]:
        """The typed base chat request (model, greedy temperature, system+user
        messages) dumped to the kwargs dict the client splats. Callers layer the
        structured-output schema on via `_apply_json_schema`."""
        return ChatRequest(
            model=model,
            messages=[
                ChatMessage(role="system", content=system_prompt),
                ChatMessage(role="user", content=user_prompt),
            ],
        ).as_params()

    def _chat_params(
        self,
        *,
        model: str,
        instructions: str,
        payload: SourcePayload,
        external_content: str | None = None,
    ) -> dict[str, Any]:
        # render_linked_article handles truncation, the nonce-fenced block, and the
        # paired system rule; empty external_content -> empty fragments, leaving the
        # prompt exactly as it was pre-enrichment.
        parts = render_linked_article(external_content or "")
        user_prompt = USER_PROMPT_TEMPLATE.format(
            instructions=instructions,
            source=payload.source,
            title=payload.title,
            content=payload.content[:CONTENT_TRUNCATE],
            external_section=parts.user_section,
        )
        params = self._base_params(
            model=model,
            system_prompt=SYSTEM_PROMPT.format(article_rule=parts.system_rule),
            user_prompt=user_prompt,
        )
        self._apply_json_schema(params, name=_JUDGMENT_SCHEMA_NAME, schema=judgment_schema())
        return params

    def _extract_params(
        self,
        *,
        model: str,
        fields: list[ExtractField],
        instructions: str,
        payload: SourcePayload,
        external_content: str | None = None,
    ) -> dict[str, Any]:
        parts = render_linked_article(external_content or "")
        # Free-form steering is optional; empty leaves the user prompt starting at
        # the item. instructions/content are .format ARGS (inserted literally), so
        # any braces inside them stay inert.
        instructions_section = (
            EXTRACT_INSTRUCTIONS_TEMPLATE.format(instructions=instructions) if instructions.strip() else ""
        )
        user_prompt = EXTRACT_USER_TEMPLATE.format(
            instructions_section=instructions_section,
            source=payload.source,
            title=payload.title,
            content=payload.content[:CONTENT_TRUNCATE],
            external_section=parts.user_section,
        )
        field_lines = render_extract_fields([(f.name, f.description) for f in fields])
        system_prompt = EXTRACT_SYSTEM_PROMPT.format(article_rule=parts.system_rule, field_lines=field_lines)
        params = self._base_params(model=model, system_prompt=system_prompt, user_prompt=user_prompt)
        self._apply_json_schema(params, name=_EXTRACTION_SCHEMA_NAME, schema=extraction_schema(fields))
        return params

    def judge(
        self,
        payload: SourcePayload,
        *,
        instructions: str,
        model: str | None = None,
        external_content: str | None = None,
    ) -> JudgmentResult:
        resolved_model = self._resolve_model(model)
        params = self._chat_params(
            model=resolved_model, instructions=instructions, payload=payload, external_content=external_content
        )
        content, elapsed_ms = self._complete(params)
        # Missing/short field -> JudgmentJSON validation raises -> recoverable
        # transient FAILED (the lenient-parse backstop for backends that ignore
        # response_format).
        parsed = JudgmentJSON.model_validate_json(content)
        return JudgmentResult(
            score=parsed.score,
            reason=parsed.reason,
            model=resolved_model,
            latency_ms=elapsed_ms,
            raw_response=content,
        )

    def extract(
        self,
        payload: SourcePayload,
        *,
        fields: list[ExtractField],
        instructions: str = "",
        model: str | None = None,
        external_content: str | None = None,
    ) -> ExtractionResult:
        resolved_model = self._resolve_model(model)
        params = self._extract_params(
            model=resolved_model,
            fields=fields,
            instructions=instructions,
            payload=payload,
            external_content=external_content,
        )
        content, elapsed_ms = self._complete(params)
        # Malformed / non-JSON reply -> json.loads raises -> recoverable transient
        # FAILED (mirrors judge's validation backstop for backends that ignore the
        # json_schema). A valid-JSON-but-wrong-shape reply is coerced below.
        raw = json.loads(content)
        return ExtractionResult(
            extracted=self._coerce_extracted(raw, fields),
            model=resolved_model,
            latency_ms=elapsed_ms,
            raw_response=content,
        )

    @staticmethod
    def _coerce_extracted(raw: object, fields: list[ExtractField]) -> dict[str, str]:
        """Reduce a parsed reply to exactly the declared keys, string-valued. A
        non-object reply, a missing key, or a null becomes "" for that field; extra
        keys the model invented are dropped. So the result shape is always the
        declared field set regardless of how loosely the backend complied. `raw` is
        a json.loads result: `object`, not `Any`, so the isinstance narrowing below
        is enforced rather than assumed."""
        # Confirmed an object -> its JSON keys are strings (values stay unknown and
        # are narrowed per-field below); cast satisfies the type checker for that.
        mapping = cast(dict[str, object], raw) if isinstance(raw, dict) else {}
        out: dict[str, str] = {}
        for f in fields:
            value = mapping.get(f.name, "")
            if value is None:
                out[f.name] = ""
            elif isinstance(value, str):
                out[f.name] = value
            else:
                # Only reachable on a non-strict backend (the schema declares string
                # fields): a dict/list, or a bool/number. Serialize as JSON so it's
                # `true` / `123` / `["a"]`, never Python repr ("True").
                out[f.name] = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        return out

    def available_models(self) -> list[str]:
        """Model ids the endpoint serves (`models.list()` -> `data[].id`).

        Raises an `openai.OpenAIError` subclass when unreachable / unauthorized /
        shape-drifted; status() catches it and maps it to operator-facing detail."""
        page = self._openai.with_options(timeout=_PROBE_TIMEOUT).models.list()
        return [m.id for m in page.data]

    def status(self) -> EngineStatus:
        """Probe the model list once ("can I talk to your LLM?"); map success or any
        failure into an `EngineStatus`. Never raises - callers (the /v1/engines view,
        a pre-flight UI) render `unreachable_reason` / `how_to_fix` directly."""
        try:
            loaded = self.available_models()
        except OpenAIError as exc:
            return EngineStatus(
                kind=self.kind,
                default_model=self.default_model,
                available=False,
                unreachable_reason=f"the LLM at {self.base_url} is unreachable or returned an unexpected response ({type(exc).__name__}: {exc})",
                how_to_fix=(
                    f"Confirm an OpenAI-compatible LLM is serving at {self.base_url} (its `/v1` base URL). "
                    f"Fix `ENGINE_BASE_URL` (and `ENGINE_API_KEY` if it needs auth) in the server's env and restart."
                ),
            )
        return EngineStatus(
            kind=self.kind,
            default_model=self.default_model,
            available=True,
            available_models=sorted(loaded),
        )
