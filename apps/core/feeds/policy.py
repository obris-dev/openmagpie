"""Server-side Feed-config POLICY (the Django/settings-coupled guards
that can't live in the pure shared `openmagpie-schema` package).

`enforce_policy` runs at the validation seams (serializer -> 400;
service). Idempotent; pure predicates.

Guards on the feed config:
  - retention_days in [1, 365] (0/negative would prune everything;
    unbounded would grow the item log forever).

Per-source watermark guards (rows on a different model than the config):
  - `enforce_source_watermark` rejects a future `last_event_at` ; run by
    every operator-supplied write (create / set / seek). The poll path's
    `advance_watermark` never runs it: it is a compare-and-set that only
    moves a mark to an item time the connector actually returned.
  - `default_and_enforce_source_watermark` fills a missing value with now,
    then runs the check ; the Source create / set paths only.
  - `enforce_seek_floor` rejects a seek target older than the feed's
    retention window ; the seek path only.

Raises `PolicyError` (ValueError); callers map it to a 400. The watermark
guards raise `SourceWatermarkError`, a subclass, so the HTTP layer can key
a rejected target at `to` and a feed-level refusal at `detail`.
"""

from __future__ import annotations

import ipaddress
from collections.abc import Iterator
from datetime import datetime, timedelta
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


class SourceWatermarkError(PolicyError):
    """A watermark target that policy rejects: in the future, or (on a seek)
    older than the feed's retention window. About the `to` / `last_event_at`
    VALUE itself, so the HTTP layer keys it there; a plain `PolicyError` from
    the same seam is about the feed (e.g. a kind that has no sources)."""


def enforce_source_watermark(value: datetime, *, field: str = "last_event_at") -> datetime:
    """Reject a future per-source watermark (a future `last_event_at` silently
    disables the source until then). The one future check, shared by the
    create / set path (via `default_and_enforce_source_watermark`) and the
    operator seek, which passes an always-present target: a seek never
    defaults, so it must NOT route through the defaulting wrapper. `field` is
    the wire name the message cites (`last_event_at` on create / set, `to` on
    a seek), so the copy matches the key the HTTP layer reports it under.
    Returns the value unchanged (a naive datetime is read as UTC,
    `timezone.now()`'s zone, the same reading the shared window resolver gives
    a naive ISO value)."""
    now = timezone.now()
    v = value if value.tzinfo else value.replace(tzinfo=now.tzinfo)
    if v > now:
        raise SourceWatermarkError(
            f"{field} is in the future ({value.isoformat()}); a future watermark silently disables the source until then"
        )
    return value


def seek_floor(*, retention_days: int, now: datetime | None = None) -> datetime:
    """The deepest seek target the feed allows: `now - retention_days`. One
    definition, used by `enforce_seek_floor` (the check) and as the `oldest`
    keyword's referent (the target, via `resolve_window_value(floor=)`), so the
    two can't disagree."""
    return (now or timezone.now()) - timedelta(days=retention_days)


def enforce_seek_floor(to: datetime, *, retention_days: int, now: datetime | None = None) -> datetime:
    """Reject a seek target older than the feed's retention window.

    The item log forgets on one clock (prune deletes rows recorded more than
    `retention_days` ago) and the watermark could otherwise point past it. Dedup
    lives on the item log, so an item whose row was pruned comes back from a
    re-poll as brand new: a fresh ULID that every watch then processes again
    (paid judge calls, duplicate deliveries). An item is always recorded AFTER
    it occurred, so anything with `occurred_at` later than `now - retention`
    still has its row and dedups; `now - retention` is where a rewind starts
    resurrecting, assuming retention has been constant for at least that window.
    It has NOT been if `retention_days` was just raised: rows pruned under the
    old window stay gone while the floor moves out to the new one, so a seek
    into that gap resurrects them. Enforced on the seek path only today. The
    create / set path can hit the same hazard (dedup is keyed on the item's
    external id, not the source row, so a dropped-then-re-added source with a
    backdated `last_event_at` re-fetches pruned items too) and is not guarded.
    And the check is made when the seek WRITES the mark but relied on when the
    next poll READS it: prune runs every cycle (after the fetches, see
    `FeedPollOperation.run`), so if that source's first fetch after the seek
    fails, the prune line passes its mark and the next successful fetch
    re-records that slice. `oldest` sits exactly on the floor, so it has no
    slack against this. All three limits share one follow-up: have prune record
    a per-feed `pruned_through` mark and clamp the poller's fetch cursor to
    `max(last_event_at, pruned_through)`, one guard at the single reader."""
    floor = seek_floor(retention_days=retention_days, now=now)
    if to < floor:
        raise SourceWatermarkError(
            f"to is older than this feed's retention window ({retention_days} day(s), before "
            f"{floor.isoformat(timespec='seconds')}); items that far back were pruned and a re-poll "
            "would record them as new and re-run every watch on them. Pick a later target, or `oldest` for the "
            "deepest this feed allows."
        )
    return to


def default_and_enforce_source_watermark(value: datetime | None) -> datetime:
    """Fill a missing per-source `last_event_at` with wall-clock now, then
    `enforce_source_watermark`. Returned value is used as-is on the new
    Source row, making `last_event_at is None` impossible post-validation
    so the poller can treat it as a hard invariant. Operators who want
    a backfill window pass an explicit past datetime. Create / set ONLY ;
    the seek path calls `enforce_source_watermark` directly."""
    if value is None:
        return timezone.now()
    return enforce_source_watermark(value)


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
