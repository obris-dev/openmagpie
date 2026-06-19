import html
import re
from datetime import UTC, datetime
from typing import Any, ClassVar

from openmagpie_schema.configs import HackerNewsCommentSourceSpec, HackerNewsFeedSourceSpec
from sources.payloads import SourcePayload

# Algolia returns Ask HN bodies (`story_text`) and all comment bodies
# (`comment_text`) as HTML, entity-encoded, same as HN stores them. The
# engine wants plain text; strip tags + unescape entities.
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _html_to_text(content_html: str) -> str:
    """Flatten an HTML body to whitespace-collapsed text. `""` in -> `""`
    out (link posts carry no body), so the engine sees title-only rather
    than embed boilerplate."""
    if not content_html:
        return ""
    text = _TAG_RE.sub(" ", content_html)
    return _WS_RE.sub(" ", html.unescape(text)).strip()


class _HackerNewsPayload(SourcePayload):
    """Shared fields for HN feed + comment payloads.

    Abstract intermediate: never registered, so payload_registry.register's
    `sample()` guard applies only to the concrete subclasses. `hn_url` is the
    discussion permalink; `feed` is the within-kind source slug (new / show /
    ask for the story feed, "comments" for the comment stream).
    """

    author: str = ""
    hn_url: str = ""
    feed: str = ""

    model_config = {"frozen": True, "extra": "ignore"}

    def source_slug(self) -> str:
        return self.feed


class HackerNewsFeedPayload(_HackerNewsPayload):
    """A Hacker News story off the new / Show HN / Ask HN feed, via Algolia."""

    PAYLOAD_KIND: ClassVar[str] = "hn_feed"

    # points / num_comments are a point-in-time snapshot from the poll.
    points: int = 0
    num_comments: int = 0

    @classmethod
    def sample(cls, variant: int = 0) -> "HackerNewsFeedPayload":
        # 1-indexed for operator-visible id/url/title (variant=0 reads as
        # "story 1"). Every field a receiver might key on stays distinct.
        n = variant + 1
        item_id = 12345 + n
        return cls(
            external_id=str(item_id),
            kind=cls.PAYLOAD_KIND,
            occurred_at=datetime(2026, 5, 27, 12, 0, tzinfo=UTC),
            source=HackerNewsFeedSourceSpec.SOURCE_KIND,
            title=f"Example HN story {n}: matched this watch",
            content="(SourcePayload.content ; included in payload only if `include_fields` lists it.)",
            url=f"https://example.com/story-{n}",
            parent_external_id="",
            author="example_user",
            points=100 + n,
            num_comments=20 + n,
            hn_url=f"https://news.ycombinator.com/item?id={item_id}",
            feed="new",
        )

    @classmethod
    def from_algolia_hit(
        cls,
        hit: dict[str, Any],
        spec: HackerNewsFeedSourceSpec,
        occurred_at: datetime,
    ) -> "HackerNewsFeedPayload":
        # objectID is the HN item id (string), matching what the Firebase
        # /item endpoint keys on, so FeedItems stay de-duped if a future
        # variant ever fetches the same story via Firebase.
        item_id = str(hit.get("objectID", ""))
        hn_url = f"https://news.ycombinator.com/item?id={item_id}"
        # Link posts carry `url`; Ask HN / text posts don't, so the canonical
        # `url` falls back to the discussion permalink (never blank).
        story_url = hit.get("url") or ""
        return cls(
            external_id=item_id,
            kind=cls.PAYLOAD_KIND,
            occurred_at=occurred_at,
            source=spec.kind,
            title=hit.get("title") or "",
            content=_html_to_text(hit.get("story_text") or ""),
            url=story_url or hn_url,
            author=hit.get("author") or "",
            points=hit.get("points") or 0,
            num_comments=hit.get("num_comments") or 0,
            hn_url=hn_url,
            feed=spec.feed,
        )


class HackerNewsCommentPayload(_HackerNewsPayload):
    """A Hacker News comment, from Algolia `tags=comment`.

    The parent story's title is mapped onto the canonical `title` field on
    purpose: the relevance engine scores only `title` + `content` (see
    engine/engines/openai_compat/prompts.py), so a bare comment fragment is
    unjudgeable without it ("+1, same problem" is meaningless without the
    thread). `content` is the comment body; `parent_external_id` is the root
    story id; `story_title` keeps the headline as its own field for display.

    Edit semantics: ingestion keys on `created_at_i` (creation time), so an
    EDITED comment (Algolia carries a later `updated_at`) is recorded once
    and never re-judged. Intentional: we listen for new conversation, not
    edits.
    """

    PAYLOAD_KIND: ClassVar[str] = "hn_comment"

    story_title: str = ""

    @classmethod
    def sample(cls, variant: int = 0) -> "HackerNewsCommentPayload":
        n = variant + 1
        item_id = 23456 + n
        story_id = 12345 + n
        story_title = f"Example HN story {n}"
        return cls(
            external_id=str(item_id),
            kind=cls.PAYLOAD_KIND,
            occurred_at=datetime(2026, 5, 27, 12, 0, tzinfo=UTC),
            source=HackerNewsCommentSourceSpec.SOURCE_KIND,
            title=story_title,  # parent headline -> canonical title (engine context)
            content=f"Example comment {n}: the comment body that matched your query.",
            url=f"https://news.ycombinator.com/item?id={item_id}",
            parent_external_id=str(story_id),
            author="example_user",
            hn_url=f"https://news.ycombinator.com/item?id={item_id}",
            feed="comments",
            story_title=story_title,
        )

    @classmethod
    def from_algolia_hit(
        cls,
        hit: dict[str, Any],
        spec: HackerNewsCommentSourceSpec,
        occurred_at: datetime,
    ) -> "HackerNewsCommentPayload":
        item_id = str(hit.get("objectID", ""))
        hn_url = f"https://news.ycombinator.com/item?id={item_id}"
        story_id = hit.get("story_id")
        story_title = hit.get("story_title") or ""
        return cls(
            external_id=item_id,
            kind=cls.PAYLOAD_KIND,
            occurred_at=occurred_at,
            source=spec.kind,
            title=story_title,  # parent headline -> canonical title (engine context)
            content=_html_to_text(hit.get("comment_text") or ""),
            # a comment has no outbound link, so canonical url == hn_url (the permalink)
            url=hn_url,
            parent_external_id=str(story_id) if story_id is not None else "",
            author=hit.get("author") or "",
            hn_url=hn_url,
            feed="comments",
            story_title=story_title,
        )
