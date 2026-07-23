"""Short-link operations: mint, resolve, record clicks, roll up stats.

Not account-scoped (see ShortLink): static methods only. Reads go through here
(no ORM in views / commands). Click recording is best-effort, so a tracking
failure never breaks the redirect.
"""

import hashlib
import hmac
import itertools
import logging
import secrets
from collections.abc import Iterator
from dataclasses import dataclass, field
from urllib.parse import urlparse, urlsplit

from django.conf import settings
from django.core.cache import caches
from django.db import IntegrityError, transaction
from django.db.models import Count, Q
from django.http import HttpRequest

from common.db import ID_IN_CHUNK

from ..constants import (
    CF_CONNECTING_IP_HEADER,
    CF_IPCOUNTRY_HEADER,
    CLICK_DEDUP_CACHE_ALIAS,
    CLICK_DEDUP_WINDOW_SECONDS,
    CODE_ALPHABET,
    CODE_LENGTH,
    COUNTRY_CODE_LENGTH,
    SLUG_PATTERN,
    SLUG_RE,
    UNKNOWN_COUNTRY,
)
from ..models import ClickEvent, ShortLink

logger = logging.getLogger("links")

# 62^6 is astronomically unlikely to loop, but bound the retry so a `code` column
# that somehow filled up fails loudly instead of spinning forever.
_MAX_CODE_TRIES = 8
# Cap the non-PII request context we log per click.
_PROP_MAX = 500


@dataclass(frozen=True)
class ShortLinkStats:
    """Rolled-up click stats for a link. `total` is recorded (deduped) events, NOT
    raw hits: repeat visits from one IP within the dedup window collapse to one row
    (IP-less clicks excepted). `unique` is distinct visitors (distinct non-blank
    ip_hash). `by_country` maps CF-IPCountry code to recorded events.
    """

    total: int
    unique: int
    by_country: dict[str, int] = field(default_factory=dict)


class ShortLinkService:
    """Mint + resolve short links and record / roll up clicks."""

    @staticmethod
    def create(*, url: str, code: str | None = None) -> ShortLink:
        """Create a short link. A random 6-char base62 code by default; pass
        `code` for a custom vanity slug. Raises ValueError on an empty url, a
        malformed custom slug, or a code that is already taken.
        """
        clean_url = (url or "").strip()
        if not clean_url:
            raise ValueError("url is required")
        parsed = urlparse(clean_url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            # Reject at mint, not at click time: a relative or scheme-less url would
            # emit a relative Location (silent misredirect) and a non-http scheme
            # (javascript:, ...) would make HttpResponseRedirect 400 every visitor.
            raise ValueError(f"invalid url {url!r}: must be an absolute http(s) URL")
        if code is None:
            return _insert_with_random_code(clean_url)
        slug = code.strip()
        if not SLUG_RE.match(slug):
            raise ValueError(f"invalid code {code!r}: must match {SLUG_PATTERN}")
        try:
            return _insert(code=slug, url=clean_url)
        except IntegrityError as exc:
            raise ValueError(f"code {slug!r} is already taken") from exc

    @staticmethod
    def find_by_code(code: str) -> ShortLink | None:
        """The redirect lookup: a miss is a normal 404, so return None (not get)."""
        return ShortLink.objects.filter(code=code).first()

    @staticmethod
    def iter_all() -> Iterator[ShortLink]:
        """Every link, newest first, streamed (mirrors the house `iter_*` readers)."""
        return ShortLink.objects.order_by("-id").iterator(chunk_size=100)

    @staticmethod
    def delete(code: str) -> bool:
        """Revoke a short link by code, deleting its ClickEvents in the same
        transaction (no FK, so the cascade is manual). Returns False on a miss so
        ops tooling can report it. This is the only way to pull a link whose
        destination later turns malicious. A best-effort record_click racing this
        can leave an orphan ClickEvent row, which is harmless: stats only roll up
        for live link ids, so an orphan is never read (matches the no-FK house rule).
        """
        link = ShortLink.objects.filter(code=code).first()
        if link is None:
            return False
        with transaction.atomic():
            ClickEvent.objects.filter(short_link_id=link.id).delete()
            link.delete()
        return True

    @staticmethod
    def record_click(*, short_link: ShortLink, request: HttpRequest) -> None:
        """Log one ClickEvent for this hit. Best-effort: any failure is logged
        and swallowed so click tracking never blocks or 500s the redirect.
        """
        try:
            ip = _client_ip(request)
            ip_hash = _hash_ip(ip) if ip else ""
            if ip_hash and _seen_recently(short_link.id, ip_hash):
                return  # same visitor inside the dedup window: don't write another row
            props = {
                "ua": request.META.get("HTTP_USER_AGENT", "")[:_PROP_MAX],
                # Store only the referer's origin (scheme://host), never the path or
                # query: full referer URLs routinely carry tokens / PII, which the
                # app's IP-averse posture explicitly avoids persisting.
                "ref": _referer_origin(request.META.get("HTTP_REFERER", "")),
            }
            # Savepoint so a failed insert rolls back only itself and never poisons
            # an enclosing transaction (parity with _insert; keeps the best-effort promise).
            with transaction.atomic():
                ClickEvent.objects.create(
                    short_link_id=short_link.id,
                    ip_hash=ip_hash,
                    country=_client_country(request),
                    props=props,
                )
        except Exception as exc:  # best-effort boundary: a tracking failure must not disturb the redirect
            logger.warning("record_click failed link=%s: %s", short_link.id, exc)

    @staticmethod
    def stats(short_link: ShortLink) -> ShortLinkStats:
        """Recorded (deduped) events / unique visitors / by-country for one link."""
        return ShortLinkService.stats_for([short_link.id])[short_link.id]

    @staticmethod
    def stats_for(short_link_ids: list[str]) -> dict[str, ShortLinkStats]:
        """Batched stats keyed by short_link_id, so `list_links` doesn't run a
        per-link N+1. Two aggregate queries per id chunk: totals+unique, then
        by-country. Each id appears in one chunk, so results merge cleanly.
        """
        totals: dict[str, tuple[int, int]] = {}
        countries: dict[str, dict[str, int]] = {}
        for chunk in itertools.batched(short_link_ids, ID_IN_CHUNK, strict=False):
            events = ClickEvent.objects.filter(short_link_id__in=chunk)
            # Exclude the "" ip_hash (IP-less click) from the unique count, so an
            # empty hash isn't folded into one phantom unique visitor.
            uniq = Count("ip_hash", distinct=True, filter=~Q(ip_hash=""))
            for row in events.values("short_link_id").annotate(total=Count("id"), uniq=uniq):
                totals[row["short_link_id"]] = (row["total"], row["uniq"])
            for row in events.values("short_link_id", "country").annotate(n=Count("id")).order_by("-n"):
                countries.setdefault(row["short_link_id"], {})[row["country"] or UNKNOWN_COUNTRY] = row["n"]
        return {
            sid: ShortLinkStats(*totals.get(sid, (0, 0)), by_country=countries.get(sid, {})) for sid in short_link_ids
        }


def _insert(*, code: str, url: str) -> ShortLink:
    """Insert one row in its own savepoint, so a caught IntegrityError (a code
    collision) rolls back only this insert and never poisons an enclosing
    transaction (ATOMIC_REQUESTS, a TestCase, ...).
    """
    with transaction.atomic():
        return ShortLink.objects.create(code=code, url=url)


def _insert_with_random_code(url: str) -> ShortLink:
    """Insert with a fresh random base62 code, retrying on the astronomically
    rare unique collision (62^6 space).
    """
    for _ in range(_MAX_CODE_TRIES):
        code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))
        try:
            return _insert(code=code, url=url)
        except IntegrityError:
            continue
    raise ValueError("could not generate a unique code")


