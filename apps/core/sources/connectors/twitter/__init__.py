"""X (Twitter) connector, unofficial route (twikit), ported from listeningkit.

One file per concern:
  - `connector.py` ; the `TwitterSearchConnector` impl (poll loop)
  - `client.py` ; `TwikitClient` wrapper (cookies env/file/credentials-dir,
    proxy attachment, error translation)
  - `payloads.py` ; `NewTweetPayload` (twikit Tweet -> SourcePayload)
  - `errors.py` ; twikit error taxonomy -> canonical TwitterError

Future variants (user timeline, list timeline) reuse `TwikitClient` with
their own spec + payload.
"""

from .connector import TwitterSearchConnector
from .payloads import NewTweetPayload

__all__ = ["NewTweetPayload", "TwitterSearchConnector"]
