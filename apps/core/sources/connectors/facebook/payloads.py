"""Facebook payloads: a group post observed via the Camofox client.

Maps the facebook-camofox-client normalized record shape onto the
openmagpie `SourcePayload` contract: the engine judges `title` +
`content`, so a post's body text goes to `content` and the group ID
becomes the within-kind `source_slug`. Metrics / refs / media stay on
the payload as source-specific fields.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, ClassVar

from openmagpie_schema.configs import FacebookGroupSourceSpec
from sources.payloads import SourcePayload

# Facebook post URL base; permalinks are https://facebook.com/groups/<gid>/posts/<id>
FACEBOOK_GROUP_URL = "https://facebook.com/groups"


class NewFacebookPostPayload(SourcePayload):
    """A single Facebook group post observed by a watched group search.

    `author` is the poster's display name; `group_id` is the Facebook
    group ID and the within-kind source slug (grouping items by producing
    group). `content` is the post body (the engine's judgeable text).
    The rest is source-specific: `metrics` (likes/comments/shares),
    `matched_terms`, `raw_extraction`.
    """

    PAYLOAD_KIND: ClassVar[str] = "new_fb_post"

    author: str = ""
    group_id: str = ""
    metrics: dict[str, int | None] = {}
    matched_terms: list[str] = []

    model_config = {"frozen": True, "extra": "ignore"}

    def source_slug(self) -> str | None:
        return self.group_id or None

    @classmethod
    def sample(cls, variant: int = 0) -> NewFacebookPostPayload:
        n = variant + 1
        post_id = f"fb_post_{n}"
        group_id = f"group_{n}"
        return cls(
            external_id=post_id,
            kind=cls.PAYLOAD_KIND,
            occurred_at=datetime(2026, 5, 27, 12, 0, tzinfo=UTC),
            source=FacebookGroupSourceSpec.SOURCE_KIND,
            title="",
            content=f"Example Facebook post {n}: the post text that matched this watch.",
            url=f"{FACEBOOK_GROUP_URL}/{group_id}/posts/{post_id}",
            author=f"Example User {n}",
            group_id=group_id,
            metrics={"likes": 10 + n, "comments": 2 + n, "shares": n},
            matched_terms=["example"],
        )

    @classmethod
    def from_record(cls, record: dict[str, Any], query_terms: list[str] | None = None) -> NewFacebookPostPayload:
        """Map a facebook-worker.py normalized record dict to a payload.

        The worker returns records in the NormalizedPostRecord shape
        (record_id / external_id / group_id / content / url / author /
        occurred_at / metrics). The tests hand in lightweight fakes with
        the same key names, so no Camofox import is needed.
        """
        external_id = str(record.get("external_id") or record.get("record_id") or "")
        group_id = str(record.get("group_id") or "")
        author = record.get("author") or {}
        author_name = str(author.get("name") or "") if isinstance(author, dict) else str(author or "")

        occurred_at = record.get("occurred_at")
        if isinstance(occurred_at, str):
            try:
                occurred_at = datetime.fromisoformat(occurred_at.replace("Z", "+00:00"))
            except ValueError:
                occurred_at = datetime.now(UTC)
        if not isinstance(occurred_at, datetime):
            occurred_at = datetime.now(UTC)
        if occurred_at.tzinfo is None:
            occurred_at = occurred_at.replace(tzinfo=UTC)

        metrics_raw = record.get("metrics") or {}
        metrics = {
            "likes": int_or_none(metrics_raw.get("likes")),
            "comments": int_or_none(metrics_raw.get("comments")),
            "shares": int_or_none(metrics_raw.get("shares")),
        }

        url = str(record.get("url") or "")
        if not url and group_id and external_id:
            url = f"{FACEBOOK_GROUP_URL}/{group_id}/posts/{external_id}"

        return cls(
            external_id=external_id,
            kind=cls.PAYLOAD_KIND,
            occurred_at=occurred_at,
            source=FacebookGroupSourceSpec.SOURCE_KIND,
            title="",
            content=str(record.get("content") or ""),
            url=url,
            author=author_name,
            group_id=group_id,
            metrics=metrics,
            matched_terms=list(record.get("matched_terms") or query_terms or []),
        )


def int_or_none(obj: Any) -> int | None:
    """Safely convert to int or return None."""
    if obj is None:
        return None
    try:
        return int(obj)
    except (ValueError, TypeError):
        return None
