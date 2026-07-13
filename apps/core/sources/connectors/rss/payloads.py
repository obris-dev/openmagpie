"""SourcePayload produced by the generic RSS/Atom connector.

`title` / `url` / `author` / `external_id` / `published` map directly to
feedparser's normalized entry keys (feedparser already collapses RSS
`<dc:creator>` -> `entry.author`, `<guid>` -> `entry.id`, `<description>`
-> `entry.summary`, etc.). Two narrow fallbacks remain because they
encode a real Atom-vs-RSS semantic difference, not a publisher quirk:

  * `content` falls back from `entry.content` (Atom full body, list of
    representations) to `entry.summary` (RSS `<description>`, Atom
    short summary). "Longest available body for the engine."
  * `published` falls back from `entry.published_parsed` to
    `entry.updated_parsed`. Atom feeds (GitHub releases, Substack
    edits) often ship only `<updated>`; skipping those would lose
    real entries.

For everything else, the canonical feedparser key is the contract;
publishers whose data lives elsewhere set a `field_map` override per
canonical name (e.g. `{"external_id": "media:content"}` for a podcast
feed). Unknown override values are simply read from the entry as-is
(feedparser exposes most namespaced keys with a `_` separator). If
the resolved key is missing on a `external_id` / `published` field,
the connector skips the entry and logs WHICH field was unresolved so
the operator can spot the missing override."""

import html
import re
import time
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any, ClassVar

from openmagpie_schema.configs import RssSourceSpec
from sources.payloads import SourcePayload

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _html_to_text(value: str) -> str:
    """Strip HTML tags + collapse whitespace + unescape entities. RSS
    publishers vary widely on whether the body is plain text, escaped
    HTML, or CDATA-wrapped HTML; normalize so the engine never sees raw
    markup."""
    return _WS_RE.sub(" ", html.unescape(_TAG_RE.sub(" ", value))).strip()


def _resolve_str(entry: Any, key: str) -> str:
    """Read a string-shaped field from the feedparser entry. Coerces
    feedparser's odd "missing returns empty string" + occasional None
    + numeric publisher values into a stable str."""
    value = entry.get(key)
    return str(value).strip() if value else ""


def _resolve_content(entry: Any, override: str | None) -> str:
    """Body: Atom `entry.content[0].value` if present, else `entry.summary`.
    An override reads that key directly. Always HTML-stripped."""
    if override:
        raw: Any = entry.get(override, "")
    else:
        content = entry.get("content")
        if content:
            first = content[0]
            raw = first.get("value", "") if isinstance(first, dict) else first
        else:
            raw = entry.get("summary", "")
    if isinstance(raw, list) and raw:
        raw = raw[0].get("value", "") if isinstance(raw[0], dict) else raw[0]
    return _html_to_text(str(raw or ""))


def _struct_time_to_utc(parsed: time.struct_time) -> datetime:
    """feedparser's `*_parsed` struct_times are ALREADY in UTC. Read the
    year/month/day/hour/minute/second fields straight into the datetime
    constructor ; `time.mktime` would interpret the struct as local
    wall-clock and shift every timestamp by the host's UTC offset on
    any non-UTC deploy."""
    return datetime(
        parsed.tm_year,
        parsed.tm_mon,
        parsed.tm_mday,
        parsed.tm_hour,
        parsed.tm_min,
        parsed.tm_sec,
        tzinfo=UTC,
    )


def _coerce_published(value: Any) -> datetime | None:
    """Read a published-ish value into an aware UTC datetime. Accepts
    feedparser's `*_parsed` struct_time (the common path, no parsing)
    AND raw strings (RFC-822 / ISO 8601) so a `field_map` override can
    point at either feedparser's normalized struct (`published_parsed`,
    `updated_parsed`) OR a string-keyed namespaced field
    (e.g. `dc_date`, `prism_published`). Naive datetimes are tagged
    UTC ; that matches feedparser's struct_time semantics and matches
    how the rest of the connector treats publish times."""
    if isinstance(value, time.struct_time):
        return _struct_time_to_utc(value)
    if isinstance(value, str) and value:
        try:
            dt = parsedate_to_datetime(value)
        except (TypeError, ValueError):
            try:
                dt = datetime.fromisoformat(value)
            except ValueError:
                return None
        return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
    return None


