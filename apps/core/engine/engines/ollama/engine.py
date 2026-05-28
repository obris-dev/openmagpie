"""OllamaEngine: calls a local Ollama server's /api/chat for relevance
scoring, with structured JSON output constrained by JudgmentJSON's
schema. Also exposes /api/tags for the listener-config policy check
that verifies a pinned `engine.model` is actually loaded.

`judge_batch` fans the per-item HTTP calls out concurrently via
`asyncio` + `httpx.AsyncClient`; the sync-on-the-outside seam
(`asyncio.run` inside `judge_batch`) keeps Django callers sync. The
single-item `judge()` shares the same internal path via a batch of one.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx
from pydantic import ValidationError

from events.observations import Observation
from listeners.models import Listener
from openmagpie_schema.engine import EngineStatus

from ..base import EngineModelInvalid, JudgeRequest, JudgmentJSON, JudgmentResult
from .prompts import CONTENT_TRUNCATE, SYSTEM_PROMPT, USER_PROMPT_TEMPLATE
from .responses import OllamaChatResponse, OllamaTagsResponse


class OllamaEngine:
    """Calls a local Ollama server's /api/chat endpoint with structured JSON output."""

    kind = "ollama"

    def __init__(self, url: str, default_model: str, concurrency: int = 1) -> None:
        self.url = url.rstrip("/")
        # `default_model` is the fallback when a Listener's config leaves
        # `engine.model` empty. Named "default_model" (not "model") because
        # judge() also takes a per-call `model` override; calling the instance
        # attribute `self.model` made `model or self.model` read ambiguously.
        self.default_model = default_model
        # Orchestrator hint: max items it should submit per batch. The
        # engine itself fans out whatever it's given; this attribute is
        # the contract the orchestrator sizes its in-flight window
        # against. Must be matched by `OLLAMA_NUM_PARALLEL` on the
        # Ollama side or extra concurrency just queues server-side.
        self.concurrency = max(1, concurrency)

    # ── Single-item API (back-compat / one-off callers) ───────────────

    def judge(
        self,
        observation: Observation,
        listener: Listener,
        *,
        model: str | None = None,
    ) -> JudgmentResult:
        """Score one observation. Implemented on top of `judge_batch`
        with a one-item list so both APIs share one transport path."""
        results = self.judge_batch([JudgeRequest(observation=observation, listener=listener, model=model)])
        result = results[0]
        if isinstance(result, Exception):
            raise result
        return result

    # ── Batch API (the orchestrator's hot path) ───────────────────────

    def judge_batch(self, requests: list[JudgeRequest]) -> list[JudgmentResult | Exception]:
        """Score N observations concurrently against Ollama.

        Returns one entry per input in submission order; each entry is
        either a JudgmentResult (success) or a captured exception
        (recoverable LLM-call failure). Empty input is a no-op
        returning an empty list, no event loop spinup.
        """
        if not requests:
            return []
        return asyncio.run(self._judge_batch_async(requests))

    async def _judge_batch_async(self, requests: list[JudgeRequest]) -> list[JudgmentResult | Exception]:
        """Open one AsyncClient for the batch (so connections pool
        across the N calls) and gather every per-item coroutine."""
        async with httpx.AsyncClient(timeout=120.0) as client:
            return await asyncio.gather(
                *(self._judge_one_async(client, req) for req in requests),
                return_exceptions=True,
            )

    async def _judge_one_async(
        self,
        client: httpx.AsyncClient,
        request: JudgeRequest,
    ) -> JudgmentResult:
        """One Ollama /api/chat call. Mirrors the sync path's prompt
        construction and response parsing; only the transport (sync
        httpx -> async httpx) differs."""
        use_model = request.model or self.default_model
        payload = self._build_payload(request, use_model)
        started = time.perf_counter()
        response = await client.post(f"{self.url}/api/chat", json=payload)
        response.raise_for_status()
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        return self._parse_chat(response.text, use_model, elapsed_ms)

    # ── Shared internals ──────────────────────────────────────────────

    def _build_payload(self, request: JudgeRequest, use_model: str) -> dict[str, Any]:
        """Compose the /api/chat body. Shared by sync and async paths so
        there's exactly one place the prompt + structured-output shape
        is constructed."""
        user_prompt = USER_PROMPT_TEMPLATE.format(
            listener_instructions=str(request.listener.instructions),
            source=request.observation.source,
            title=request.observation.title,
            content=request.observation.content[:CONTENT_TRUNCATE],
        )
        # `format` here is Ollama's structured-output knob: when given a JSON
        # schema, Ollama constrains the model's output to conform to it. That's
        # what makes message.content a JSON string matching JudgmentJSON below.
        # If a model/Ollama-version ever ignores it, JudgmentJSON.model_validate_json
        # raises ValidationError, caught by the orchestrator's recoverable set.
        return {
            "model": use_model,
            "format": JudgmentJSON.model_json_schema(),
            "stream": False,
            # temperature=0 = greedy decoding. We want the same observation +
            # prompt to produce the same score across runs so the prompt itself
            # is what's under test, not LLM noise. (Tiny residual non-determinism
            # from inference parallelism is still possible but bounded.)
            "options": {"temperature": 0},
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
        }

    def _parse_chat(self, response_text: str, use_model: str, elapsed_ms: int) -> JudgmentResult:
        """Validate Ollama's wrapper + the structured JSON inside,
        return a JudgmentResult. ValidationError on a shape drift
        propagates as a recoverable error."""
        chat = OllamaChatResponse.model_validate_json(response_text)
        raw_content = chat.message.content
        parsed = JudgmentJSON.model_validate_json(raw_content)
        return JudgmentResult(
            score=parsed.score,
            reason=parsed.reason,
            model=use_model,
            latency_ms=elapsed_ms,
            raw_response=raw_content,
        )

    # ── Model availability / status ───────────────────────────────────

    def available_models(self) -> list[str]:
        """Names of models currently loaded on this Ollama server.

        Raises httpx.HTTPError on unreachable server and
        pydantic.ValidationError if Ollama's tags-response shape ever
        drifts (e.g. renamed `models` field). Both bubble up; the
        listener-config policy callsite wraps them in
        EngineModelInvalid with operator-facing detail.
        """
        response = httpx.get(f"{self.url}/api/tags", timeout=5.0)
        response.raise_for_status()
        tags = OllamaTagsResponse.model_validate_json(response.text)
        return [m.name for m in tags.models]

    def status(self) -> EngineStatus:
        """Probe /api/tags once; map success or any failure into an
        `EngineStatus`. Never raises: callers (the /v1/engines view,
        the quickstart wizard) render `unreachable_reason` and
        `how_to_fix` directly, so a probe error here is not the same
        as a server error."""
        try:
            loaded = self.available_models()
        except httpx.HTTPError as exc:
            return EngineStatus(
                kind=self.kind,
                default_model=self.default_model,
                available=False,
                unreachable_reason=f"Ollama at {self.url} unreachable ({type(exc).__name__}: {exc})",
                how_to_fix=(
                    f"Start Ollama (`ollama serve` or open the desktop app) and confirm it's "
                    f"reachable at {self.url}. If the URL is wrong, update `OLLAMA_URL` in the "
                    f"server's env and restart."
                ),
            )
        except ValidationError as exc:
            return EngineStatus(
                kind=self.kind,
                default_model=self.default_model,
                available=False,
                unreachable_reason=f"Ollama at {self.url} returned an unexpected /api/tags shape ({exc.error_count()} error(s))",
                how_to_fix=(
                    "Ollama's API likely drifted from what this server parses. Update Ollama "
                    "to a recent version, or open an issue against openmagpie so the parser "
                    "catches up."
                ),
            )
        return EngineStatus(
            kind=self.kind,
            default_model=self.default_model,
            available=True,
            available_models=sorted(loaded),
        )

    def validate_model(self, model: str) -> None:
        """Confirm `model` is loaded on this Ollama server. Engine-policy
        hook called from listener config save (see Engine protocol).
        Raises EngineModelInvalid when the server is unreachable OR the
        model isn't in the loaded set; message names the URL and the
        available list so the operator can fix the YAML or pull the
        model on the Ollama side.
        """
        try:
            loaded = self.available_models()
        except httpx.HTTPError as exc:
            raise EngineModelInvalid(f"can't validate engine.model: Ollama at {self.url} unreachable ({exc})") from exc
        if model not in loaded:
            raise EngineModelInvalid(
                f"engine.model {model!r} not loaded on Ollama at {self.url}; available: {sorted(loaded) or '(none)'}"
            )
