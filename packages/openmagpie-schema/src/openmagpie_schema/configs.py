"""Pure typed source specs, keyed by kind.

SHARED, zero-Django source of truth (imported by core *and* the magpie
CLI). Carries only *shape* + pure transforms. The Django/settings-coupled
*policy* (SSRF / https rules, default engine kind, ...) is NOT here ; it
lives in `core` and runs at the server's validation seam. Splitting shape
from policy is what lets this module be a dependency-free shared package.
"""

import json
import re
from typing import Annotated, ClassVar, Literal, NamedTuple, get_args
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, field_validator

from ._unions import _PLUGIN_MEMBER_LOC_NAMES, KIND_MAX_LENGTH, builtin_union_kinds, reject_builtin_kind

# ── Source specs (discriminated union over kind) ──────────────────────────

# Reddit's max subreddit-name length; the slug validator bounds names to it.
MAX_SUBREDDIT_LENGTH = 21


class RedditSubredditSourceSpec(BaseModel):
    """Identity of one subreddit source. Bound to RedditSubRedditConnector."""

    SOURCE_KIND: ClassVar[str] = "reddit_subreddit"
    URL_FIELDS: ClassVar[tuple[str, ...]] = ()  # no operator-supplied URL to SSRF-check

    kind: Literal["reddit_subreddit"] = "reddit_subreddit"
    subreddit: str

    @field_validator("subreddit")
    @classmethod
    def _validate_subreddit(cls, value: str) -> str:
        """Validate + normalize the BARE subreddit name. A pasted `r/` or `/r/`
        prefix is stripped (callers build the `r/<name>/...` request URL and
        `display()` re-adds the prefix), the name is held to a URL-safe charset
        (letters/digits/underscores, <=MAX_SUBREDDIT_LENGTH chars - nothing like
        `/`, `?`, `#`, `+`, or whitespace that would break a request URL), and the
        result is lowercased (subreddit names are case-insensitive, so that's the
        one canonical identity).

        Deliberately a URL-safe subset, not Reddit's exact naming rule: it's looser
        (allows 1-2 char names) and doesn't special-case `u_` user feeds. Unlike
        RssSourceSpec.url's validate-only check, this also normalizes."""
        slug = re.sub(r"^/?r/", "", value.strip(), flags=re.IGNORECASE)
        if not re.fullmatch(rf"[A-Za-z0-9_]{{1,{MAX_SUBREDDIT_LENGTH}}}", slug):
            raise ValueError(f"invalid subreddit {value!r}: letters/digits/underscores, <={MAX_SUBREDDIT_LENGTH} chars")
        # Subreddit names are case-insensitive (r/Python and r/python are the same
        # sub), so normalize to the one canonical lowercase form.
        return slug.lower()

    def display(self) -> str:
        return f"r/{self.subreddit}"


class RssSourceSpec(BaseModel):
    """Identity of one RSS/Atom source by URL. Bound to a generic RSS connector."""

    SOURCE_KIND: ClassVar[str] = "rss"
    # Fields the connector actually FETCHES, so the write-time SSRF gate checks only
    # these (not display-only fields like `name`, which the connector never dereferences
    # and which shouldn't 400 for containing a private-IP-looking string).
    URL_FIELDS: ClassVar[tuple[str, ...]] = ("url",)

    kind: Literal["rss"] = "rss"
    url: str
    name: str = ""

    @field_validator("url")
    @classmethod
    def _validate_url_structural(cls, value: str) -> str:
        """Structural check only (http/https scheme + host present).
        Connector-side reachability / feed-format validation runs at poll
        time. An empty URL slips through plain `str` typing and silently
        produces a blank source_label downstream; reject it here."""
        parts = urlsplit(value)
        if parts.scheme not in {"http", "https"}:
            raise ValueError(f"rss URL scheme must be http or https, got {parts.scheme!r}")
        if not parts.netloc:
            raise ValueError(f"rss URL missing host: {value!r}")
        return value

    def display(self) -> str:
        return self.name or self.url


