"""Server-side Feed-config POLICY (the Django/settings-coupled guards
that can't live in the pure shared `openmagpie-schema` package).

`enforce_policy` runs at the validation seams (serializer -> 400;
service). Idempotent; pure predicates.

Guards on the feed config:
  - retention_days in [1, 365] (0/negative would prune everything;
    unbounded would grow the item log forever).

Per-source `last_event_at` defaulting + future-watermark rejection
moved to `default_and_enforce_source_watermark`, called from the
Source create / set paths (rows on a different model than the config).

Raises `PolicyError` (ValueError); callers map it to a 400.
"""

from __future__ import annotations

import ipaddress
from collections.abc import Iterator
from datetime import datetime
from urllib.parse import urlparse

from django.conf import settings
from django.utils import timezone

from feeds.configs import FeedConfig
from openmagpie_schema.configs import PluginSourceSpec, SourceSpec

RETENTION_MIN_DAYS = 1
RETENTION_MAX_DAYS = 365


class PolicyError(ValueError):
    """A feed config violates server policy. Mapped to a 400 at the HTTP
    boundary; the message is operator-facing."""


def default_and_enforce_source_watermark(value: datetime | None) -> datetime:
    """Fill a missing per-source `last_event_at` with wall-clock now;
    reject a future watermark. Returned value is used as-is on the new
    Source row, making `last_event_at is None` impossible post-validation
    so the poller can treat it as a hard invariant. Operators who want
    a backfill window pass an explicit past datetime."""
    now = timezone.now()
    if value is None:
        return now
    v = value if value.tzinfo else value.replace(tzinfo=now.tzinfo)
    if v > now:
        raise PolicyError(
            f"last_event_at is in the future ({value.isoformat()}); "
            "a future watermark silently disables the source until then"
        )
    return value


def _check_url_ip(candidate: str) -> None:
    """Reject `candidate` if it parses as a URL whose host is an IP LITERAL in a
    blocked range (loopback / private / link-local / metadata). A plain hostname is
    left for the connector to resolve + re-check at poll time, and a non-URL string
    parses to no host and is skipped."""
    try:
        # Both the parse AND the .hostname access are inside the try: since the scan
        # now feeds arbitrary plugin-blob strings, a malformed URL like `http://[::1`
        # raises ValueError from urlparse itself (not just ipaddress), which would 500
        # the write. A string that can't yield a blocked IP literal is a skip, not a 400.
        parsed = urlparse(candidate)
        if not parsed.hostname:
            return
        ip = ipaddress.ip_address(parsed.hostname)
    except ValueError:
        return  # not a parseable URL / not an IP literal host ; nothing to block here
    if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved:
        raise PolicyError(f"SOURCE_BLOCK_PRIVATE_IPS is set; URL host resolves to blocked IP {ip}")


def _iter_strings(value: object) -> Iterator[str]:
    """Every string leaf of a (possibly nested) dumped model value."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _iter_strings(item)
    elif isinstance(value, list | tuple):
        for item in value:
            yield from _iter_strings(item)


def _enforce_source_url_safety(spec: SourceSpec) -> None:
    """Operational SSRF gate at the create seam: reject a spec carrying a URL whose
    host is a blocked IP literal (a 400, not a poll-time fetch).

    Two scopes, so the check lands on fields the connector actually FETCHES:
    - A built-in spec declares its fetched-URL fields via `URL_FIELDS` (empty for a
      spec with none, e.g. reddit / hn), so only those are checked. A display-only
      field (e.g. RssSourceSpec.name) is NOT scanned and won't 400 for containing a
      private-IP-looking string it never dereferences. `URL_FIELDS` must name SCALAR
      string fields: a list/dict-valued field would `str(...)` to a non-URL and be
      skipped (put a multi-URL spec behind the open plugin fallback, which walks leaves).
    - The plugin fallback (`PluginSourceSpec`) is an open blob with no declared fields,
      so EVERY string leaf is scanned: a fork spec that exposes an operator-supplied
      URL gets this defense-in-depth for free (the tradeoff is that a private-IP-looking
      string in an unrelated plugin field is also rejected; the fork controls its blob).

    Best-effort and write-time only: a plain hostname passes here and MUST still be
    re-checked by the connector at poll time (DNS can change between create and poll; a
    public host can 302 to an internal target). Structural URL checks for the built-in
    RSS spec already ran in the schema (`RssSourceSpec._validate_url_structural`)."""
    if not settings.SOURCE_BLOCK_PRIVATE_IPS:
        return
    url_fields = getattr(spec, "URL_FIELDS", None)
    # `isinstance` MUST come first (not just `url_fields is None`): PluginSourceSpec is
    # extra="allow", so an operator could smuggle a `"URL_FIELDS": []` key into the blob,
    # which surfaces via getattr and would steer the else-branch scan away from the real
    # URL fields. The short-circuit forces the open blob down the full-scan path.
    if isinstance(spec, PluginSourceSpec) or url_fields is None:
        # Scan every string leaf when the fields aren't known: the open plugin blob, OR
        # a typed spec that DIDN'T declare URL_FIELDS. Fail SAFE: an undeclared spec is
        # over-scanned, not silently skipped (a spec with no fetched URL opts out
        # explicitly with URL_FIELDS = (), which is distinct from "forgot to declare").
        # mode="json" (not "python") so a pydantic URL field (HttpUrl / AnyUrl) dumps as
        # a string, not a `Url` object the str-only walk would skip (matches canonical_spec).
        candidates: Iterator[str] = _iter_strings(spec.model_dump(mode="json"))
    else:
        # Declared: check only the connector's fetched-URL fields (skips display-only
        # fields, so they don't 400 for containing a private-IP-looking string).
        candidates = (str(getattr(spec, field)) for field in url_fields)
    for candidate in candidates:
        _check_url_ip(candidate)


def enforce_source_spec_safety(specs: list[SourceSpec]) -> None:
    """Apply server-policy URL safety guards to every spec. Called from
    `SourceService.set_sources` so a CLI / API create or replace fails
    loud with a 400 instead of reaching the connector."""
    for spec in specs:
        _enforce_source_url_safety(spec)


def _enforce_retention(config: FeedConfig) -> None:
    """retention_days must be a sane bound."""
    days = getattr(config, "retention_days", None)
    if days is None:
        return
    if not (RETENTION_MIN_DAYS <= days <= RETENTION_MAX_DAYS):
        raise PolicyError(f"retention_days must be between {RETENTION_MIN_DAYS} and {RETENTION_MAX_DAYS} (got {days})")


def enforce_policy(config: FeedConfig) -> FeedConfig:
    """Apply every server policy guard on the feed config; return the
    config or raise `PolicyError`. Idempotent. Per-source watermark
    policy runs on the Source create/set paths, not here."""
    _enforce_retention(config)
    return config
