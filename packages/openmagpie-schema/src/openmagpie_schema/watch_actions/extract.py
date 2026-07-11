"""The extract kind: LLM hydration of declared fields config + result.

A pure HYDRATION node (a third family beside FILTER and DELIVERY): it runs
the engine over an item to pull a USER-DECLARED set of fields out of the
fuzzy source text and writes them, structured, onto the run's result. It
gates nothing and delivers nothing ; it always advances. Downstream
consumers read the persisted `extracted` blob off the run (e.g. a report
projecting `result.extracted.*`), NOT a later action (an action only ever
sees the feed item, never an upstream run's result).
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Any, ClassVar

from pydantic import BaseModel, Field, field_validator

from openmagpie_schema.watch_enums import WatchActionKind

from ._engine import EngineActionConfigBase, ExternalContentStatus
from .base import WatchActionConfigBase, WatchActionConfigSummary

# A field name must be a safe, stable key: it becomes `extracted.<name>` (the
# dot-path a report projects) and a JSON-schema property the engine fills, so
# disallow dots, whitespace, and emptiness.
_FIELD_NAME = re.compile(r"[A-Za-z0-9_-]+")  # used with fullmatch, so no ^/$ anchors


class ExtractField(BaseModel):
    """One field to pull out of an item. `name` is the key under
    `extracted` (and the `result.extracted.<name>` report column) ; `description`
    is what the engine should put there (e.g. "a one-line summary of the item").
    A typed list (not a free dict) so the engine can build a strict JSON schema
    from it."""

    # max_length bounds the JSON-schema property name + prompt line the engine
    # builds from it (cost): a slug never needs more.
    name: str = Field(max_length=64)
    # The per-field guidance the engine follows; non-empty (an empty one gives the
    # engine nothing to go on) and bounded -- it's the larger per-field prompt
    # driver, so with the field count it caps the config-built prompt size.
    description: str = Field(min_length=1, max_length=256)

    @field_validator("description", mode="before")
    @classmethod
    def _strip_description(cls, v: object) -> object:
        # Strip BEFORE the length check, so a whitespace-only " " (which would pass
        # min_length=1) collapses to "" and is rejected -- the "nothing to go on" case.
        return v.strip() if isinstance(v, str) else v

    @field_validator("name")
    @classmethod
    def _safe_key(cls, v: str) -> str:
        # fullmatch, not match: `match` lets a trailing newline ("foo\n") through
        # (`$` matches before it); the name becomes a JSON-schema property + a
        # report dot-path, so it must be a clean slug end to end.
        if not _FIELD_NAME.fullmatch(v):
            raise ValueError(
                f"field name {v!r} must match {_FIELD_NAME.pattern} (no dots/spaces; it becomes a result key)"
            )
        return v


class ExtractConfig(EngineActionConfigBase):
    """Config for a WatchAction with kind == 'extract'.

    Hydration only: the engine reads each item (optionally with its fetched
    linked-article text) and returns a value for every declared `field`,
    persisted under `result.extracted`. No threshold, no gate ; the run
    always SUCCEEDS so the chain advances. Domain-agnostic ; the field set
    and `instructions` are entirely operator-supplied."""

    CONFIG_KIND: ClassVar[str] = WatchActionKind.EXTRACT.value

    # The user-declared fields to pull out (required, non-empty ; an empty
    # set would extract nothing). Names must be unique (they're result keys).
    # max_length on a list caps the NUMBER of fields (<= 64), bounding the per-item
    # LLM call size (each field is a schema property + a prompt line); 64 is already
    # a very wide extraction.
    fields: list[ExtractField] = Field(max_length=64)
    # Optional free-form steering for the whole extraction ("focus on the most
    # recent event when several are mentioned"), distinct from the per-field descriptions.
    # Bounded too, so the config-built prompt size has a hard ceiling.
    instructions: str = Field(default="", max_length=2048)
    # engine + fetch_external_content are inherited from EngineActionConfigBase.

    model_config = {"extra": "ignore"}

    @field_validator("fields")
    @classmethod
    def _non_empty_unique(cls, v: list[ExtractField]) -> list[ExtractField]:
        if not v:
            raise ValueError("extract requires at least one field")
        names = [f.name for f in v]
        if len(set(names)) != len(names):
            raise ValueError("extract field names must be unique (they become result keys)")
        return v

    def redacted_dump(self) -> dict[str, Any]:
        """No secrets in an extract config (fields / instructions / engine
        are all non-secret), so a plain dump is safe."""
        return self.model_dump(mode="json")

    def summary(self) -> WatchActionConfigSummary:
        # Truncate the name list so the preview stays one-line-ish (up to 64 fields x
        # 64-char names would be ~4KB otherwise), like the semantic_filter summary.
        shown = [f.name for f in self.fields[:5]]
        names = ", ".join(shown) + (f", +{len(self.fields) - len(shown)} more" if len(self.fields) > len(shown) else "")
        return WatchActionConfigSummary(detail=f"{self.engine_label()} extract {len(self.fields)} fields: {names}")

    def merge_preserving(self, prior: WatchActionConfigBase) -> ExtractConfig:
        """Nothing to carry forward: an extract config has no masked secrets
        or runtime state, so the submitted config wins wholesale."""
        return self


class ExtractStatus(StrEnum):
    """Outcome of one hydration, recorded on the result so a SUCCEEDED run
    carries how complete its extraction was."""

    COMPLETE = "complete"  # every declared field came back with a value
    PARTIAL = "partial"  # some declared fields came back empty
    EMPTY = "empty"  # nothing extracted (every field empty)


class ExtractResult(BaseModel):
    """Result an extract run writes to WatchActionRun.result.

    `extracted` is the `{field name: value}` map (string values in v1 ; the
    report renders them as cells and projects `result.extracted.<name>`).
    `status` is how complete the extraction was: COMPLETE (every declared field
    filled), PARTIAL (some), EMPTY (none). The action always sets it explicitly ;
    the EMPTY default just covers a result built without one. `enrichment_status`
    records how the linked-article fetch fared (the article TEXT is never stored ;
    the fetch is lazy + ephemeral)."""

    extracted: dict[str, str] = Field(default_factory=dict)
    status: ExtractStatus = ExtractStatus.EMPTY
    enrichment_status: ExternalContentStatus = ExternalContentStatus.NOT_APPLICABLE


# The key the field map lives under on a run result (`ExtractResult.extracted`).
# A consumer projecting `result.<EXTRACTED_KEY>.<field>` (the report) imports this
# instead of hardcoding the string. A test pins it to the model's field name so a
# rename can't leave it stale (a module-level assert would be stripped under -O).
EXTRACTED_KEY = "extracted"

# The config keys a consumer reads off the opaque `config` blob to find the declared
# fields (e.g. the export building its `extracted.<name>` columns). Pinned to the
# model field names by tests -- like EXTRACTED_KEY -- so a rename can't silently
# leave a consumer reading a dead key (-> zero declared columns).
EXTRACT_FIELDS_KEY = "fields"  # ExtractConfig.fields
EXTRACT_FIELD_NAME_KEY = "name"  # ExtractField.name

# The extract-only fixed result key a consumer reads by NAME (the activity-get
# detail). The shared `enrichment_status` key lives in `_engine` (it's on both
# result models). Pinned to the model field by a test so a rename can't drop it.
EXTRACT_STATUS_KEY = "status"  # ExtractResult.status