class _HackerNewsSpec(BaseModel):
    """Shared fields for the Algolia-backed Hacker News specs.

    `query` is the server-side keyword pre-filter (Algolia full-text search);
    `match` picks AND (default, every word must appear) vs ANY (OR, via
    Algolia `optionalWords`). Empty `query` means no pre-filter (fine for the
    low-volume story feeds; the comment spec makes it required)."""

    URL_FIELDS: ClassVar[tuple[str, ...]] = ()  # no operator-supplied URL to SSRF-check

    query: str = ""
    match: Literal["all", "any"] = "all"


class HackerNewsFeedSourceSpec(_HackerNewsSpec):
    """Identity of one Hacker News story feed. Bound to HackerNewsFeedConnector.

    `feed` selects which posts the connector pulls; it maps to an Algolia
    HN Search `tags` value (new -> story, show -> show_hn, ask -> ask_hn).
    The set is closed to the feeds that map to a single Algolia tag and a
    newest-first date order; the ranked Firebase feeds (top / best) have no
    Algolia equivalent and are intentionally out of scope here."""

    SOURCE_KIND: ClassVar[str] = "hn_feed"

    kind: Literal["hn_feed"] = "hn_feed"
    feed: Literal["new", "show", "ask"] = "new"

    def display(self) -> str:
        return {"new": "Hacker News (new)", "show": "Show HN", "ask": "Ask HN"}.get(self.feed, self.feed)


class HackerNewsCommentSourceSpec(_HackerNewsSpec):
    """Identity of one Hacker News COMMENT stream. Bound to HackerNewsCommentConnector.

    `tags=comment` unfiltered is the site-wide comment firehose (~20k/day on
    average, bursty), so `query` is REQUIRED and NON-BLANK here (it overrides the
    base default away). That is the structural guard that keeps an unfiltered
    firehose from ever reaching the per-item relevance engine -- a blank or
    whitespace-only query would strip to no pre-filter, so it is rejected at the
    spec layer, not merely required-to-be-present. Volume past the keyword filter
    is further bounded by the connector's page cap."""

    SOURCE_KIND: ClassVar[str] = "hn_comment"

    kind: Literal["hn_comment"] = "hn_comment"
    query: str = Field(min_length=1)  # required + non-blank: the firehose guard (see _query_not_blank)

    @field_validator("query")
    @classmethod
    def _query_not_blank(cls, v: str) -> str:
        # min_length=1 rejects "" ; this also rejects a whitespace-only query and
        # stores it stripped. A blank query = no pre-filter = the whole firehose.
        v = v.strip()
        if not v:
            raise ValueError("hn_comment requires a non-blank query (the firehose guard)")
        return v

    def display(self) -> str:
        return f'HN comments: "{self.query}"'


class TwitterSearchSourceSpec(BaseModel):
    """Identity of one X (Twitter) search stream. Bound to TwitterSearchConnector.

    `query` is the search expression (keywords, quoted phrases, `from:`,
    `lang:`, `filter:` operators, whatever X's search syntax accepts); it is
    REQUIRED and NON-BLANK so a source always carries a server-side pre-filter
    before any per-item LLM cost (same discipline as hn_comment: a blank query
    would be the unfiltered firehose). `mode` picks the result ordering twikit
    asks X for: `latest` (newest first, the listener's default) or `top`
    (ranked). `count` caps the per-cycle fetch. `lang` optionally narrows to
    tweets in one language (ISO 639-1, e.g. "en"); empty = no filter.
    """

    SOURCE_KIND: ClassVar[str] = "twitter_search"
    URL_FIELDS: ClassVar[tuple[str, ...]] = ()  # no operator-supplied URL to SSRF-check

    kind: Literal["twitter_search"] = "twitter_search"
    query: str = Field(min_length=1)
    mode: Literal["latest", "top"] = "latest"
    count: int = Field(default=20, ge=1, le=100)
    lang: str = ""

    @field_validator("query")
    @classmethod
    def _query_not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("twitter_search requires a non-blank query (the firehose guard)")
        return v

    @field_validator("lang")
    @classmethod
    def _lang_normalize(cls, v: str) -> str:
        return v.strip().lower()

    def display(self) -> str:
        return f'X search: "{self.query}"'


