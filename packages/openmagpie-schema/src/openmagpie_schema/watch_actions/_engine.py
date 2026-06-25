"""Shared primitives for the LLM-backed action kinds (semantic_filter, extract):
the engine + model selection, the config base both kinds extend, and the
linked-article enrichment status their results carry.

Its own sibling module (not a concrete kind's), so neither kind imports its shared
base from the other -- the module-per-capability rule, mirroring `_delivery.py` /
`_secrets.py`. Import these via the package `__init__`, not this module.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field

from .base import WatchActionConfigBase


class EngineSpec(BaseModel):
    """Which engine + model an LLM-backed action uses.

    `kind == ""` means "use the server default" ; the server fills it
    from settings and rejects an unregistered kind (policy ; the pure
    package can't know the registry). `model`, when non-empty, is the
    per-call model override the engine runs with (else the engine's
    server-side default).
    """

    kind: str = ""
    model: str = ""


class EngineActionConfigBase(WatchActionConfigBase):
    """Shared config for the LLM-backed kinds (semantic_filter, extract): the
    engine + model selection and the opt-in linked-article fetch. Lets those
    actions share one prepare path (load config -> hydrate item -> resolve engine
    -> fetch external content) instead of duplicating it per kind."""

    # default = EngineSpec(kind=""); the server fills the real default kind from
    # settings + validates it (policy; the pure package can't know the registry).
    engine: EngineSpec = Field(default_factory=EngineSpec)
    # When the item has an `external_url` (an off-site link, e.g. an HN link post),
    # fetch that page and fold its readable text into the LLM call so a bare link is
    # judged on its substance, not just the title. ON by default; set false to skip
    # the fetch. No-ops when the item has no external_url (Reddit, Ask HN, RSS).
    fetch_external_content: bool = True

    def engine_label(self) -> str:
        """The engine + model rendered for a config `summary()`: `engine(default)`
        for the empty 'use server default' kind, else `kind | model` (or
        `engine(kind)` when no model override). One place for the "default"
        placeholder, shared by every engine-backed kind's summary."""
        kind = self.engine.kind or "default"
        return f"{kind} | {self.engine.model}" if self.engine.model else f"engine({kind})"


# The result field both engine-backed kinds carry for linked-article enrichment
# provenance (`*Result.enrichment_status`). A by-name reader (the activity-get
# detail) imports this; a test pins it to both result models so a rename can't
# leave it stale. Lives here (with ExternalContentStatus) since the field is shared.
ENRICHMENT_STATUS_KEY = "enrichment_status"


class ExternalContentStatus(StrEnum):
    """How the linked-article enrichment fared for one run, recorded on the result
    so a finished run carries its own provenance: run WITH the article, or without
    it (none to fetch / disabled / the fetch yielded nothing). A run is never failed
    for missing enrichment ; this is the status of it."""

    NOT_APPLICABLE = "not_applicable"  # item had no external_url (Reddit, Ask HN, RSS)
    DISABLED = "disabled"  # fetch_external_content was off
    INCLUDED = "included"  # fetched + extracted, folded into the LLM call
    UNAVAILABLE = "unavailable"  # the fetch FAILED (network / HTTP error, blocked host, timeout, oversize)
    MISSING = "missing"  # fetched OK, but no usable article text came out (paywall / JS-only / non-article)
