"""The destination-URL rules shared by the HTTP delivery kinds (webhook,
bower): one structural check and one exact-match restore of a masked URL."""

from urllib.parse import urlsplit

from ._secrets import redact_url


def check_delivery_url(url: str) -> str:
    """Structural check: an http(s) URL with a host AND an in-range port.
    The SSRF gate (private-IP / require-https) is settings-coupled, so it
    lives in `watches.policy` (write) + the impl (send), not here. A
    redacted `scheme://host/***` still passes (host + scheme present), so
    an edit that leaves the URL masked validates and `merge_preserving`
    restores the real one.

    The port is validated HERE so a bad one can never persist: `.port` is
    a lazy property that raises only on access, so a URL like
    `https://h:99999/p` would otherwise pass and then crash every read
    (redacted_dump) and the send-time resolve."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError("url must be an http(s) URL with a host")
    try:
        parts.port  # noqa: B018 - property access validates the port range
    except ValueError:
        raise ValueError("url has an out-of-range port") from None
    return url


def restore_masked_url(submitted: str, prior_url: str | None) -> str:
    """The URL to keep on an edit: the prior one only when the operator sent
    back precisely the masked form shown for it (`redact_url(prior)`), not
    one that merely looks masked, so a real URL ending in `/***` is kept."""
    if prior_url is not None and submitted == redact_url(prior_url):
        return prior_url
    return submitted
