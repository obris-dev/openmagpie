"""Facebook connectors, public surface.

One file per concern:
  - `connector.py`, the Connector implementation(s) + polling logic
  - `payloads.py`, our internal SourcePayload subclasses
  - `client.py`, the subprocess client that calls the facebook-worker.py
  - `errors.py`, error taxonomy
"""

from .connector import FacebookGroupConnector
from .payloads import NewFacebookPostPayload

__all__ = [
    "FacebookGroupConnector",
    "NewFacebookPostPayload",
]
