"""TikTok payloads: a video observed via a SerpBase keyword search.

The engine judges title + content, so the caption goes to both (TikTok has no
separate title); the author's handle is the within-kind source slug, so items
group by creator. Counters are TikTok's string-encoded metrics, parsed to ints
and kept as source-specific fields; hashtags are joined into one text field so
each can be a column when the item is sent on.

The API's `url` / `link` / author fields are returned unvalidated, so every URL
written onto a payload is checked against a TikTok host allowlist first: an item
that carries a link pointing anywhere else is dropped to a payload with no
`url`, never rendered as a trusted link downstream.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any, ClassVar
from urllib.parse import urlsplit

from openmagpie_schema.configs import TikTokSearchSourceSpec
from sources.payloads import SourcePayload

TIKTOK_HOSTS = frozenset({"tiktok.com", "www.tiktok.com", "m.tiktok.com"})
TIKTOK_VIDEO_URL = "https://www.tiktok.com/"

# The API renders counters as strings ("8878"); anything else is 0 rather than an
# error, because a missing counter must not fail an otherwise usable item.
_COUNTER_FIELDS = ("likes_count", "comments_count", "shares_count", "views_count", "saves_count")


class NewTikTokVideoPayload(SourcePayload):
    """A single TikTok video found by a watched keyword search.

    `author` is the creator's display name and `handle` (their unique id) is the
    within-kind source slug. `source_query` is the keyword the producing source
    searched for ("" only if a spec were ever built without one), so items from
    two watched searches over the same video do not collapse into one slug.
    """

    PAYLOAD_KIND: ClassVar[str] = "new_tiktok_video"

    author: str = ""
    handle: str = ""
    hashtags: str = ""
    source_query: str = ""
    metrics: dict[str, int] = {}

    model_config = {"frozen": True, "extra": "ignore"}

    def source_slug(self) -> str | None:
        return self.handle or None

    @classmethod
    def sample(cls, variant: int = 0) -> NewTikTokVideoPayload:
        n = variant + 1
        video_id = str(760_000_000_000_000_000 + n)
        handle = f"example_creator_{n}"
        return cls(
            external_id=video_id,
            kind=cls.PAYLOAD_KIND,
            occurred_at=datetime(2026, 5, 27, 12, 0, tzinfo=UTC),
            source=TikTokSearchSourceSpec.SOURCE_KIND,
            title=f"Example TikTok caption {n} that matched this watch #example",
            content=f"Example TikTok caption {n} that matched this watch #example",
            url=f"https://www.tiktok.com/@{handle}/video/{video_id}",
            author=f"Example Creator {n}",
            handle=handle,
            hashtags="example, exampletwo",
            source_query="example query",
            metrics={
                "likes_count": 1200,
                "comments_count": 34,
                "shares_count": 12,
                "views_count": 45000,
                "saves_count": 90,
            },
        )

    @classmethod
    def from_post(
        cls, post: dict[str, Any], spec: TikTokSearchSourceSpec, *, occurred_at: datetime
    ) -> NewTikTokVideoPayload:
        """Map one `/tiktok/search` post to a payload."""
        raw_author = post.get("author")
        author: dict[str, Any] = raw_author if isinstance(raw_author, dict) else {}
        text = str(post.get("text") or "")
        handle = str(author.get("username") or author.get("name") or "")
        url = _tiktok_url(post.get("url")) or _tiktok_url(post.get("link"))
        return cls(
            external_id=str(post.get("id") or ""),
            kind=cls.PAYLOAD_KIND,
            occurred_at=occurred_at,
            source=TikTokSearchSourceSpec.SOURCE_KIND,
            title=_title(text),
            content=text,
            url=url,
            author=str(author.get("name") or handle),
            handle=handle,
            hashtags=", ".join(_hashtags(post.get("hashtags"))),
            source_query=spec.query,
            metrics={field: _counter(post.get(field)) for field in _COUNTER_FIELDS},
        )


def parse_timestamp(raw: object) -> datetime | None:
    """An ISO-8601 API timestamp as an aware datetime, or None.

    TikTok's `published_at` is `2026-04-01T23:30:56Z`; a naive value is read as
    UTC rather than as the server's local zone.
    """
    if not isinstance(raw, str) or not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _title(text: str, *, limit: int = 120) -> str:
    """The caption's first line, cut to a title-sized preview."""
    first = next((line.strip() for line in text.splitlines() if line.strip()), "")
    return first if len(first) <= limit else f"{first[: limit - 1].rstrip()}…"


def _tiktok_url(raw: object) -> str:
    """`raw` when it is an https URL on a TikTok host, else ""."""
    if not isinstance(raw, str) or not raw:
        return ""
    parts = urlsplit(raw)
    if parts.scheme != "https" or parts.hostname not in TIKTOK_HOSTS:
        return ""
    return raw.split("?", 1)[0]


def _hashtags(raw: object) -> list[str]:
    """The tag names of a `hashtags` list; anything malformed is skipped."""
    if not isinstance(raw, list):
        return []
    names: list[str] = []
    for tag in raw:
        if isinstance(tag, dict):
            name = dict(tag).get("name")
            if name:
                names.append(str(name))
        elif tag:
            names.append(str(tag))
    return names


def _counter(raw: object) -> int:
    """A TikTok counter ("8878", 8878) as int; 0 for anything unparseable."""
    if isinstance(raw, bool):
        return 0
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str) and re.fullmatch(r"\d+", raw.strip()):
        return int(raw.strip())
    return 0
