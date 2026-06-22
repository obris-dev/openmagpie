"""SourceService: account-scoped CRUD on Source rows for a Feed.

Mirrors `FeedService`. Operates only on curated feeds (the only feed
kind today); the kind check is at the boundary so a future kind can
opt out cleanly.

The mutation surface is intentionally narrow:

  set_sources(feed, items, dry_run) replace-mode reconcile;
      additive + drops missing + preserves watermarks on persisted
  remove(feed, source_id)            by row id; idempotent

There is no single-row `add` ; the create-time path (FeedService.create
with starter sources) and `set_sources` already cover what an add
would do, and a bespoke "one source" mutation surface invites
flag-per-kind UX that scales badly.

Uniqueness is enforced by `(account_id, feed_id, spec_hash)` where
`spec_hash` is the sha256 of the canonical spec dump. Operators
never see the hash ; it's pure dedup plumbing.
"""

import builtins
import hashlib
import json
import logging
from collections.abc import Iterator
from datetime import datetime

import ulid
from django.db import transaction
from django.db.models import Count

from common.locks import feed_set_lock
from feeds.models import Feed, Source
from feeds.policy import PolicyError, default_and_enforce_source_watermark, enforce_source_spec_safety
from openmagpie_schema.configs import SourceSpec
from openmagpie_schema.feed import SourceInput, SourceSetResult
from sources import registry as source_registry


class ConcurrentSetSourcesError(Exception):
    """Another `set_sources` is already running on the same feed.
    Raised by `SourceService.set_sources` when the per-feed try-lock
    fails. View layer maps to 409; the operator retries."""


logger = logging.getLogger("feeds")


def _assert_connector_registered(specs: list[SourceSpec]) -> None:
    """Reject input specs whose connector kind isn't loaded in this
    deployment. The poll path is defensive about this (`KeyError` is
    in `_RECOVERABLE_ERRORS` so an unknown kind logs and skips one
    source instead of aborting the cycle), but a write-time check
    means an operator sees a clean 400 with the offending kind named
    instead of a row that silently never polls. Mirrors how feeds
    `validate_config` runs at the boundary."""
    missing: set[str] = set()
    for spec in specs:
        try:
            source_registry.get(spec.kind)
        except KeyError:
            missing.add(spec.kind)
    if missing:
        raise PolicyError(
            "no connector registered for source kind(s): "
            + ", ".join(sorted(missing))
            + " (this deployment is missing the connector; remove the offending sources or upgrade the server)"
        )


