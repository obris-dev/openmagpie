"""Mapping an OpenAI-client failure to the engine's outcome.

Which SDK exception is a PERMANENT config defect (raised as EngineRequestRejected,
which the drain records as terminal ERRORED and never retries) vs a TRANSIENT failure
(re-raised so the drain records a recoverable FAILED and retries it). Its own module
because the classification has grown (auth, endpoint/model, malformed body,
temperature-locked reasoning models, rate limits) and the engine's `_complete` should
read as the request FLOW, not the error taxonomy.
"""

from __future__ import annotations

import logging
from typing import Any, NoReturn

from openai import (
    AuthenticationError,
    BadRequestError,
    NotFoundError,
    OpenAIError,
    PermissionDeniedError,
    RateLimitError,
    UnprocessableEntityError,
)

from ..base import EngineRequestRejected

logger = logging.getLogger("engine")


def raise_for_completion_error(exc: OpenAIError, *, base_url: str, params: dict[str, Any]) -> NoReturn:
    """Classify a chat-completion failure and always raise: EngineRequestRejected for a
    permanent 4xx config defect, or the original exception re-raised for a transient one
    (RateLimit / 5xx / timeout / connection) that the drain retries. `params` carries the
    request so the temperature-rejection message can name the value we sent."""
    if isinstance(exc, AuthenticationError | PermissionDeniedError):
        raise EngineRequestRejected(
            f"the LLM at {base_url} rejected auth ({exc.status_code}); check ENGINE_API_KEY"
        ) from exc
    if isinstance(exc, NotFoundError):
        raise EngineRequestRejected(
            f"the LLM at {base_url} has no such endpoint/model ({exc.status_code}); "
            f"check ENGINE_BASE_URL and ENGINE_MODEL"
        ) from exc
    if isinstance(exc, BadRequestError | UnprocessableEntityError):
        _raise_bad_request(exc, base_url=base_url, params=params)
    if isinstance(exc, RateLimitError):
        # A 429 that outlived the SDK's own backoff (ENGINE_MAX_RETRIES). Emit a distinct
        # WARNING so it stays greppable while tuning WATCH_RUN_DRAIN_CONCURRENCY, rather
        # than blending into the generic transient-failure log ; still transient below.
        logger.warning(
            "engine rate-limited (429) at %s, past its retries. Lower WATCH_RUN_DRAIN_CONCURRENCY "
            "or raise ENGINE_MAX_RETRIES if this persists.",
            base_url,
        )
        raise exc
    # InternalServerError / APITimeoutError / APIConnectionError / any other OpenAIError:
    # transient, so re-raise unchanged and let the drain record a recoverable FAILED.
    raise exc


def _raise_bad_request(
    exc: BadRequestError | UnprocessableEntityError, *, base_url: str, params: dict[str, Any]
) -> NoReturn:
    """A 400/422. A 4xx that refuses the temperature we send means deterministic (greedy)
    scoring is off on this model; say so plainly instead of blaming structured outputs.
    Trust OpenAI's `param` (where temperature is actually locked); the text fallback is
    refusal-shaped so a multi-param 400 or a payload that merely echoes "temperature"
    can't misfire it (non-OpenAI backends may not set `param`)."""
    text = str(exc).lower()
    if getattr(exc, "param", None) == "temperature" or (
        "temperature" in text and ("does not support" in text or "unsupported" in text)
    ):
        raise EngineRequestRejected(
            f"the LLM at {base_url} rejected temperature={params.get('temperature')!r} "
            f"({exc.status_code}): this model accepts only its default temperature, so "
            f"deterministic (greedy) scoring is unavailable and its judgments vary between "
            f"runs on the same input. Set ENGINE_MODEL to a model that accepts temperature=0 "
            f"for reproducible scores (some reasoning models lock temperature to their default)."
        ) from exc
    raise EngineRequestRejected(
        f"the LLM at {base_url} rejected the request as malformed ({exc.status_code}); "
        f"it may not support OpenAI structured outputs (json_schema) - check ENGINE_BASE_URL/ENGINE_MODEL"
    ) from exc
