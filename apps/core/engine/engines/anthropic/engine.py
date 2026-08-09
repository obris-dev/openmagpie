"""AnthropicEngine: relevance scoring using the official `anthropic` client.
"""

import json
import time
from typing import Any, cast

from anthropic import Anthropic, APIStatusError, APIConnectionError, RateLimitError, APITimeoutError

from openmagpie_schema.engine import EngineStatus
from openmagpie_schema.watch_actions import ExtractField
from sources.payloads import SourcePayload

from ..base import EngineRequestRejected, ExtractionResult, JudgmentJSON, JudgmentResult
from ..openai_compat.prompts import (
    CONTENT_TRUNCATE,
    EXTRACT_INSTRUCTIONS_TEMPLATE,
    EXTRACT_SYSTEM_PROMPT,
    EXTRACT_USER_TEMPLATE,
    SYSTEM_PROMPT,
    USER_PROMPT_TEMPLATE,
    render_extract_fields,
    render_linked_article,
)
from ..openai_compat.schemas import extraction_schema, judgment_schema

_PROBE_TIMEOUT = 5.0
_CHAT_TIMEOUT = 120.0

class AnthropicEngine:
    kind = "anthropic"

    def __init__(self, *, base_url: str = "", default_model: str, api_key: str = "", max_retries: int = 0) -> None:
        self.default_model = default_model
        self.api_key = api_key
        
        kwargs: dict[str, Any] = {
            "api_key": self.api_key or "noauth",
            "max_retries": max_retries,
            "timeout": _CHAT_TIMEOUT,
        }
        if base_url:
            kwargs["base_url"] = base_url.rstrip("/")
            
        self._client = Anthropic(**kwargs)

    def _resolve_model(self, model: str | None) -> str:
        resolved_model = model or self.default_model
        if not resolved_model:
            raise EngineRequestRejected(
                "no model configured: set ENGINE_MODEL (or the action's engine.model)."
            )
        return resolved_model

    def _raise_for_error(self, exc: Exception) -> None:
        """Map Anthropic errors to transient (propagate) or permanent (EngineRequestRejected)."""
        if isinstance(exc, (APIConnectionError, APITimeoutError, RateLimitError)):
            raise exc
        
        if isinstance(exc, APIStatusError):
            if exc.status_code in (401, 403, 404, 400, 422):
                raise EngineRequestRejected(f"Anthropic API rejected request ({exc.status_code}): {exc.message}")
            raise exc
        raise exc

    def _complete(
        self, 
        model: str, 
        system_prompt: str, 
        user_prompt: str, 
        tool_name: str, 
        tool_schema: dict[str, Any]
    ) -> tuple[str, int]:
        started = time.perf_counter()
        
        # Anthropic tool schema expects JSON Schema, but without some top-level OpenAPI concepts.
        # We wrap our generated JSON schema as an Anthropic tool.
        tools = [
            {
                "name": tool_name,
                "description": "Output the result based on the requested schema.",
                "input_schema": tool_schema,
            }
        ]

        try:
            response = self._client.messages.create(
                model=model,
                system=system_prompt,
                messages=[
                    {"role": "user", "content": user_prompt}
                ],
                tools=tools, # type: ignore
                tool_choice={"type": "tool", "name": tool_name},
                max_tokens=1024,
                temperature=0.0,
            )
        except Exception as exc:
            self._raise_for_error(exc)
            
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        
        # Look for the tool use block
        content = ""
        if hasattr(response, 'content'):
            for block in response.content:
                if block.type == "tool_use" and block.name == tool_name:
                    content = json.dumps(block.input)
                    break
                    
        return content, elapsed_ms

    def judge(
        self,
        payload: SourcePayload,
        *,
        instructions: str,
        model: str | None = None,
        external_content: str | None = None,
    ) -> JudgmentResult:
        resolved_model = self._resolve_model(model)
        parts = render_linked_article(external_content or "")
        
        user_prompt = USER_PROMPT_TEMPLATE.format(
            instructions=instructions,
            source=payload.source,
            title=payload.title,
            content=payload.content[:CONTENT_TRUNCATE],
            external_section=parts.user_section,
        )
        system_prompt = SYSTEM_PROMPT.format(article_rule=parts.system_rule)

        content, elapsed_ms = self._complete(
            model=resolved_model,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            tool_name="judgment",
            tool_schema=judgment_schema(),
        )

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
        parts = render_linked_article(external_content or "")
        
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

        content, elapsed_ms = self._complete(
            model=resolved_model,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            tool_name="extraction",
            tool_schema=extraction_schema(fields),
        )

        raw = json.loads(content)
        
        from ..openai_compat.engine import OpenAICompatEngine
        
        return ExtractionResult(
            extracted=OpenAICompatEngine._coerce_extracted(raw, fields),
            model=resolved_model,
            latency_ms=elapsed_ms,
            raw_response=content,
        )

    def status(self) -> EngineStatus:
        """Probe the Anthropic endpoint for reachability and available models."""
        if not self.api_key or self.api_key == "noauth":
             return EngineStatus(
                kind=self.kind,
                default_model=self.default_model,
                available=False,
                unreachable_reason="No API key provided",
                how_to_fix="Set ENGINE_API_KEY in the environment.",
            )
             
        try:
            page = self._client.models.list()
            models = sorted(m.id for m in page.data)
            return EngineStatus(
                kind=self.kind,
                default_model=self.default_model,
                available=True,
                available_models=models,
            )
        except Exception as exc:
            return EngineStatus(
                kind=self.kind,
                default_model=self.default_model,
                available=False,
                unreachable_reason=f"API unreachable: {exc}",
            )
