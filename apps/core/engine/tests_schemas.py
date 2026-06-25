"""The structured-output JSON schemas (openai_compat/schemas.py): the outbound
shapes the engine forces on judge / extract replies. Split from tests.py (the
engine-CALL behavior) to keep each module under the length cap."""

from __future__ import annotations

from django.test import SimpleTestCase

from engine.engines.openai_compat.schemas import judgment_schema


class JudgmentSchemaTests(SimpleTestCase):
    def test_strips_score_bounds_and_titles(self) -> None:
        # score's ge/le must not leak as minimum/maximum (strict mode has rejected
        # those); pydantic `title`s stripped too. Bounds stay on the model for the
        # inbound parse; this is only the OUTBOUND shape.
        schema = judgment_schema()
        score = schema["properties"]["score"]
        self.assertNotIn("minimum", score)
        self.assertNotIn("maximum", score)
        self.assertNotIn("title", score)
        self.assertNotIn("title", schema)  # root title too
        self.assertFalse(schema["additionalProperties"])  # still closed for strict mode
