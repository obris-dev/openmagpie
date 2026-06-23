"""Pure typed source specs, keyed by kind.

SHARED, zero-Django source of truth (imported by core *and* the magpie
CLI). Carries only *shape* + pure transforms. The Django/settings-coupled
*policy* (SSRF / https rules, default engine kind, ...) is NOT here ; it
lives in `core` and runs at the server's validation seam. Splitting shape
from policy is what lets this module be a dependency-free shared package.
"""

import re
from typing import Annotated, ClassVar, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, field_validator

# ── Source specs (discriminated union over kind) ──────────────────────────

# Reddit's max subreddit-name length; the slug validator bounds names to it.
MAX_SUBREDDIT_LENGTH = 21


class RedditSubredditSourceSpec(BaseModel):
    """Identity of one subreddit source. Bound to RedditSubRedditConnector."""

    SOURCE_KIND: ClassVar[str] = "reddit_subreddit"

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


SourceSpec = Annotated[
    RedditSubredditSourceSpec | RssSourceSpec | HackerNewsFeedSourceSpec | HackerNewsCommentSourceSpec,
    Field(discriminator="kind"),
]
