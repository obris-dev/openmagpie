"""Source specs for platform sources: X, YouTube, Product Hunt, TikTok.

Split from `configs` (which owns the built-in union and the source envelopes) so
each module stays under the line cap. Import these from `configs`, which
re-exports them as members of the source union.
"""

import re
from typing import ClassVar, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, field_validator


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


class TikTokSearchSourceSpec(BaseModel):
    """Identity of one TikTok search stream. Bound to TikTokSearchConnector.

    `query` is the keyword expression and is REQUIRED and NON-BLANK, so a source
    always carries a server-side pre-filter before any per-item LLM cost (the
    same firehose guard as twitter_search / hn_comment). `count` caps the
    per-cycle fetch (bounded at 30, the connector's per-call page ceiling).
    """

    SOURCE_KIND: ClassVar[str] = "tiktok_search"
    URL_FIELDS: ClassVar[tuple[str, ...]] = ()  # no operator-supplied URL to SSRF-check

    kind: Literal["tiktok_search"] = "tiktok_search"
    query: str = Field(min_length=1)
    count: int = Field(default=16, ge=1, le=30)

    @field_validator("query")
    @classmethod
    def _query_not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("tiktok_search requires a non-blank query (the firehose guard)")
        return v

    def display(self) -> str:
        return f'TikTok search: "{self.query}"'


class YouTubeSearchSourceSpec(BaseModel):
    """Identity of one YouTube search stream. Bound to YouTubeSearchConnector.

    `query` is REQUIRED and NON-BLANK so a source always carries a server-side
    pre-filter before any per-item LLM cost. `count` caps the per-cycle fetch
    (bounded at 50, the client's search cap)."""

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


# A Product Hunt topic slug as it appears in the topic's URL (`developer-tools`).
_TOPIC_SLUG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
_PRODUCT_HUNT_HOSTS = frozenset({"producthunt.com", "www.producthunt.com"})


def _slug_from_topic_url(raw: str) -> str:
    """The slug from a pasted topic URL, with or without its scheme or www. Anything
    else with a `/` is refused, never read as an empty (every-topic) slug."""
    url = urlsplit(raw if "://" in raw else f"https://{raw}")
    parts = [part for part in url.path.split("/") if part]
    if url.hostname not in _PRODUCT_HUNT_HOSTS or len(parts) != 2 or parts[0] != "topics":
        raise ValueError(f"{raw!r} is not a Product Hunt topic URL; expected https://www.producthunt.com/topics/<slug>")
    return parts[1].lower()


class ProductHuntLaunchesSourceSpec(BaseModel):
    """Identity of one Product Hunt launch stream. Bound to ProductHuntLaunchesConnector.

    Front-page (featured) launches, read from the official GraphQL API with the
    server's developer token (PRODUCT_HUNT_TOKEN). `topic` narrows to one topic by
    its slug (`developer-tools`) or its pasted topic URL; empty = every topic."""

    SOURCE_KIND: ClassVar[str] = "producthunt_launches"
    URL_FIELDS: ClassVar[tuple[str, ...]] = ()  # no operator-supplied URL to SSRF-check

    kind: Literal["producthunt_launches"] = "producthunt_launches"
    topic: str = ""

    @field_validator("topic")
    @classmethod
    def _topic_slug(cls, v: str) -> str:
        raw = v.strip()
        if not raw:
            return ""
        slug = _slug_from_topic_url(raw) if "/" in raw else raw.lower()
        if not _TOPIC_SLUG.fullmatch(slug):
            raise ValueError(f"invalid Product Hunt topic {v!r}: use the slug from its URL, e.g. developer-tools")
        return slug

    def display(self) -> str:
        return "Product Hunt launches" + (f": {self.topic}" if self.topic else "")
