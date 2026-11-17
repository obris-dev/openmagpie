"""Secret-redaction helpers shared by secret-bearing action configs.

The secret-bearing kinds: `webhook` (its URL path/query + header values)
and `bower` (its webhook URL, whose path carries the token).
The redaction sentinel + URL masking live here so the read path and the
edit round-trip (`redacted_dump` / `merge_preserving`) agree on one
representation.
"""

from urllib.parse import urlsplit

# What a redacted secret is dumped as, and recognized by on an edit
# round-trip (the operator leaving it masked means "keep the stored one").
REDACTED = "***"


def redact_url(url: str) -> str:
    """Mask a delivery URL to `scheme://host[:port]/***`: the path/query
    (where tokens often hide) are dropped, the destination host kept so the
    redacted view stays recognizable. NEVER raises ; it's on the read path
    (GET / CLI preview), so a malformed stored URL falls back to the bare
    sentinel rather than 500ing the listing.

    `.hostname` and `.port` are read inside the try because both can raise
    on a degenerate URL (an out-of-range port). An IPv6 host is
    re-bracketed: `parts.hostname` strips the `[]`, so without this the
    rebuilt netloc would reparse to a different (or null) host."""
    try:
        parts = urlsplit(url)
        host = parts.hostname
        port = parts.port  # property; raises ValueError on out-of-range
    except ValueError:
        return REDACTED
    if not parts.scheme or not host:
        return REDACTED
    if ":" in host:  # IPv6 literal, re-add the brackets hostname stripped
        host = f"[{host}]"
    netloc = f"{host}:{port}" if port else host
    return f"{parts.scheme}://{netloc}/{REDACTED}"


def looks_redacted_url(url: str) -> bool:
    """Conservative 'might this be a masked URL?' check, by shape.

    Used ONLY to REFUSE an ambiguous secret merge (a false positive there
    is a safe, recoverable refusal) ; NEVER to silently restore a prior
    secret. The restore path compares against the exact `redact_url(prior)`
    instead, so a real URL that merely ends in `/***` is kept, not swapped."""
    return url == REDACTED or url.endswith(f"/{REDACTED}")
