"""Base SourcePayload, the typed in-memory item a connector produces.

A `SourcePayload` is the canonical, in-memory form of one item fetched from
a source (an RSS entry, a Reddit post, ...). Concrete subclasses live next to
their connector (e.g. `sources/connectors/reddit/payloads.py`). When an
item is recorded, the full `payload.model_dump()` is what gets stored in
`FeedItem.data`, and the payload registry hydrates that dump back into a typed
`SourcePayload` when an action needs to judge it.
"""

from datetime import datetime
from typing import ClassVar

from pydantic import BaseModel


class SourcePayload(BaseModel):
    """Base typed payload. Subclasses canonicalize source-specific data."""

    PAYLOAD_KIND: ClassVar[str]

    external_id: str
    kind: str  # equals PAYLOAD_KIND of the concrete subclass
    occurred_at: datetime
    source: str  # connector kind, e.g. "reddit_subreddit"

    # Canonical engine input fields (subclasses map source-native fields -> these)
    title: str = ""
    content: str = ""
    url: str = ""
    # The off-platform link this item points to ("" when self-contained, e.g. a
    # text post or a Reddit thread). Distinct from `url` (the item's own page on
    # its source); the relevance engine's lazy article-fetch reads THIS, not `url`.
    external_url: str = ""
    parent_external_id: str = ""

    model_config = {"frozen": True}

    @classmethod
    def __pydantic_init_subclass__(cls, **kwargs: object) -> None:
        """No subclass may declare a field named `relevance_score`.

        A relevance score can be layered onto the payload dump AFTER
        `model_dump()` by downstream display / result paths, so a
        same-named source field would be silently clobbered. Runs in
        `__pydantic_init_subclass__` (post-field-collection) so this
        catches subclass-declared fields, which `__init_subclass__` runs
        too early to see.
        """
        super().__pydantic_init_subclass__(**kwargs)
        if "relevance_score" in cls.model_fields:
            raise TypeError(
                f"{cls.__name__} declares a field named 'relevance_score', which collides with "
                "the engine's relevance_score that downstream layers add onto the payload dump. "
                "Rename the source field."
            )

    def source_slug(self) -> str | None:
        """The within-kind source identifier for this payload.

        Subclasses override when their source kind has a meaningful
        sub-identifier (Reddit subreddit, GitHub `owner/repo`, Slack
        channel, ...). Used to group items by producing source.
        """
        return None

    @classmethod
    def sample(cls, variant: int = 0) -> "SourcePayload":
        """Return a synthetic instance for payload previews.

        `variant` lets a preview show N distinct items in the same source
        group ; each connector decides how to vary observably (different
        external_id, url, title). Subclasses MUST produce a distinct
        payload for each variant index a caller passes.

        Each connector's payload must implement this so an operator wiring
        up a webhook can see the exact shape their receiver will get
        BEFORE any real items land. No safe default: a passthrough would
        surface as a Pydantic validation error on the required fields a
        subclass adds.
        """
        raise NotImplementedError(f"{cls.__name__} must implement sample() for payload-preview support")
