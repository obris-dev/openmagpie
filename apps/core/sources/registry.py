"""Connector registry. Maps kind string → Connector instance.

Values are `Connector[Any]`: each concrete connector is generic over its
own spec variant, but the registry dispatches by `kind` string so the
variant is erased here ; the call seam (`polling.py`) passes a runtime
`SourceSpec` that the kind guarantees matches the stored connector.
"""

from typing import Any

from sources.connectors import (
    Connector,
    HackerNewsCommentConnector,
    HackerNewsFeedConnector,
    RedditSubRedditConnector,
    RssConnector,
)

_REGISTRY: dict[str, Connector[Any]] = {
    RedditSubRedditConnector.kind: RedditSubRedditConnector(),
    RssConnector.kind: RssConnector(),
    HackerNewsFeedConnector.kind: HackerNewsFeedConnector(),
    HackerNewsCommentConnector.kind: HackerNewsCommentConnector(),
}


def get(kind: str) -> Connector[Any]:
    """Raises KeyError if the kind has no registered connector."""
    return _REGISTRY[kind]


def register(connector: Connector[Any]) -> None:
    _REGISTRY[connector.kind] = connector