def _client_ip(request: HttpRequest) -> str:
    """The visitor IP. CF-Connecting-IP is honored only when SHORTLINK_TRUST_CF_HEADERS
    is on (origin reachable only via the CF tunnel, where the edge overwrites any
    client-supplied value); off-tunnel it's forgeable, so we fall back to REMOTE_ADDR.
    """
    if settings.SHORTLINK_TRUST_CF_HEADERS:
        cf_ip = request.META.get(CF_CONNECTING_IP_HEADER)
        if cf_ip:
            return cf_ip
    return request.META.get("REMOTE_ADDR") or ""


def _client_country(request: HttpRequest) -> str:
    """The 2-letter CF-IPCountry, or "" when we don't trust the header (see _client_ip)."""
    if not settings.SHORTLINK_TRUST_CF_HEADERS:
        return ""
    return request.META.get(CF_IPCOUNTRY_HEADER, "")[:COUNTRY_CODE_LENGTH]


def _referer_origin(referer: str) -> str:
    """The referer reduced to scheme://host[:port], dropping any userinfo, path,
    query, and fragment, so no PII- or credential-bearing referer URL is persisted
    (Referer is fully client-controlled). Returns "" for a blank / relative referer.
    """
    parts = urlsplit(referer)
    host = parts.hostname  # lowercased, userinfo (user:pass@) stripped
    if not parts.scheme or not host:
        return ""
    if ":" in host:  # IPv6 literal: urlsplit strips the brackets, re-add them
        host = f"[{host}]"
    try:
        port = parts.port  # raises ValueError on a malformed/out-of-range port
    except ValueError:
        port = None
    origin = f"{parts.scheme}://{host}:{port}" if port else f"{parts.scheme}://{host}"
    return origin[:_PROP_MAX]


def _seen_recently(short_link_id: str, ip_hash: str) -> bool:
    """True if this (link, visitor) already recorded a click inside the dedup
    window. cache.add returns False when the key already exists, so it caps writes
    at one row per visitor per window: an anti-abuse bound that also dedups bot
    re-fetches. Uses the dedicated CLICK_DEDUP_CACHE_ALIAS so these high-volume
    public writes never cull the default cache's scheduler locks. Fail-open: any
    cache error counts as "not seen", so a flaky cache backend never drops a real
    click. (On the DatabaseCache backend the add is check-then-insert, so a rare
    concurrent double-write is possible; harmless for best-effort analytics.)
    """
    key = f"clickdedup:{short_link_id}:{ip_hash}"
    try:
        return not caches[CLICK_DEDUP_CACHE_ALIAS].add(key, 1, CLICK_DEDUP_WINDOW_SECONDS)
    except Exception:
        return False


def _hash_ip(ip: str) -> str:
    """Salted HMAC-SHA256 of the IP. We store the hash, never the raw IP: distinct
    hashes dedup unique visitors while the small, enumerable IP space stays
    non-recoverable from the digest. The salt is SHORTLINK_IP_HASH_SALT (a separate
    knob from SECRET_KEY, so rotating SECRET_KEY doesn't break historical dedup).
    """
    return hmac.new(settings.SHORTLINK_IP_HASH_SALT.encode("utf-8"), ip.encode("utf-8"), hashlib.sha256).hexdigest()