def _resolve_published(entry: Any, override: str | None) -> datetime | None:
    """Pick the published timestamp. An `override` is tried first (high
    priority), then the canonical `published_parsed -> updated_parsed`
    fallback chain so an override that doesn't resolve doesn't disable
    the entries that DO have a real `published`. The chain dedupes the
    override key so we don't read the same entry twice."""
    chain: list[str] = []
    if override:
        chain.append(override)
    chain.extend(k for k in ("published_parsed", "updated_parsed") if k != override)
    for key in chain:
        dt = _coerce_published(entry.get(key))
        if dt is not None:
            return dt
    return None


class RssEntryPayload(SourcePayload):
    """One entry from an RSS / Atom feed."""

    PAYLOAD_KIND: ClassVar[str] = "rss_entry"

    author: str = ""
    feed_url: str = ""
    categories: list[str] = []

    model_config = {"frozen": True, "extra": "ignore"}

    @property
    def article_url(self) -> str:
        # An RSS entry's own `url` IS the article (no separate discussion page), so the
        # linked-article enrichment fetches it. Overrides the base (which uses
        # external_url); derived from the stored `url`, so it applies to existing items.
        return self.url

    def source_slug(self) -> str:
        return self.feed_url

    @classmethod
    def sample(cls, variant: int = 0) -> "RssEntryPayload":
        n = variant + 1
        return cls(
            external_id=f"https://example.com/news/article-{n}",
            kind=cls.PAYLOAD_KIND,
            occurred_at=datetime(2026, 5, 27, 12, 0, tzinfo=UTC),
            source=RssSourceSpec.SOURCE_KIND,
            title=f"Example RSS headline {n}",
            content="The first paragraph of the article body, after the connector strips HTML.",
            url=f"https://example.com/news/article-{n}",
            parent_external_id="",
            author="Staff Writer",
            feed_url="https://example.com/rss.xml",
            categories=["news"],
        )

    @classmethod
    def from_feedparser_entry(
        cls,
        entry: Any,
        spec: RssSourceSpec,
        field_map: dict[str, str],
    ) -> "tuple[RssEntryPayload | None, str]":
        """Project one feedparser entry to an `RssEntryPayload`.

        Returns `(payload, "")` on success, or `(None, missing_field)` if
        a required field can't be resolved. The connector logs the missing
        field name so the operator can spot which `field_map` override
        the publisher needs."""
        published = _resolve_published(entry, field_map.get("published"))
        if published is None:
            return None, "published"

        external_id = _resolve_str(entry, field_map.get("external_id") or "id")
        if not external_id:
            return None, "external_id"

        # `tags` is feedparser's normalized form of RSS `<category>` /
        # Atom `<category>` ; each item is a FeedParserDict with `term`.
        # Empty-term entries get filtered out here ; the empties usually
        # come from publishers who emit `<category></category>` as a
        # placeholder.
        categories = [
            term for t in entry.get("tags", []) if isinstance(t, dict) and (term := t.get("term", "").strip())
        ]

        # `url` is the entry link. `external_url` is left unset: an RSS entry has no
        # separate off-platform link (the entry IS the article), and linked-article
        # enrichment selects the fetch target per kind via `RssEntryPayload.article_url`
        # (-> `url`) at evaluation time, so nothing needs to be baked into the payload.
        payload = cls(
            external_id=external_id,
            kind=cls.PAYLOAD_KIND,
            occurred_at=published,
            source=spec.kind,
            title=_resolve_str(entry, field_map.get("title") or "title"),
            content=_resolve_content(entry, field_map.get("content")),
            url=_resolve_str(entry, field_map.get("url") or "link"),
            parent_external_id="",
            author=_resolve_str(entry, field_map.get("author") or "author"),
            feed_url=spec.url,
            categories=categories,
        )
        return payload, ""
