"""Pure typed config + wire schemas for a Feed.

SHARED, zero-Django source of truth (imported by core *and* the magpie
CLI). A Feed is a curated set of Source rows a Watch subscribes to:
the Feed owns the poll loop + a readable item log; per-source state
(spec + watermark + meta + field_map) lives on `feeds.Source` rows.
This module carries only *shape* + pure transforms; the
Django/settings-coupled *policy* (no future watermark, retention
bounds) lives in core `feeds.policy`.
"""

from datetime import datetime
from typing import Annotated, Any, ClassVar, Literal

from pydantic import BaseModel, Field

from .configs import SourceSpec
from .wire import ConfigBlob

# ── Config (write-path `data` blob, keyed by kind) ────────────────────────


class FeedConfigSummary(BaseModel):
    """Display-only projection of a feed config for the CLI preview.

    Built server-side from the typed config (the only place that knows
    the schema) so the CLI prints it without parsing the `data` blob.
    Curated feeds emit an empty summary because all per-source state
    surfaces via FeedView.sources / source_count; the class stays as
    a hook for future kinds that have non-source-shaped config to
    project for the CLI."""


class FeedConfig(BaseModel):
    """Base for every feed-kind config.

    Declares the contract every kind MUST implement; no working defaults
    (a silent default would show a blank preview or reset watermarks)."""

    def redacted_dump(self) -> dict[str, Any]:
        raise NotImplementedError(
            f"{type(self).__name__} must implement redacted_dump() (no safe default: the fallback could leak secrets)"
        )

    def summary(self) -> FeedConfigSummary:
        raise NotImplementedError(f"{type(self).__name__} must implement summary()")

    def merge_preserving(self, prior: "FeedConfig") -> "FeedConfig":
        """Edit round-trip: return self with state that must NOT reset
        on an edit carried over from `prior`. Curated feeds carry no
        such state at the config layer (per-source watermarks live on
        Source rows), so they return `self`. Future secret-bearing
        kinds override to restore masked values."""
        raise NotImplementedError(f"{type(self).__name__} must implement merge_preserving()")


class CuratedFeedConfig(FeedConfig):
    """Schema for Feed.data when Feed.kind == 'curated'.

    Sources are user-maintained ; one Source row per place data comes
    from, regardless of whether they were typed in by hand or generated
    by an external script. Watermarks + per-source metadata live on the
    Source rows; this config only carries feed-level knobs.

    `default_field_map` is a connector-readable hint dictionary that
    applies to every source in the feed; per-source overrides on a
    Source row's `field_map` take precedence. Keys a connector doesn't
    recognise are ignored."""

    FEED_KIND: ClassVar[str] = "curated"

    # Item-log retention window. Bounds checked in policy ([1, 365]).
    retention_days: int = 30
    # Connector-readable defaults; row-level `field_map` overrides per key.
    default_field_map: dict[str, str] = {}

    model_config = {"extra": "ignore"}

    def redacted_dump(self) -> dict[str, Any]:
        """Curated feeds carry no secrets at the config level (source
        specs are public identities on their own rows). The contract is
        here so a future secret-bearing kind can't ship without
        implementing it."""
        return self.model_dump(mode="json")

    def summary(self) -> FeedConfigSummary:
        """Empty stub. The server's feed serializer enriches with the
        joined source count + display labels (this method is pure /
        no DB access)."""
        return FeedConfigSummary()

    def merge_preserving(self, prior: "FeedConfig") -> "CuratedFeedConfig":
        """Submitted retention_days + default_field_map win on edit; no
        per-source state lives on this config (it's on Source rows, which
        are mutated through the dedicated sub-resource)."""
        return self


# ── Source envelopes (referenced by FeedView / FeedMutationResponse) ──────


class SourceInput(BaseModel):
    """One source on a feed-create or set-sources payload.

    `meta` is operator-supplied free-form tags; the recorder copies it
    onto each FeedItem the source produces. `field_map` overrides the
    feed-level `default_field_map` for a single source ; empty means
    inherit. Connectors that don't read `field_map` ignore it.

    `last_event_at` is the optional starting watermark. None means
    "live mode from now" (server-policy defaulted at save time); a
    past datetime means "fetch items newer than this" ; the operator's
    backfill knob. Server policy rejects future values."""

    spec: SourceSpec
    meta: dict[str, str] = {}
    field_map: dict[str, str] = {}
    last_event_at: datetime | None = None


class SourceWire(BaseModel):
    """One Source row on the read path."""

    id: str
    spec: SourceSpec
    meta: dict[str, str] = {}
    field_map: dict[str, str] = {}
    last_event_at: Any = None
    created_at: Any = None


# ── Wire (read-path response envelope) ────────────────────────────────────


# ── Item payload (the typed `FeedItem.data` dump) ─────────────────────────
#
# `FeedItem.data` is one connector SourcePayload's `model_dump()`. The server's
# SourcePayload hierarchy (apps/core/sources) carries connector machinery
# (sample(), parsing, registry) that doesn't cross the wire ; these mirror only
# the DATA fields a reader consumes, so the canonical engine inputs (title /
# content / url ...) are typed and connector-specific fields (subreddit, ...)
# are typed per kind. `kind` is the PAYLOAD_KIND (e.g. "new_post"), NOT the
# connector kind on FeedItemWire.source_kind (e.g. "reddit_subreddit").


class FeedItemPayload(BaseModel):
    """Base payload: the canonical engine-input fields every connector maps
    onto, plus a catch-all for keys this build doesn't model. `extra="allow"`
    keeps unmodeled connector fields readable, and this is also the FALLBACK
    union member: a payload whose `kind` matches no known variant (a newer
    connector, or older data) validates here instead of breaking the read."""

    model_config = {"extra": "allow"}

    kind: str = ""
    external_id: str = ""
    source: str = ""
    occurred_at: Any = None  # datetime | None; renderer ISO-encodes
    title: str = ""
    content: str = ""
    url: str = ""
    parent_external_id: str = ""


