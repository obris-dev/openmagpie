"""Hacker News connectors, public surface.

One file per concern:
  - `algolia.py` ; the HN Algolia search client (`AlgoliaSearch`): paging,
    the `since` watermark filter, the `query` keyword pre-filter, body cap
  - `connector.py` ; the Connector implementations, thin adapters that hold an
    `AlgoliaSearch` and supply their own tag + payload
  - `payloads.py` ; our internal SourcePayload subclasses

Stories via the new/show/ask tags; comments via `tags=comment`, keyword-
filtered. Future variants reuse `AlgoliaSearch` with their own tag + payload.
"""

from .connector import HackerNewsCommentConnector, HackerNewsFeedConnector
from .payloads import HackerNewsCommentPayload, HackerNewsFeedPayload

__all__ = [
    "HackerNewsCommentConnector",
    "HackerNewsCommentPayload",
    "HackerNewsFeedConnector",
    "HackerNewsFeedPayload",
]
