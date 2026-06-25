"""ExtractAction.run: the hydration happy path (extracted fields persisted, the
run SUCCEEDS), the COMPLETE/PARTIAL/EMPTY status, and the permanent-defect ERROR
paths (bad config, unhydratable item, unknown engine, engine rejection). The
engine is stubbed; no live LLM, no network (external fetch off in the config)."""

from __future__ import annotations

import ulid
from django.test import SimpleTestCase

from engine import registry as engine_registry
from engine.engines import EngineRequestRejected, ExtractionResult, JudgmentResult
from openmagpie_schema.engine import EngineStatus
from openmagpie_schema.watch_actions import (
    ENRICHMENT_STATUS_KEY,
    EXTRACT_FIELD_NAME_KEY,
    EXTRACT_FIELDS_KEY,
    EXTRACT_STATUS_KEY,
    EXTRACTED_KEY,
    ExtractConfig,
    ExtractField,
    ExtractResult,
    SemanticFilterResult,
)
from openmagpie_schema.watch_enums import DeliveryCadence, WatchActionRunState
from sources.connectors.hackernews.payloads import HackerNewsFeedPayload
from sources.payloads import SourcePayload
from watches import run_messages
from watches.actions.extract import ExtractAction
from watches.actions.protocol import ActionContext, ActionItem
from watches.models import WatchAction

FIELDS = [{"name": "person", "description": "who"}, {"name": "org", "description": "where"}]


class _StubEngine:
    """Stands in for the registered engine: extract() returns a canned result (or
    raises). Signatures match the Engine protocol; judge()/status() are unused
    here but present so the stub is structurally an Engine."""

    kind = "openai_compat"

    def __init__(self, *, extracted: dict[str, str] | None = None, raises: Exception | None = None) -> None:
        self._extracted = extracted or {}
        self._raises = raises

    def extract(
        self,
        payload: SourcePayload,
        *,
        fields: list[ExtractField],
        instructions: str = "",
        model: str | None = None,
        external_content: str | None = None,
    ) -> ExtractionResult:
        if self._raises is not None:
            raise self._raises
        return ExtractionResult(extracted=self._extracted, model="m", latency_ms=1, raw_response="{}")

    def judge(
        self,
        payload: SourcePayload,
        *,
        instructions: str,
        model: str | None = None,
        external_content: str | None = None,
    ) -> JudgmentResult:  # pragma: no cover - unused by these tests
        raise NotImplementedError

    def status(self) -> EngineStatus:  # pragma: no cover - unused by these tests
        raise NotImplementedError


def _item() -> ActionItem:
    return ActionItem(
        data=HackerNewsFeedPayload.sample().model_dump(mode="json"),
        key="hn:1",
        source_label="hn",
        source_kind="hn_feed",
    )


def _context() -> ActionContext:
    return ActionContext(watch_id="w", watch_name="n", delivery=DeliveryCadence.INSTANT)


def _action(config: dict) -> WatchAction:
    return WatchAction(id=ulid.ulid(), kind="extract", config=config)


class ExtractRunTests(SimpleTestCase):
    def setUp(self) -> None:
        # register() swaps the module-global engine; restore the real one after.
        self.addCleanup(setattr, engine_registry, "_engine", engine_registry._engine)

    def _run(self, stub: _StubEngine, *, config: dict | None = None, item: ActionItem | None = None):
        engine_registry.register(stub)
        # fetch off: the sample payload HAS an external_url, so leaving it on would
        # attempt a real fetch. The fetch path is covered in tests_external.py.
        cfg = config or {"fields": FIELDS, "fetch_external_content": False}
        return ExtractAction().run(_action(cfg), items=[item or _item()], context=_context())

    def test_extracted_persisted_and_succeeds(self) -> None:
        out = self._run(_StubEngine(extracted={"person": "Pat", "org": "Acme"}))
        self.assertEqual(out.state, WatchActionRunState.SUCCEEDED)
        self.assertEqual(out.result["extracted"], {"person": "Pat", "org": "Acme"})
        self.assertEqual(out.result["status"], "complete")

    def test_partial_status(self) -> None:
        out = self._run(_StubEngine(extracted={"person": "Pat", "org": ""}))
        self.assertEqual(out.state, WatchActionRunState.SUCCEEDED)
        self.assertEqual(out.result["status"], "partial")

    def test_empty_extraction_still_succeeds(self) -> None:
        # Nothing found is NOT a gate or an error: hydration always advances.
        out = self._run(_StubEngine(extracted={"person": "", "org": ""}))
        self.assertEqual(out.state, WatchActionRunState.SUCCEEDED)
        self.assertEqual(out.result["status"], "empty")

    def test_bad_config_errors(self) -> None:
        # Empty fields list -> ExtractConfig validation fails -> permanent ERRORED.
        out = self._run(_StubEngine(), config={"fields": [], "fetch_external_content": False})
        self.assertEqual(out.state, WatchActionRunState.ERRORED)
        self.assertEqual(out.error, run_messages.CONFIG_INVALID)

    def test_unknown_engine_errors(self) -> None:
        out = self._run(
            _StubEngine(), config={"fields": FIELDS, "fetch_external_content": False, "engine": {"kind": "nope"}}
        )
        self.assertEqual(out.state, WatchActionRunState.ERRORED)
        self.assertEqual(out.error, run_messages.ENGINE_UNAVAILABLE)

    def test_engine_rejection_errors(self) -> None:
        out = self._run(_StubEngine(raises=EngineRequestRejected("bad request")))
        self.assertEqual(out.state, WatchActionRunState.ERRORED)
        self.assertEqual(out.error, run_messages.ENGINE_REJECTED)

    def test_unhydratable_item_errors(self) -> None:
        bad = ActionItem(data={"source": "x", "kind": "totally_unknown"}, key="x:1", source_label="x", source_kind="x")
        out = self._run(_StubEngine(extracted={"person": "p"}), item=bad)
        self.assertEqual(out.state, WatchActionRunState.ERRORED)
        self.assertEqual(out.error, run_messages.ITEM_UNREADABLE)