class RssEntryPayload(FeedItemPayload):
    """`rss_entry`: one RSS/Atom entry (RssEntryConnector)."""

    kind: Literal["rss_entry"]  # required, so a non-rss dump can't match here
    author: str = ""
    feed_url: str = ""
    categories: list[str] = []


class NewRedditPostPayload(FeedItemPayload):
    """`new_post`: one post off a subreddit's /new (RedditSubredditConnector)."""

    kind: Literal["new_post"]  # required, so a non-reddit dump can't match here
    author: str = ""
    permalink: str = ""
    subreddit: str = ""


# Tried left-to-right so a dump resolves to its concrete variant (matched on the
# required `kind` literal) and only falls to the permissive base when no variant
# claims it. Variants REQUIRE their `kind`, so an empty / kind-less dict can't
# greedily match the first variant and lands on the base instead. CAVEAT: a dump
# whose `kind` matches a variant but whose other fields fail that variant's
# validation (e.g. `categories: "oops"` on rss_entry) ALSO degrades to the base,
# raw fields kept in model_extra, not an error - robust for a read/display wire,
# but a consumer keying on `isinstance(data, RssEntryPayload)` won't see the
# malformed row (canonical fields like `title` still read off the base).
FeedItemData = Annotated[
    RssEntryPayload | NewRedditPostPayload | FeedItemPayload,
    Field(union_mode="left_to_right"),
]


class FeedItemWire(BaseModel):
    """One persisted FeedItem on the wire ; the "sort by new and go" unit.

    `data` is the connector SourcePayload's dump, parsed into a typed
    `FeedItemData` (canonical fields typed for every kind; connector-specific
    fields typed per known kind; unknown kinds fall to the permissive base).
    Datetimes stay real; the renderer ISO-encodes them.

    `source_kind` is the connector kind (e.g. `"reddit_subreddit"`),
    denormalized from the producing Source. `source_label` is the
    operator-visible display string (e.g. `"r/ClaudeCowork"`), set on
    the FeedItem row at record time from the SourceSpec's `.display()`."""

    id: str
    source_kind: str
    source_label: str = ""
    external_id: str
    occurred_at: Any = None  # datetime | None; renderer encodes
    data: FeedItemData = Field(default_factory=FeedItemPayload)


class FeedWire(BaseModel):
    """The kind-independent envelope every `/v1/feeds` response item
    carries. List-item shape and base for detail / mutation responses."""

    id: str
    name: str
    kind: str
    is_active: bool
    poll_interval_seconds: int
    last_polled_at: Any = None  # datetime | None
    next_poll_at: Any = None
    # creator, audit/display only (account-scoped reads, not an ownership filter)
    user_id: str
    data: ConfigBlob = {}
    created_at: Any = None


class FeedListResponse(BaseModel):
    """`GET /v1/feeds` -> `{"items": [...], "next_cursor": <id>|None}`.

    Cursor-paginated by ULID pk, newest-first. Pass `?after=<id>` to fetch
    the next page; `next_cursor` is the id to send back, or null when the
    page wasn't full (= no more rows)."""

    items: list[FeedWire] = []
    next_cursor: str | None = None


class FeedItemListResponse(BaseModel):
    """`GET /v1/feeds/<id>/items` -> `{"items": [...], "next_cursor": <id>|None}`.

    Cursor-paginated by ULID pk, newest-first. Pass `?after=<id>` to fetch the
    next page; `next_cursor` is the id to send back, or null when the page wasn't
    full (= no more rows)."""

    items: list[FeedItemWire] = []
    next_cursor: str | None = None


class FeedView(FeedWire):
    """`GET /v1/feeds/<id>` - read view: envelope + display `summary` +
    the recent item log (this is the "sort by new and go" surface; the
    detail endpoint IS the reader, no separate route needed)."""

    summary: FeedConfigSummary = FeedConfigSummary()
    recent_items: list[FeedItemWire] = []
    # The feed's currently-attached Source rows. Populated on GET-detail
    # so a single call shows everything the operator wants to see; list
    # pages keep their bare wire to avoid per-row joins.
    sources: list[SourceWire] = []
    source_count: int = 0


class FeedMutationResponse(FeedWire):
    """Create / edit response (POST + PUT, real and `?dry_run=true`).
    `id` absent on a create dry-run preview; `dry_run` True for preview.
    Carries the same `sources` + `source_count` enrichment as FeedView
    so the CLI's confirm-preview shows the operator the post-mutation
    source list without an extra GET."""

    id: str | None = None
    summary: FeedConfigSummary = FeedConfigSummary()
    sources: list[SourceWire] = []
    source_count: int = 0
    dry_run: bool


class SourceSetResult(BaseModel):
    """POST /v1/feeds/{id}/sources (PUT collection) ; replace-mode reconcile result.

    `added` / `removed` / `persisted` describe the diff this call
    applied; `source_count` is the total after the diff. A no-op set
    (nothing changed) reports `added=0, removed=0, persisted=total`."""

    added: int
    removed: int
    persisted: int
    source_count: int


class SourceSetPayload(BaseModel):
    """Round-trip file format for `magpie feed export-sources` /
    `magpie feed set-sources`. Operators or their scrape scripts can
    construct this directly; the bare `list[SourceInput]` shape is
    also accepted on input for hand-rolled cases."""

    version: Literal["v1"] = "v1"
    sources: list[SourceInput] = []
