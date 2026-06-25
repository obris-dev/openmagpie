"""The chat-completions request shape the OpenAICompatEngine builds and splats.

A typed builder for the universal-subset params (model, messages, temperature),
kept in its own module so `engine.py` reads as the request/response FLOW rather
than the request SHAPE. The structured-output `response_format` is layered on
after `as_params()` by the engine's `_apply_json_schema` hook - a dict-level seam
so a backend override can swap the mechanism - so it is deliberately not a field
here.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class ChatMessage(BaseModel):
    """One chat message, the universal {role, content} shape."""

    role: str
    content: str


class ChatRequest(BaseModel):
    """The chat-completions request: the universal-subset params we send. Built
    typed, then `as_params()` dumps it to the kwargs dict the client splats into
    `chat.completions.create(**...)`."""

    model: str
    messages: list[ChatMessage]
    # temperature=0 -> greedy decoding: same payload + prompt -> same output across
    # runs, so the prompt is what's under test, not LLM sampling noise.
    temperature: float = 0

    def as_params(self) -> dict[str, Any]:
        return self.model_dump()
