import html
import re
from datetime import UTC, datetime
from typing import Any, ClassVar

from openmagpie_schema.configs import RedditSubredditSourceSpec
from sources.payloads import SourcePayload

# Reddit wraps the post body in `<!-- SC_OFF --> ... <!-- SC_ON -->` and
# appends a "submitted by ... [link] [comments]" trailer. We want just the
# body; the trailer is noise to the engine.
_SC_BLOCK_RE = re.compile(r"<!--\s*SC_OFF\s*-->(.*?)<!--\s*SC_ON\s*-->", re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _atom_content_to_text(content_html: str) -> str:
    """Pull the user's body out of an Atom `<content type="html">` payload.

    Returns `""` for link posts (no SC_OFF block, just media markup) so the
    engine sees title-only and doesn't get confused by image-embed boilerplate.
    """
    match = _SC_BLOCK_RE.search(content_html)
    if not match:
        return ""
    body_html = match.group(1)
    body = _TAG_RE.sub(" ", body_html)
    return _WS_RE.sub(" ", html.unescape(body)).strip()


def _feedparser_content_html(entry: Any) -> str:
    """Atom `<content type="html">` lands in `entry.content` as a list of
    FeedParserDicts (one per representation Atom can carry). Reddit only
    ships one; pull its `.value`. Missing = link post; return ""."""
    content = entry.get("content")
    if not content:
        return ""
    first = content[0]
    return first.get("value", "") if isinstance(first, dict) else str(first)


class NewRedditPostPayload(SourcePayload):
    """A new top-level post observed in a watched subreddit."""

    PAYLOAD_KIND: ClassVar[str] = "new_post"

    # Reddit-specific fields. `author` lives here (not on SourcePayload base)
    # because "who emitted this" is a source-shaped concept, Slack has `user`,
    # scheduled jobs have nothing, etc. score/comments/ratio are not on the
    # Atom feed; add them back when this connector switches to authenticated
    # oauth.reddit.com.
    author: str = ""
    permalink: str
    subreddit: str = ""

    model_config = {"frozen": True, "extra": "ignore"}

    def source_slug(self) -> str:
        return self.subreddit

    @classmethod
    def sample(cls, variant: int = 0) -> "NewRedditPostPayload":
        # 1-indexed for the operator-visible id/url/title (so `variant=0`
        # reads as "post 1", `variant=1` as "post 2"). Keeps every field
        # that the receiver might key on (external_id, url, permalink,
        # title) honestly distinct across variants.
        n = variant + 1
        slug = f"1example{n}"
        return cls(
            external_id=slug,
            kind=cls.PAYLOAD_KIND,
            occurred_at=datetime(2026, 5, 27, 12, 0, tzinfo=UTC),
            source=RedditSubredditSourceSpec.SOURCE_KIND,
            title=f"Example post {n}: matched this watch",
            content="(SourcePayload.content ; included in payload only if `include_fields` lists it.)",
            url=f"https://www.reddit.com/r/example/comments/{slug}/example_post_{n}/",
            parent_external_id="",
            subreddit="example",
            permalink=f"/r/example/comments/{slug}/example_post_{n}/",
            author="example_user",
        )

    @classmethod
    def from_feedparser_entry(cls, entry: Any, published: datetime) -> "NewRedditPostPayload":
        # No `spec` param: a combined `/r/a+b+c/new.rss` fetch has no single
        # source spec, and each entry's own `<category term>` names its
        # subreddit. `source` is the fixed kind constant (every reddit_subreddit
        # spec carries the same `kind`). A tag-less entry yields subreddit=""; the
        # connector (_walk_new) raises ConnectorParseError on it rather than
        # routing a post with no sub.
        #
        # Atom id is `t3_<post-id>`; the post id alone matches what the JSON
        # endpoint returned, so existing FeedItems keyed on the bare id stay
        # de-duped across the connector swap.
        post_id = entry.get("id", "").removeprefix("t3_")
        # `/u/username` -> `username`. Deleted users come back as `/u/[deleted]`.
        author = entry.get("author", "").removeprefix("/u/")
        link = entry.get("link", "")
        # `link` is the absolute comments URL; the permalink is the path part.
        permalink = link.removeprefix("https://www.reddit.com") or "/"
        # Atom `<category term="...">` ; feedparser exposes it as `tags`.
        tags = entry.get("tags") or []
        subreddit = tags[0].get("term", "") if tags and isinstance(tags[0], dict) else ""
        return cls(
            external_id=post_id,
            kind=cls.PAYLOAD_KIND,
            occurred_at=published,
            source=RedditSubredditSourceSpec.SOURCE_KIND,
            title=entry.get("title", ""),
            content=_atom_content_to_text(_feedparser_content_html(entry)),
            author=author,
            url=link,
            permalink=permalink,
            subreddit=subreddit,
        )
