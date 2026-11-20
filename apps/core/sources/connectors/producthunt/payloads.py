"""Product Hunt payloads: a launch observed via the GraphQL API.

The engine judges title + content, so the product name is the title and the
tagline plus description is the content. `external_url` stays empty: the API
gives the company site only as a Product Hunt redirect behind a bot challenge,
so there is no page to fetch. Lists are joined into text fields so each one can
be a column when the item is sent on.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, ClassVar

from openmagpie_schema.configs import ProductHuntLaunchesSourceSpec
from sources.payloads import SourcePayload

# Product Hunt's API returns this in place of a user's name and username unless
# the token's app was granted access to user data.
REDACTED = "[REDACTED]"


class NewProductHuntLaunchPayload(SourcePayload):
    """A single Product Hunt front-page launch.

    `author` is the first maker whose name the API shows, `makers` every such
    maker; both are empty when the API redacts them. `source_topic` is the topic
    slug the producing source is narrowed to ("" for every topic), and the
    within-kind source slug; `topics` is the launch's own topic names."""

    PAYLOAD_KIND: ClassVar[str] = "new_producthunt_launch"

    author: str = ""
    tagline: str = ""
    makers: str = ""
    topics: str = ""
    source_topic: str = ""

    model_config = {"frozen": True, "extra": "ignore"}

    def source_slug(self) -> str | None:
        return self.source_topic or None

    @classmethod
    def sample(cls, variant: int = 0) -> NewProductHuntLaunchPayload:
        n = variant + 1
        return cls(
            external_id=str(900_000 + n),
            kind=cls.PAYLOAD_KIND,
            occurred_at=datetime(2026, 5, 27, 7, 1, tzinfo=UTC),
            source=ProductHuntLaunchesSourceSpec.SOURCE_KIND,
            title=f"Example Product {n}",
            content=f"The one-line pitch {n}\n\nWhat the product does, in the maker's words.",
            url=f"https://www.producthunt.com/posts/example-product-{n}",
            author="Example Maker",
            tagline=f"The one-line pitch {n}",
            makers="Example Maker, Second Maker",
            topics="Developer Tools, Productivity",
            source_topic="developer-tools",
        )

    @classmethod
    def from_post(
        cls, post: dict[str, Any], spec: ProductHuntLaunchesSourceSpec, *, occurred_at: datetime
    ) -> NewProductHuntLaunchPayload:
        """Map one GraphQL `Post` node, read for `spec`, to a payload."""
        tagline = str(post.get("tagline") or "")
        description = str(post.get("description") or "")
        makers = [name for name in (_maker_name(m) for m in post.get("makers") or []) if name]
        topics = [str(node.get("name") or "") for node in _topic_nodes(post.get("topics"))]
        return cls(
            external_id=str(post.get("id") or ""),
            kind=cls.PAYLOAD_KIND,
            occurred_at=occurred_at,
            source=ProductHuntLaunchesSourceSpec.SOURCE_KIND,
            title=str(post.get("name") or ""),
            content="\n\n".join(part for part in (tagline, description) if part),
            url=_without_query(str(post.get("url") or "")),
            author=makers[0] if makers else "",
            tagline=tagline,
            makers=", ".join(makers),
            topics=", ".join(name for name in topics if name),
            source_topic=spec.topic,
        )


def parse_timestamp(raw: object) -> datetime | None:
    """An ISO-8601 API timestamp as an aware datetime, or None."""
    if not isinstance(raw, str) or not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _maker_name(maker: Any) -> str:
    if not isinstance(maker, dict):
        return ""
    name = str(maker.get("name") or "").strip()
    return "" if name == REDACTED else name


def _without_query(url: str) -> str:
    return url.split("?", 1)[0]


def _topic_nodes(topics: Any) -> list[dict[str, Any]]:
    """The topic nodes of a post's `topics` connection; anything malformed is skipped."""
    edges = topics.get("edges") if isinstance(topics, dict) else None
    if not isinstance(edges, list):
        return []
    nodes = (edge.get("node") for edge in edges if isinstance(edge, dict))
    return [node for node in nodes if isinstance(node, dict)]