class ExtractFieldValidationTests(SimpleTestCase):
    """ExtractField.name is a slug that becomes a JSON-schema property + report
    dot-path, so it must validate end to end (fullmatch, not match)."""

    def test_trailing_newline_is_rejected(self) -> None:
        from pydantic import ValidationError

        from openmagpie_schema.watch_actions import ExtractField

        with self.assertRaises(ValidationError):
            ExtractField(name="person\n", description="x")

    def test_plain_slug_is_accepted(self) -> None:
        from openmagpie_schema.watch_actions import ExtractField

        self.assertEqual(ExtractField(name="person", description="x").name, "person")

    def test_overlong_name_is_rejected(self) -> None:
        from pydantic import ValidationError

        with self.assertRaises(ValidationError):  # bounds the schema-property / prompt-line size
            ExtractField(name="x" * 65, description="d")

    def test_empty_or_overlong_description_is_rejected(self) -> None:
        from pydantic import ValidationError

        with self.assertRaises(ValidationError):  # empty -> zero guidance for the engine
            ExtractField(name="person", description="")
        with self.assertRaises(ValidationError):  # whitespace-only is still "nothing to go on"
            ExtractField(name="person", description="   ")
        with self.assertRaises(ValidationError):  # bounds the per-field prompt cost
            ExtractField(name="person", description="x" * 257)

    def test_too_many_fields_is_rejected(self) -> None:
        from pydantic import ValidationError

        with self.assertRaises(ValidationError):  # caps the per-item LLM call size
            ExtractConfig(fields=[ExtractField(name=f"f{i}", description="d") for i in range(65)])


class ExtractedKeyContractTests(SimpleTestCase):
    def test_extracted_key_is_a_real_extract_result_field(self) -> None:
        # EXTRACTED_KEY is what report consumers project (`result.<key>.<field>`); it
        # must stay the actual ExtractResult field name (a -O-proof guard, vs the
        # module-level assert that python -O would strip).
        self.assertIn(EXTRACTED_KEY, ExtractResult.model_fields)

    def test_config_keys_are_real_model_fields(self) -> None:
        # The export reads these off the opaque config blob to find the declared
        # fields; pin them to the real field names so a rename can't silently yield
        # zero columns.
        self.assertIn(EXTRACT_FIELDS_KEY, ExtractConfig.model_fields)
        self.assertIn(EXTRACT_FIELD_NAME_KEY, ExtractField.model_fields)

    def test_summary_truncates_a_long_field_list(self) -> None:
        # A many-field config's summary stays one-line-ish: first few names + "+K more".
        cfg = ExtractConfig(fields=[ExtractField(name=f"f{i}", description="d") for i in range(8)])
        detail = cfg.summary().detail
        self.assertIn("+3 more", detail)  # 8 fields, 5 shown
        self.assertNotIn("f5", detail)  # the 6th name is not spelled out

    def test_result_keys_are_real_model_fields(self) -> None:
        # The activity-get detail reads these by name; pin them so a rename can't
        # silently drop status / enrichment. enrichment_status is shared, so it must
        # stay a real field on BOTH result models.
        self.assertIn(EXTRACT_STATUS_KEY, ExtractResult.model_fields)
        self.assertIn(ENRICHMENT_STATUS_KEY, ExtractResult.model_fields)
        self.assertIn(ENRICHMENT_STATUS_KEY, SemanticFilterResult.model_fields)
