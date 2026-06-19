"""Pure typed source specs, keyed by kind.

SHARED, zero-Django source of truth (imported by core *and* the magpie
CLI). Carries only *shape* + pure transforms. The Django/settings-coupled
*policy* (SSRF / https rules, default engine kind, ...) is NOT here ; it
lives in `core` and runs at the server's validation seam. Splitting shape
from policy is what lets this module be a dependency-free shared package.
"""

from typing import Annotated, ClassVar, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, field_validator

# ── Source specs (discriminated union over kind) ──────────────────────────


class RedditSubredditSourceSpec(BaseModel):
    """Identity of one subreddit source. Bound to RedditSubRedditConnector."""

    SOURCE_KIND: ClassVar[str] = "reddit_subreddit"

    kind: Literal["reddit_subreddit"] = "reddit_subreddit"
    subreddit: str

    def display(self) -> str:
        return f"r/{self.subreddit}"


class RssSourceSpec(BaseModel):
    """Identity of one RSS/Atom source by URL. Bound to a generic RSS connector."""

    SOURCE_KIND: ClassVar[str] = "rss"

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
    average, bursty), so `query` is REQUIRED here: it overrides the base default
    away. That is the structural guard that keeps an unfiltered firehose from
    ever reaching the per-item relevance engine. Volume past the keyword filter
    is further bounded by the connector's page cap."""

    SOURCE_KIND: ClassVar[str] = "hn_comment"

    kind: Literal["hn_comment"] = "hn_comment"
    query: str  # required: no default (see docstring)

    def display(self) -> str:
        return f'HN comments: "{self.query}"'


SourceSpec = Annotated[
    RedditSubredditSourceSpec | RssSourceSpec | HackerNewsFeedSourceSpec | HackerNewsCommentSourceSpec,
    Field(discriminator="kind"),
]
