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
  3. Structured output goes through the overridable `_apply_structured_output`
     hook, so a backend that ever needs a different MECHANISM (e.g. vLLM `guided_*`
     via `extra_body`) is a one-method override - not a rewrite.
"""

import time
from typing import Any

from openai import (
    AuthenticationError,
    BadRequestError,
    NotFoundError,
    OpenAI,
    OpenAIError,
    PermissionDeniedError,
    UnprocessableEntityError,
)

from openmagpie_schema.engine import EngineStatus
from sources.payloads import SourcePayload

from ..base import EngineRequestRejected, JudgmentJSON, JudgmentResult
from .prompts import (
    CONTENT_TRUNCATE,
    EXTERNAL_CONTENT_TEMPLATE,
    EXTERNAL_CONTENT_TRUNCATE,
    SYSTEM_PROMPT,
    USER_PROMPT_TEMPLATE,
)

# Reachability/model-list probe timeout (s); the chat call gets a longer one
# since a local model can be slow to judge.
_PROBE_TIMEOUT = 5.0
_CHAT_TIMEOUT = 120.0


class OpenAICompatEngine:
    """Calls an OpenAI-compatible endpoint via the `openai` client with structured
    JSON output; the one engine that reaches any such backend."""

    kind = "openai_compat"

    def __init__(self, *, base_url: str, default_model: str, api_key: str = "") -> None:
        self.base_url = base_url.rstrip("/")
        # `default_model` is the fallback when a caller's config leaves
        # `engine.model` empty. Named "default_model" (not "model") because
        # judge() also takes a per-call `model` override; `model or self.model`
        # would read ambiguously.
        self.default_model = default_model
        self.api_key = api_key
        self._openai: OpenAI | None = None

    def _client(self) -> OpenAI:
        """The shared client (built once). `max_retries=0`: the drain owns retry
        semantics (a transient failure -> FAILED -> retried), so the SDK must not
        also retry and mask/duplicate that. A blank api_key becomes a placeholder
        because the SDK requires a non-empty one; local servers ignore it."""
        if self._openai is None:
            self._openai = OpenAI(
                base_url=self.base_url,
                api_key=self.api_key or "noauth",
                max_retries=0,
                timeout=_CHAT_TIMEOUT,
            )
        return self._openai

    def _apply_structured_output(self, params: dict[str, Any]) -> None:
        """Force the JSON shape via the standard OpenAI `response_format`
        (json_schema, strict) - the way every current mainstream backend accepts.
        THE override point: a backend that ever needs a different mechanism (native
        grammar via `extra_body`) overrides this whole method."""
        # Strict structured outputs need additionalProperties:false + all fields
        # required; JudgmentJSON's score+reason are already required, and we add the
        # closed-object constraint here (kept off the parser model so the inbound
        # parse stays lenient - the validation backstop).
        schema = {**JudgmentJSON.model_json_schema(), "additionalProperties": False}
        params["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": "judgment", "schema": schema, "strict": True},
        }

    def _chat_params(
        self,
        *,
        model: str,
        instructions: str,
        payload: SourcePayload,
        external_content: str | None = None,
    ) -> dict[str, Any]:
        # The linked-article section is rendered only when external_content is
        # given (the filter opted in and the item had an external link); else ""
        # leaves the prompt exactly as before.
        external_section = ""
        if external_content:
            external_section = EXTERNAL_CONTENT_TEMPLATE.format(
                external_content=external_content[:EXTERNAL_CONTENT_TRUNCATE]
            )
        user_prompt = USER_PROMPT_TEMPLATE.format(
            instructions=instructions,
            source=payload.source,
            title=payload.title,
            content=payload.content[:CONTENT_TRUNCATE],
            external_section=external_section,
        )
        params = {
            "model": model,
            # temperature=0 -> greedy decoding: same payload + prompt -> same score
            # across runs, so the prompt is what's under test, not LLM noise.
            "temperature": 0,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
        }
        self._apply_structured_output(params)
        return params

    def judge(
        self,
        payload: SourcePayload,
        *,
        instructions: str,
        model: str | None = None,
        external_content: str | None = None,
    ) -> JudgmentResult:
        # Per-call model override; None means "use this instance's default"
        # (settings.ENGINE_MODEL from env).
        use_model = model or self.default_model
        # No model anywhere (ENGINE_MODEL unset + no per-action pin) is a permanent
        # config defect -> ERRORED, not a model="" request the backend would 4xx on.
        if not use_model:
            raise EngineRequestRejected(
                "no model configured: set ENGINE_MODEL (or the action's engine.model). "
                "List your LLM's models with: python -m engine.scripts.probe <ENGINE_BASE_URL>"
            )
        params = self._chat_params(
            model=use_model, instructions=instructions, payload=payload, external_content=external_content
        )
        started = time.perf_counter()
        # Permanent 4xx config defects -> EngineRequestRejected (ERRORED, not
        # retried). Transient errors (RateLimitError/InternalServerError/
        # APITimeout/APIConnection and any other OpenAIError) propagate -> the
        # drain's recoverable FAILED.
        try:
            completion = self._client().chat.completions.create(**params)
        except (AuthenticationError, PermissionDeniedError) as exc:
            raise EngineRequestRejected(
                f"the LLM at {self.base_url} rejected auth ({exc.status_code}); check ENGINE_API_KEY"
            ) from exc
        except NotFoundError as exc:
            raise EngineRequestRejected(
                f"the LLM at {self.base_url} has no such endpoint/model ({exc.status_code}); "
                f"check ENGINE_BASE_URL and ENGINE_MODEL"
            ) from exc
        except (BadRequestError, UnprocessableEntityError) as exc:
            raise EngineRequestRejected(
                f"the LLM at {self.base_url} rejected the request as malformed ({exc.status_code}); "
                f"it may not support OpenAI structured outputs (json_schema) - check ENGINE_BASE_URL/ENGINE_MODEL"
            ) from exc
        elapsed_ms = int((time.perf_counter() - started) * 1000)

        # A loosely-compatible backend may omit choices, the message, or its
        # content; handle each explicitly so a missing field is an empty string
        # (-> JudgmentJSON validation fails -> recoverable transient FAILED), not
        # an AttributeError that only lands in the right bucket by luck.
        choice = completion.choices[0] if completion.choices else None
        message = choice.message if choice else None
        content = (message.content if message else None) or ""
        parsed = JudgmentJSON.model_validate_json(content)

        return JudgmentResult(
            score=parsed.score,
            reason=parsed.reason,
            model=use_model,
            latency_ms=elapsed_ms,
            raw_response=content,
        )

    def available_models(self) -> list[str]:
        """Model ids the endpoint serves (`models.list()` -> `data[].id`).

        Raises an `openai.OpenAIError` subclass when unreachable / unauthorized /
        shape-drifted; status() catches it and maps it to operator-facing detail."""
        page = self._client().with_options(timeout=_PROBE_TIMEOUT).models.list()
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