class YouTubeSearchSourceSpec(BaseModel):
    """Identity of one YouTube search stream. Bound to YouTubeSearchConnector.

    `query` is the search expression (keywords, phrases, operators like
    `from:`, `channel:`); it is REQUIRED and NON-BLANK so a source always
    carries a server-side pre-filter before any per-item LLM cost.
    `count` caps the per-cycle fetch (capped at 50 by yt-dlp for search).
    """

    SOURCE_KIND: ClassVar[str] = "youtube_search"
    URL_FIELDS: ClassVar[tuple[str, ...]] = ()  # no operator-supplied URL to SSRF-check

    kind: Literal["youtube_search"] = "youtube_search"
    query: str = Field(min_length=1)
    count: int = Field(default=20, ge=1, le=50)

    @field_validator("query")
    @classmethod
    def _query_not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("youtube_search requires a non-blank query (the firehose guard)")
        return v

    def display(self) -> str:
        return f'YouTube search: "{self.query}"'


# The built-ins as a discriminated union over `kind` (defined before the plugin
# fallback so the built-in kind set can be derived from it below). A built-in kind
# with a malformed spec fails its typed member here and is rejected by the fallback,
# so it surfaces as a validation error rather than being absorbed as a raw blob.
_BuiltinSourceSpec = Annotated[
    RedditSubredditSourceSpec
    | RssSourceSpec
    | HackerNewsFeedSourceSpec
    | HackerNewsCommentSourceSpec
    | TwitterSearchSourceSpec
    | YouTubeSearchSourceSpec,
    Field(discriminator="kind"),
]

# Built-in source kinds, DERIVED from the union members above rather than kept as a
# second hand-maintained list, so the set the plugin fallback rejects can never drift
# from the union (adding a member to _BuiltinSourceSpec extends this for free). Taken
# from each member's `SOURCE_KIND` ClassVar (a plain str, always present) rather than
# the `kind` field default, which is fragile: a multi-value Literal would capture only
# the default, and a member without a default would inject PydanticUndefined.
_BUILTIN_SOURCE_SPECS = get_args(get_args(_BuiltinSourceSpec)[0])
# Reject-set: the kind values the union DISPATCHES on, via the SAME shared helper the
# action/run unions use (it reads every Literal arg, so a multi-value Literal is
# handled). Sharing the helper is why the SOURCE_KIND cross-pin below is the ONLY
# source-specific piece.
_BUILTIN_SOURCE_KINDS = builtin_union_kinds(_BuiltinSourceSpec)

# Import-time guards, load-bearing so raised explicitly (a bare `assert` is stripped
# under `python -O`, and this is a shared library):
# (1) If the union is ever refactored down to a single member, `get_args` returns ()
#     and the reject-set goes empty, silently letting the fallback absorb EVERY
#     malformed built-in spec (the exact hole this set closes). Require >=2, one per
#     member.
# (2) Source-specific cross-pin: each member also carries a SOURCE_KIND ClassVar (the
#     connector registry's key), declared independently of the `kind` Literal. Pin the
#     Literal to exactly (SOURCE_KIND,) so the discriminator, the reject-set, and the
#     connector key can't diverge (a divergence would let a malformed built-in slip
#     past the reject-set). getattr default keeps a missing SOURCE_KIND a curated
#     RuntimeError, not a bare AttributeError.
if not (len(_BUILTIN_SOURCE_KINDS) == len(_BUILTIN_SOURCE_SPECS) >= 2):
    raise RuntimeError(f"expected >=2 built-in source kinds, one per union member; got {sorted(_BUILTIN_SOURCE_KINDS)}")
for _spec in _BUILTIN_SOURCE_SPECS:
    if get_args(_spec.model_fields["kind"].annotation) != (getattr(_spec, "SOURCE_KIND", None),):
        raise RuntimeError(
            f"{_spec.__name__}: kind Literal {get_args(_spec.model_fields['kind'].annotation)} must be exactly "
            f"(SOURCE_KIND,) = ({getattr(_spec, 'SOURCE_KIND', None)!r},); the discriminator, reject-set, and "
            f"connector key would otherwise diverge"
        )


