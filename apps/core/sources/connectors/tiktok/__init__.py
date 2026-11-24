"""TikTok search connector, public surface.

One file per concern:
  - `client.py` ; the SerpBase `/tiktok/search` client: the key, the request
    envelope (HTTP 200 is not success), rate limits, and the response shape
  - `connector.py` ; the Connector: one keyword search per poll, filtered by the
    source's watermark
  - `payloads.py` ; our internal SourcePayload subclass

Needs SERPBASE_API_KEY (the connector's `required_settings`).
"""

from .connector import TikTokSearchConnector
from .payloads import NewTikTokVideoPayload

__all__ = [
    "NewTikTokVideoPayload",
    "TikTokSearchConnector",
]
