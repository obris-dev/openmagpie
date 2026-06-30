"""The typed `FeedItem.data` dump: the item-payload union.

`FeedItem.data` is one connector SourcePayload's `model_dump()`. The server's
SourcePayload hierarchy (apps/core/sources) carries connector machinery
(sample(), parsing, registry) that doesn't cross the wire ; these mirror only
the DATA fields a reader consumes, so the canonical engine inputs (title /
content / url ...) are typed and connector-specific fields (subreddit, ...) are
typed per kind. `kind` is the PAYLOAD_KIND (e.g. "new_post"), NOT the connector
kind on FeedItemWire.source_kind (e.g. "reddit_subreddit").

Split from `feed` (which owns the feed / source / wire envelopes) so each module
stays under the line cap and one concern per file.
"""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field


class FeedItemPayload(BaseModel):
    """Base payload: the canonical engine-input fields every connector maps
    onto, plus a catch-all for keys this build doesn't model. `extra="allow"`
    keeps unmodeled connector fields readable, and this is also the FALLBACK
    union member: a payload whose `kind` matches no known variant (a newer
    connector, or older data) validates here instead of breaking the read."""

    model_config = {"extra": "allow"}

    kind: str = ""  # PAYLOAD_KIND discriminator (e.g. "hn_feed"), not the connector kind (FeedItemWire.source_kind)
    external_id: str = ""  # the item's stable id on its source; FeedItem dedup keys on it
    source: str = ""  # the connector kind that produced it (e.g. "hn_feed", "reddit_subreddit")
    occurred_at: datetime | None = None  # when the item was published at its source
    title: str = ""  # the item's headline (engine input)
    content: str = ""  # the item's own body, the poster's words ("" for a bare link); engine input
    url: str = ""  # the item's canonical page on its source (Reddit comments, HN discussion); not the off-site link
    external_url: str = ""  # off-platform link this item points to ("" if self-contained); the article fetch reads this
    parent_external_id: str = ""  # external_id of the parent item (a comment's root story); "" for top-level


class RssEntryPayload(FeedItemPayload):
    """`rss_entry`: one RSS/Atom entry (RssEntryConnector)."""

    kind: Literal["rss_entry"]  # required, so a non-rss dump can't match here
    author: str = ""
    feed_url: str = ""
    categories: list[str] = Field(default_factory=list)


class NewRedditPostPayload(FeedItemPayload):
    """`new_post`: one post off a subreddit's /new (RedditSubredditConnector)."""

    kind: Literal["new_post"]  # required, so a non-reddit dump can't match here
    author: str = ""
    permalink: str = ""
    subreddit: str = ""


class HackerNewsFeedPayload(FeedItemPayload):
    """`hn_feed`: one Hacker News story / Show HN / Ask HN post (HackerNewsFeedConnector)."""

    kind: Literal["hn_feed"]  # required, so a non-hn dump can't match here
    author: str = ""
    points: int = 0
    num_comments: int = 0
    feed: str = ""  # new / show / ask


class HackerNewsCommentPayload(FeedItemPayload):
    """`hn_comment`: one Hacker News comment (HackerNewsCommentConnector).

    `title` carries the parent story's headline (the relevance engine scores
    only title+content, so a bare comment is unjudgeable without it); `content`
    is the comment body; `parent_external_id` is the root story id."""

    kind: Literal["hn_comment"]  # required, so a non-hn dump can't match here
    author: str = ""
    feed: str = ""  # always "comments"
    story_title: str = ""


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
    RssEntryPayload | NewRedditPostPayload | HackerNewsFeedPayload | HackerNewsCommentPayload | FeedItemPayload,
    Field(union_mode="left_to_right"),
]
