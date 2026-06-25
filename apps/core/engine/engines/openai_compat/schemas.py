"""The strict JSON Schemas the OpenAICompatEngine forces on structured-output
replies (the `json_schema.schema` payload). Kept beside `request.py` so `engine.py`
holds the request/response FLOW, not the output SHAPES.

A JSON Schema is declarative meta - a dict is its natural form, not a model we
dump. Strict mode (the OpenAI structured-output contract) requires
`additionalProperties: false` and EVERY property in `required`; `_strict_object`
stamps both so each builder states only its properties.
"""

from typing import Any

from openmagpie_schema.watch_actions import ExtractField

from ..base import JudgmentJSON


def _strict_object(properties: dict[str, Any]) -> dict[str, Any]:
    """A strict (closed, every-property-required) object schema over `properties`.
    `required` is derived from the keys, so the property set is the single source."""
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def judgment_schema() -> dict[str, Any]:
    """The relevance-judgment schema, from the `JudgmentJSON` parser model.

    Strips the keywords strict structured-output mode has historically rejected:
    score's `ge`/`le` -> `minimum`/`maximum`, and pydantic's `title`s. The bounds
    stay ON the model for the INBOUND parse (`model_validate_json` rejects an
    out-of-range score); they're just dropped from the OUTBOUND schema, which only
    shapes the reply. additionalProperties:false closes it for strict mode (kept off
    the model so the inbound parse stays lenient - the non-conforming-backend
    backstop)."""
    schema = JudgmentJSON.model_json_schema()
    schema.pop("title", None)
    for prop in schema.get("properties", {}).values():
        for unsupported in ("title", "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum"):
            prop.pop(unsupported, None)
    schema["additionalProperties"] = False
    return schema


def extraction_schema(fields: list[ExtractField]) -> dict[str, Any]:
    """One string property per declared field, all required (strict mode) - the
    model returns "" for ones it can't fill, instructed by the prompt."""
    return _strict_object({f.name: {"type": "string", "description": f.description} for f in fields})
