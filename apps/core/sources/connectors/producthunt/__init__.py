"""Product Hunt connector, public surface.

One file per concern:
  - `client.py` ; the GraphQL API client (`ProductHuntClient`): the token, the
    `posts` query, rate limits, and the response shape (`Page`)
  - `connector.py` ; the Connector: walks launch days oldest first under a
    per-poll page budget, re-reading the watermark's day
  - `payloads.py` ; our internal SourcePayload subclass

Front-page launches only, optionally narrowed to one topic. Needs
PRODUCT_HUNT_TOKEN (the connector's `required_settings`).
"""

from .connector import ProductHuntLaunchesConnector
from .payloads import NewProductHuntLaunchPayload

__all__ = [
    "NewProductHuntLaunchPayload",
    "ProductHuntLaunchesConnector",
]