class PluginSourceSpec(BaseModel):
    """Fallback spec member for a plugin (non-built-in) source kind. `kind` is any
    non-built-in string; the rest of the spec is an open blob (a fork's typed spec
    schema lives in its own contract, and its web/CLI narrow on `kind`). Selected
    only when no built-in discriminator matches (the left-to-right union below).
    `extra="allow"` keeps every submitted field through `model_dump(mode="json")`,
    so `canonical_spec` / `source_identity` (the spec_hash basis) stay stable."""

    model_config = {"extra": "allow"}

    kind: str = Field(min_length=1, max_length=KIND_MAX_LENGTH)

    @field_validator("kind")
    @classmethod
    def _not_builtin(cls, v: str) -> str:
        return reject_builtin_kind(v, _BUILTIN_SOURCE_KINDS)

    def display(self) -> str:
        # No typed shape, so fall back to an operator label if the blob carries one,
        # else the kind. A fork's typed spec member supplies a real display().
        extra = self.model_extra or {}
        label = extra.get("name") or extra.get("label")
        return str(label) if label else self.kind


# One source spec, keyed by `kind`: the built-in discriminated union above, then a
# left-to-right fallthrough to the plugin member for any other kind (same discipline
# as WatchActionWire).
SourceSpec = Annotated[_BuiltinSourceSpec | PluginSourceSpec, Field(union_mode="left_to_right")]

# Pin the fallback member name against `_unions._PLUGIN_MEMBER_LOC_NAMES` (see _nodes):
# a rename that isn't mirrored there silently turns off clean_union_errors' stripping.
if PluginSourceSpec.__name__ not in _PLUGIN_MEMBER_LOC_NAMES:
    raise RuntimeError(f"{PluginSourceSpec.__name__} missing from _unions._PLUGIN_MEMBER_LOC_NAMES")


def canonical_spec(spec: SourceSpec) -> str:
    """Canonical JSON for a source spec: the single identity both the server's
    `spec_hash` (which sha256s this) and the magpie CLI's source-diff compare on.
    Sorted keys + compact separators make it independent of field-declaration
    order, so two specs denote the same source iff this string matches. Pure
    shape; keep it byte-stable - a change reshuffles every stored `spec_hash`
    (pinned by core's `SpecHashCanonicalTests`)."""
    return json.dumps(spec.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))


class SourceFields(BaseModel):
    """The identity + operator-tunable config shared by both source envelopes -
    `SourceInput` (write path) and `SourceWire` (read path).

    `meta` is operator-supplied free-form tags; the recorder copies it onto each
    FeedItem the source produces. `field_map` overrides the feed-level
    `default_field_map` for a single source; empty means inherit (connectors that
    don't read it ignore it). The watermark (`last_event_at`) and server-assigned
    fields (`id`, `created_at`) live on the envelopes, since they differ by
    direction."""

    spec: SourceSpec
    meta: dict[str, str] = Field(default_factory=dict)
    field_map: dict[str, str] = Field(default_factory=dict)


class SourceIdentity(NamedTuple):
    """A source's full reconcile identity - everything `feed source set` keys on:
    the spec (`canonical_spec`, the `spec_hash` basis) PLUS the mutable config it
    refreshes, meta + field_map. Excludes last_event_at (watermarks are never
    reconciled). Hashable + ordered, so callers diff source sets by plain
    equality instead of ad-hoc JSON."""

    spec: str
    meta: tuple[tuple[str, str], ...]
    field_map: tuple[tuple[str, str], ...]


def source_identity(source: SourceFields) -> SourceIdentity:
    """Build the shared `SourceIdentity` from any source envelope (SourceInput or
    SourceWire, via their `SourceFields` base) - the ONE definition the magpie
    CLI's source-diff compares on and that the server's set_sources reconcile
    mirrors (spec_hash + meta/field_map)."""
    return SourceIdentity(
        spec=canonical_spec(source.spec),
        meta=tuple(sorted(source.meta.items())),
        field_map=tuple(sorted(source.field_map.items())),
    )
