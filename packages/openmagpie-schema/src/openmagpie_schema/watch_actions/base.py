"""Shared base for every per-kind WatchAction config + the CLI summary.

Each concrete kind (semantic_filter, webhook, log) lives in its own
sibling module and subclasses `WatchActionConfigBase`. The base declares
the read-path contract every kind MUST implement so a new kind can't ship
a silent hole (a blank preview, a secret-leaking dump).
"""

from __future__ import annotations

from typing import Any, ClassVar

from pydantic import BaseModel


class WatchActionConfigSummary(BaseModel):
    """Display-only projection of an action config for the CLI preview.

    Built server-side from the typed config (the only place that knows
    the schema), so the CLI prints it without parsing the `config` blob ;
    no shadow schema on the client. `detail` is presentation, not a
    contract ; the action's `kind` is carried separately (the column /
    wire), so it isn't repeated here."""

    detail: str = ""


class WatchActionConfigBase(BaseModel):
    """Base for every action-kind config.

    Declares the read-path contract every kind MUST implement. No working
    defaults: a silent `summary()` shows a blank preview and a default
    `redacted_dump()` would leak a future kind's secrets. Fail loudly
    here, don't ship a silent hole. Mirrors `FeedConfig`.

    No `kind` field: the discriminator lives on the WatchAction row /
    write envelope, and the registry maps it to the right subclass. The
    concrete config is the pure kind-specific shape."""

    # The action-kind string this config registers under (used by
    # watches.registry.register). A ClassVar constant, NOT a model field; every
    # concrete kind overrides it. Empty on the base so a subclass that forgets
    # it fails loudly at register() rather than registering under "".
    CONFIG_KIND: ClassVar[str] = ""

    def redacted_dump(self) -> dict[str, Any]:
        raise NotImplementedError(
            f"{type(self).__name__} must implement redacted_dump() (no safe default: the fallback would leak secrets)"
        )

    def summary(self) -> WatchActionConfigSummary:
        raise NotImplementedError(f"{type(self).__name__} must implement summary()")

    def merge_preserving(self, prior: WatchActionConfigBase) -> WatchActionConfigBase:
        """Edit round-trip: return self with state that must NOT reset on
        an edit, carried from `prior` (e.g. a webhook's masked secret). No
        safe default: a silent passthrough would corrupt secrets to the
        redaction sentinel."""
        raise NotImplementedError(
            f"{type(self).__name__} must implement merge_preserving() (no safe default: would corrupt secrets)"
        )

    def has_masked_secret(self) -> bool:
        """Whether this config still carries a masked secret (the operator
        left a redaction sentinel in place). Default False ; a kind with no
        secrets never does. The chain-replace guard uses this to refuse an
        ambiguous secret pairing; secret-bearing kinds override it."""
        return False
