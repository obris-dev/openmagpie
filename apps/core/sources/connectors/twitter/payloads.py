"""X (Twitter) payloads: a tweet observed via the unofficial twikit route.

Shapes the listeningkit SocialEvent normalization (see
REPOS/listeningkit/docs/okf/backend/domains/twitter/unofficial/parsing.md)
onto the openmagpie `SourcePayload` contract: the engine judges `title` +
`content`, so a tweet's text goes to `content` and the author's handle
becomes the within-kind `source_slug`. Metrics / refs / media stay on the
payload as source-specific fields (available to actions that read them,
omitted from the engine prompt unless included).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, ClassVar

from openmagpie_schema.configs import TwitterSearchSourceSpec
from sources.payloads import SourcePayload

# Tweet URL base; a tweet's permalink is https://x.com/<handle>/status/<id>.
X_STATUS_URL = "https://x.com"


class NewTweetPayload(SourcePayload):
    """A single tweet observed by a watched X search stream.

    `author` is the user's display name; `handle` is the @screen_name and the
    within-kind source slug (grouping items by producing account). `content`
    is the tweet's full text (the engine's judgeable body). The rest is the
    listeningkit event shape carried as payload fields: `metrics`, `refs`
    (in_reply_to / quoted / retweet_of), `media`, `lang`.
    """

    PAYLOAD_KIND: ClassVar[str] = "new_tweet"

    author: str = ""
    handle: str = ""
    lang: str = ""
    metrics: dict[str, int | None] = {}
    refs: dict[str, str | None] = {}
    media: list[dict[str, Any]] = []

    model_config = {"frozen": True, "extra": "ignore"}

    def source_slug(self) -> str | None:
        return self.handle or None

    @classmethod
    def sample(cls, variant: int = 0) -> NewTweetPayload:
        n = variant + 1
        tweet_id = str(999_000_000_000_000_000 + n)
        handle = f"example_user_{n}"
        return cls(
            external_id=tweet_id,
            kind=cls.PAYLOAD_KIND,
            occurred_at=datetime(2026, 5, 27, 12, 0, tzinfo=UTC),
            source=TwitterSearchSourceSpec.SOURCE_KIND,
            title="",
            content=f"Example tweet {n}: the post text that matched this watch.",
            url=f"{X_STATUS_URL}/{handle}/status/{tweet_id}",
            author=f"Example User {n}",
            handle=handle,
            lang="en",
            metrics={"likes": 100 + n, "retweets": 20 + n, "replies": 5 + n, "quotes": 2 + n, "views": 1000 + n},
            refs={"in_reply_to": None, "quoted": None, "retweet_of": None},
            media=[],
        )

    @classmethod
    def from_tweet(cls, tweet: Any, query: str | None = None) -> NewTweetPayload:
        """Map a twikit `Tweet` (or a duck-typed test double) to a payload.

        Kept attribute-driven (getattr with a default) so the connector's
        unit tests can hand in lightweight fakes without importing twikit;
        the real twikit Tweet supplies the same attributes. `query` is
        recorded nowhere on the payload (the SourceSpec carries it); it is
        accepted for symmetry with the listeningkit event's listenId and
        future field_map use.
        """
        del query
        tweet_id = str(getattr(tweet, "id", None) or "")
        user = getattr(tweet, "user", None)
        handle = ""
        author = ""
        if user is not None:
            handle = str(getattr(user, "screen_name", None) or getattr(user, "username", None) or "")
            author = str(getattr(user, "name", None) or "")
        text = getattr(tweet, "full_text", None) or getattr(tweet, "text", None) or ""
        created = getattr(tweet, "created_at_datetime", None) or getattr(tweet, "created_at", None)
        occurred_at = created if isinstance(created, datetime) else datetime.now(UTC)
        if occurred_at.tzinfo is None:
            occurred_at = occurred_at.replace(tzinfo=UTC)
        lang = str(getattr(tweet, "lang", None) or "")

        def _id(obj: Any) -> str | None:
            return str(getattr(obj, "id", None)) if obj is not None else None

        media = []
        for m in getattr(tweet, "media", None) or []:
            media.append(
                {
                    "type": getattr(m, "type", None),
                    "url": getattr(m, "media_url_https", None) or getattr(m, "media_url", None),
                    "thumbnail": getattr(m, "thumbnail_url", None),
                }
            )

        return cls(
            external_id=tweet_id,
            kind=cls.PAYLOAD_KIND,
            occurred_at=occurred_at,
            source=TwitterSearchSourceSpec.SOURCE_KIND,
            title="",
            content=text,
            url=f"{X_STATUS_URL}/{handle}/status/{tweet_id}" if handle else "",
            author=author,
            handle=handle,
            lang=lang,
            metrics={
                "likes": getattr(tweet, "favorite_count", None),
                "retweets": getattr(tweet, "retweet_count", None),
                "replies": getattr(tweet, "reply_count", None),
                "quotes": getattr(tweet, "quote_count", None),
                "views": getattr(tweet, "view_count", None),
            },
            refs={
                "in_reply_to": _id(getattr(tweet, "in_reply_to", None)),
                "quoted": _id(getattr(tweet, "quote", None)),
                "retweet_of": _id(getattr(tweet, "retweeted_tweet", None)),
            },
            media=media,
        )
