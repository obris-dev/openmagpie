"""OllamaEngine: calls a local Ollama server's /api/chat for relevance
scoring, with structured JSON output constrained by JudgmentJSON's
schema. Also exposes /api/tags for the listener-config policy check
that verifies a pinned `engine.model` is actually loaded.

`judge_stream` fans the per-item HTTP calls out with sliding-window
concurrency: as one call completes the next starts (no batch
boundaries). Sliding-window-bounded via `asyncio.Semaphore`; the
async loop runs in a background thread and bridges results back to
the sync iterator caller via `queue.Queue`. `judge_batch` is a
convenience wrapper that drains the stream into a list.
"""

from __future__ import annotations

import asyncio
import queue
import threading
import time
from collections.abc import Iterator
from typing import Any

import httpx
from pydantic import ValidationError

from events.observations import Observation
from listeners.models import Listener
from openmagpie_schema.engine import EngineStatus

from ..base import EngineModelInvalid, JudgeRequest, JudgmentJSON, JudgmentResult
from .prompts import CONTENT_TRUNCATE, SYSTEM_PROMPT, USER_PROMPT_TEMPLATE
from .responses import OllamaChatResponse, OllamaTagsResponse

# Recoverable failure modes for an LLM call. Captured by `judge_stream`'s
# internal try/except and returned as exception instances so the
# orchestrator can decide whether to retry next cycle.
_BATCH_RECOVERABLE = (httpx.HTTPError, ValidationError)

# Sentinel pushed to the result queue when the asyncio worker thread
# has finished (all tasks complete or stopped). Lets the sync iterator
# know to terminate cleanly.
_STREAM_DONE = object()


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
        """Drain `judge_stream` into a list in submission order.
        Convenience wrapper; callers that want streaming throughput
        should use `judge_stream` directly."""
        if not requests:
            return []
        results: list[JudgmentResult | Exception | None] = [None] * len(requests)
        for idx, result in self.judge_stream(requests):
            results[idx] = result
        # Any None left would be a bug in the streamer (every submitted
        # index must produce one result); cast keeps the type-checker happy.
        return [r for r in results if r is not None]

    def judge_stream(
        self,
        requests: list[JudgeRequest],
    ) -> Iterator[tuple[int, JudgmentResult | Exception]]:
        """Sliding-window streaming. Internally runs an asyncio loop in
        a daemon thread; the loop fans `requests` out with at most
        `self.concurrency` in flight (semaphore-bounded). As each
        completes, its `(idx, result)` lands on a sync `queue.Queue`
        which this iterator drains.

        Closing the iterator early (the orchestrator `break`s out of
        the for loop on a recoverable error) sets a stop flag; the
        worker stops submitting new requests but lets in-flight ones
        run to completion before the thread exits. Their results go
        nowhere, but the in-flight HTTP calls are honored rather than
        cancelled mid-flight (cancelling httpx mid-request can leave
        connections in awkward states)."""
        if not requests:
            return

        out_queue: queue.Queue = queue.Queue()
        stop = threading.Event()

        def run_loop() -> None:
            try:
                asyncio.run(self._stream_async(requests, out_queue, stop))
            finally:
                out_queue.put(_STREAM_DONE)

        worker = threading.Thread(target=run_loop, daemon=True, name="ollama-judge-stream")
        worker.start()

        try:
            while True:
                item = out_queue.get()
                if item is _STREAM_DONE:
                    return
                yield item
        finally:
            # Signal the worker to stop scheduling new requests. In-flight
            # ones drain naturally; the daemon thread dies on process exit.
            stop.set()

    async def _stream_async(
        self,
        requests: list[JudgeRequest],
        out_queue: queue.Queue,
        stop: threading.Event,
    ) -> None:
        """Fan `requests` out with at most `self.concurrency` in flight
        via an asyncio.Semaphore. Each completed coroutine pushes
        `(idx, result_or_exc)` onto the sync queue immediately.
        `return_exceptions=True` on the gather isn't enough on its own
        because we want results to land in the queue AS THEY HAPPEN,
        not after the slowest finishes."""
        sem = asyncio.Semaphore(self.concurrency)

        async def bounded(client: httpx.AsyncClient, idx: int, request: JudgeRequest) -> None:
            # Honor the stop flag before acquiring the semaphore so a
            # cancellation signal doesn't have to wait for an in-flight
            # call to release a slot.
            if stop.is_set():
                return
            async with sem:
                if stop.is_set():
                    return
                try:
                    result: JudgmentResult | Exception = await self._judge_one_async(client, request)
                except _BATCH_RECOVERABLE as exc:
                    result = exc
                out_queue.put((idx, result))

        async with httpx.AsyncClient(timeout=120.0) as client:
            tasks = [asyncio.create_task(bounded(client, i, r)) for i, r in enumerate(requests)]
            # gather drains everything; per-task exceptions can't propagate
            # because bounded() catches _BATCH_RECOVERABLE itself and any
            # other exception is a programming bug we want to surface.
            await asyncio.gather(*tasks)

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