def _hash_spec(spec: SourceSpec) -> str:
    """sha256 of the canonical spec dump.

    Pydantic v2's `model_dump_json` follows field-declaration order,
    NOT alphabetical, so a future reorder / alias / populate_by_name
    on a SourceSpec subclass would silently change every hash and
    break dedup on existing rows. Canonicalize by routing through
    `json.dumps(model_dump(mode="json"), sort_keys=True)`: the hash
    depends on field names + values only, never on their order in
    the class body. Pinned by a regression test in
    `feeds/tests.py::SpecHashCanonicalTests`."""
    return hashlib.sha256(
        json.dumps(spec.model_dump(mode="json"), sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


class SourceGlobal:
    """Static methods only. Span all accounts. Scheduler / telemetry only."""

    @staticmethod
    def count_by_kind() -> dict[str, int]:
        """{source kind: count} across all accounts (telemetry gauge)."""
        rows = Source.objects.values("kind").annotate(n=Count("id"))
        return {row["kind"]: row["n"] for row in rows}


class SourceService:
    """Account-scoped service for Source CRUD bound to a Feed."""

    Global = SourceGlobal

    def __init__(self, *, account_id: str) -> None:
        if not account_id:
            raise ValueError("SourceService requires account_id")
        self.account_id = account_id

    def _assert_scope(self, account_id: str, what: str) -> None:
        if account_id != self.account_id:
            raise ValueError(f"{what} account_id mismatch: {account_id!r} not in scope {self.account_id!r}")

    def _assert_curated(self, feed: Feed) -> None:
        self._assert_scope(str(feed.account_id), "feed")
        if feed.kind != "curated":
            raise PolicyError(f"sources are only supported on curated feeds (got kind={feed.kind!r})")

    def _scoped(self, feed: Feed):
        return Source.objects.filter(account_id=self.account_id, feed_id=str(feed.id))

    def list(self, feed: Feed) -> list[Source]:
        self._assert_scope(str(feed.account_id), "feed")
        return list(self._scoped(feed).order_by("id"))

    def count(self, feed: Feed) -> int:
        self._assert_scope(str(feed.account_id), "feed")
        return self._scoped(feed).count()

    def iter_for_poll(self, feed: Feed) -> Iterator[Source]:
        """Stream this feed's sources in RANDOM order for one poll cycle.

        Random (`ORDER BY RANDOM()`), not for aesthetics: it
        decorrelates which sources fail from their POSITION in the
        cycle. Many sources sit behind shared infra (one CMS / CDN)
        that rate-limits our egress IP under a burst ; with a FIXED
        order the same unlucky sources past that threshold fail every
        poll and never record. Reshuffling per cycle moves the danger
        zone each time, so over a few polls every source gets a clean
        run. `.iterator()` streams instead of materializing the full
        row set (a feed can carry 1000+ sources)."""
        self._assert_scope(str(feed.account_id), "feed")
        return self._scoped(feed).order_by("?").iterator()

    def advance_watermark(self, source: Source, value: datetime) -> int:
        """Move `last_event_at` strictly forward on one Source row.

        The `last_event_at__lt=value` guard makes the UPDATE monotonic
        at the DB so a stale poll (out-of-order completion under
        concurrent pollers, or any future caller outside `poll_lock`)
        can't clobber a newer watermark. Equivalent guard for an
        operator-initiated backfill: it's a different code path
        (`rewind_watermark`, future) and would bypass this method.

        Scoped by `(account_id, id)` so a stale Source pointer can't
        update across tenants. Returns the row count affected
        (0 = no-op: already past `value`, or not in scope). Pure
        column UPDATE, no JSONB rewrite."""
        return Source.objects.filter(
            account_id=self.account_id,
            id=source.id,
            last_event_at__lt=value,
        ).update(last_event_at=value)

    def delete_for_feed(self, feed: Feed) -> int:
        """Drop every Source row attached to a feed; used by
        FeedService.delete to cascade. Idempotent."""
        self._assert_scope(str(feed.account_id), "feed")
        deleted, _ = self._scoped(feed).delete()
        return deleted

    def get_by_id(self, source_id: str, /) -> Source:
        """One source by its OWN id (account-scoped, no feed needed). Raises
        `Source.DoesNotExist` on miss / another account's. Backs the by-own-id
        detail route `/v1/feed-sources/<id>` ; the feed it belongs to is read off
        the returned row, not supplied by the caller."""
        return Source.objects.get(id=source_id, account_id=self.account_id)

    def remove_by_id(self, source_id: str, /) -> int:
        """Delete one source by its OWN id (account-scoped). Resolves the source,
        verifies its owning feed is curated, then deletes. Raises
        `Source.DoesNotExist` if absent / another account's (HTTP -> 404) and
        `PolicyError` if the feed isn't curated (HTTP -> 400), the same guard the
        feed-scoped set path runs, checked here rather than assumed. Returns the
        delete count (1 ; 0 only if a concurrent delete won the race). Backs
        `DELETE /v1/feed-sources/<id>`."""
        source = self.get_by_id(source_id)
        try:
            feed = Feed.objects.get(id=source.feed_id, account_id=self.account_id)
        except Feed.DoesNotExist as exc:
            # Race: the feed (and its sources, via FeedService.delete's atomic
            # cleanup) was deleted between resolving the source and here. The
            # source is gone too, so 404 is the truthful answer.
            raise Source.DoesNotExist from exc
        self._assert_curated(feed)
        deleted, _ = source.delete()
        return deleted

    def set_sources(
        self,
        feed: Feed,
        items: builtins.list[SourceInput],
        *,
        dry_run: bool = False,
    ) -> SourceSetResult:
        """Replace the feed's sources with `items`. Additive + drops
        missing + preserves watermarks on rows that persist.

        Dedup by spec_hash on the input list so a script that emitted
        the same spec twice doesn't trip the unique constraint. First
        occurrence wins for meta/field_map values; subsequent
        duplicates are logged at WARNING so the operator can spot a
        scrape script that's silently dropping data."""
        self._assert_curated(feed)
        specs = [item.spec for item in items]
        _assert_connector_registered(specs)
        enforce_source_spec_safety(specs)

        new_by_hash: dict[str, SourceInput] = {}
        for item in items:
            h = _hash_spec(item.spec)
            if h in new_by_hash:
                # Silent first-wins would mask scraper bugs (e.g. two
                # entries of the same URL with different `meta` tags).
                # Surface it.
                logger.warning(
                    "set_sources: duplicate input spec %r on feed %s ; keeping first occurrence, dropping later (meta=%r, field_map=%r)",
                    item.spec.display(),
                    feed.id,
                    item.meta,
                    item.field_map,
                )
                continue
            new_by_hash[h] = item
        new_hashes = set(new_by_hash)

        # Dry-run path is read-only and unauthoritative ; intentionally
        # skips the lock and reports a snapshot diff. A real run must
        # take the snapshot UNDER the lock (otherwise two concurrent
        # operators each compute their diff against the pre-A state,
        # and B's apply hits IntegrityError on rows A inserted, or
        # silently deletes rows A added).
        if dry_run:
            existing_hashes = set(self._scoped(feed).values_list("spec_hash", flat=True))
            return SourceSetResult(
                added=len(new_hashes - existing_hashes),
                removed=len(existing_hashes - new_hashes),
                persisted=len(new_hashes & existing_hashes),
                source_count=len(new_hashes),
            )

        # Serialize concurrent set_sources on the same feed and take
        # the snapshot inside the lock so the diff is authoritative.
        # Loser of the race gets a retry-friendly 409.
        with feed_set_lock(str(feed.id)) as acquired:
            if not acquired:
                raise ConcurrentSetSourcesError(
                    f"another set-sources is in progress for feed {feed.id}; retry in a moment"
                )
            with transaction.atomic():
                existing: dict[str, tuple[dict, dict]] = {
                    row["spec_hash"]: (row["meta"] or {}, row["field_map"] or {})
                    for row in self._scoped(feed).values("spec_hash", "meta", "field_map")
                }
                existing_hashes = set(existing)

                added_hashes = new_hashes - existing_hashes
                removed_hashes = existing_hashes - new_hashes
                persisted_hashes = existing_hashes & new_hashes

                if removed_hashes:
                    self._scoped(feed).filter(spec_hash__in=removed_hashes).delete()
                if added_hashes:
                    # `ignore_conflicts=True` is a belt against TTL
                    # expiry: the lock auto-releases after
                    # `FEED_SET_LOCK_TIMEOUT_SECONDS` (60s), so a
                    # pathological bulk apply over the timeout could
                    # let a second writer in mid-flight and the two
                    # inserts could collide on `(account_id, feed_id,
                    # spec_hash)`. Under the lock + post-lock snapshot
                    # the row sets are normally disjoint by
                    # construction, so the ignore is a no-op on the
                    # happy path ; it only matters when the TTL gave
                    # up before we did.
                    Source.objects.bulk_create(
                        [
                            Source(
                                id=str(ulid.ulid()),
                                account_id=self.account_id,
                                feed_id=str(feed.id),
                                kind=new_by_hash[h].spec.kind,
                                spec=new_by_hash[h].spec.model_dump(mode="json"),
                                spec_hash=h,
                                last_event_at=default_and_enforce_source_watermark(new_by_hash[h].last_event_at),
                                meta=new_by_hash[h].meta,
                                field_map=new_by_hash[h].field_map,
                            )
                            for h in added_hashes
                        ],
                        ignore_conflicts=True,
                    )
                # Persisted rows: refresh meta + field_map ONLY where
                # the input actually changed them. Skipping unchanged
                # rows turns a 1000-source no-op re-import from N
                # UPDATEs into zero. Watermarks (last_event_at) are
                # never touched here.
                dirty_hashes = [
                    h for h in persisted_hashes if existing[h] != (new_by_hash[h].meta, new_by_hash[h].field_map)
                ]
                for h in dirty_hashes:
                    desired = new_by_hash[h]
                    self._scoped(feed).filter(spec_hash=h).update(
                        meta=desired.meta,
                        field_map=desired.field_map,
                    )

                return SourceSetResult(
                    added=len(added_hashes),
                    removed=len(removed_hashes),
                    persisted=len(persisted_hashes),
                    source_count=len(new_hashes),
                )
