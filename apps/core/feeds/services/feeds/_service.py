"""FeedService: account-scoped CRUD on the Feed row + poll-state writes.

Item-log reads / writes (record_items, prune_items, the window queries)
live on `FeedItemService` in a sibling module; the Feed-row concern is
small enough to read top-to-bottom on its own.
"""

import builtins
import logging
from datetime import datetime, timedelta
from functools import cached_property
from typing import TYPE_CHECKING, Any

from django.db import transaction

from feeds.models import Feed, FeedItem
from feeds.policy import PolicyError, enforce_policy
from feeds.registry import load_config, parse_config, validate_config
from openmagpie_schema.feed import SourceInput

from ._global import FeedGlobal

if TYPE_CHECKING:
    from feeds.services.sources import SourceService

logger = logging.getLogger("feeds")


class FeedService:
    """Account-scoped service for Feed reads, writes, and poll-state updates."""

    Global = FeedGlobal

    def __init__(self, *, account_id: str) -> None:
        if not account_id:
            raise ValueError("FeedService requires account_id")
        self.account_id = account_id

    def _assert_scope(self, account_id: str, what: str) -> None:
        if account_id != self.account_id:
            raise ValueError(f"{what} account_id mismatch: {account_id!r} not in scope {self.account_id!r}")

    @cached_property
    def source_svc(self) -> "SourceService":
        # Local import to avoid the feeds -> sources -> feeds.models
        # import cycle at module load.
        from feeds.services.sources import SourceService

        return SourceService(account_id=self.account_id)

    def get(self, id: str) -> Feed:
        """Raises Feed.DoesNotExist if missing (or owned by another account)."""
        return Feed.objects.get(id=id, account_id=self.account_id)

    def find_by_name(self, name: str, /) -> Feed | None:
        """This account's feed with `name`, or None. Names aren't unique;
        returns the oldest match (id order) for a stable result."""
        return Feed.objects.filter(account_id=self.account_id, name=name).order_by("id").first()

    def existing_ids(self, ids: list[str], /) -> set[str]:
        """Of `ids`, the subset that are feeds in THIS account. One query;
        callers diff against the input to find unknown / cross-account ids
        (e.g. validating a watch's feed subscription set)."""
        return set(Feed.objects.filter(account_id=self.account_id, id__in=ids).values_list("id", flat=True))

    def list(self, *, after: str | None = None, limit: int = 50) -> list[Feed]:
        """This account's feeds, newest first (by ULID pk).

        Cursor-paginated: pass `after=<id>` to fetch rows whose id is
        strictly less than that (ULIDs sort by creation, so "less than"
        = "older than"). `limit` caps the page size."""
        qs = Feed.objects.filter(account_id=self.account_id)
        if after:
            qs = qs.filter(id__lt=after)
        return list(qs.order_by("-id")[:limit])

    def build(
        self,
        *,
        user_id: str,
        name: str,
        kind: str,
        poll_interval_seconds: int,
        data: dict[str, Any],
    ) -> Feed:
        """Validate inputs and return an UNSAVED Feed (dry-run preview)."""
        validated = validate_config(kind, data)
        normalized_data = validated.model_dump(mode="json")
        return Feed(
            user_id=user_id,
            account_id=self.account_id,
            kind=kind,
            name=name,
            poll_interval_seconds=poll_interval_seconds,
            data=normalized_data,
        )

    def create(
        self,
        *,
        user_id: str,
        name: str,
        kind: str,
        poll_interval_seconds: int,
        data: dict[str, Any],
        # builtins.list: the class defines a `list` method that shadows the
        # builtin in annotation scope.
        sources: builtins.list[SourceInput] | None = None,
    ) -> Feed:
        """Create a Feed plus, optionally, its starter Source rows in
        one atomic step. `sources` is a list of `SourceInput` (already
        validated upstream by the serializer / CLI parser).

        For a curated feed with sources, the inner `set_sources` call
        runs the same diff-and-bulk_create as the dedicated set verb
        ; starting from zero rows, every source is an add."""
        feed = self.build(
            user_id=user_id,
            name=name,
            kind=kind,
            poll_interval_seconds=poll_interval_seconds,
            data=data,
        )
        with transaction.atomic():
            feed.save()
            if sources:
                self.source_svc.set_sources(feed, sources)
        return feed

    def build_update(
        self,
        feed: Feed,
        /,
        *,
        name: str,
        poll_interval_seconds: int,
        data: dict[str, Any],
    ) -> Feed:
        """Validate an edit, apply to the EXISTING feed (unsaved). `kind`
        is immutable (changing it would swap the config schema)."""
        self._assert_scope(str(feed.account_id), "feed")

        # parse_config = shape only. Policy runs on the MERGE OUTPUT (what
        # persists); merge_preserving handles edit round-trip state.
        submitted = parse_config(str(feed.kind), data)
        prior = load_config(feed)
        try:
            merged = submitted.merge_preserving(prior)
        except ValueError as exc:
            # merge refusal (shouldn't happen for curated feeds w/o secrets,
            # but the contract is shared) -> 400, never a 500.
            raise PolicyError(str(exc)) from exc
        merged = enforce_policy(merged)

        feed.name = name
        feed.poll_interval_seconds = poll_interval_seconds
        feed.data = merged.model_dump(mode="json")
        return feed

    def update(
        self,
        feed: Feed,
        /,
        *,
        name: str,
        poll_interval_seconds: int,
        data: dict[str, Any],
    ) -> Feed:
        feed = self.build_update(feed, name=name, poll_interval_seconds=poll_interval_seconds, data=data)
        feed.save(update_fields=["name", "poll_interval_seconds", "data", "updated_at"])
        return feed

    def delete(self, feed: Feed, /) -> None:
        """Delete a Feed plus its FeedItems and Source rows. Wrapped in
        transaction.atomic() so a failure between the cascades and the
        row delete can't orphan rows. FeedItem.feed_id and Source.feed_id
        are plain CharFields, not FKs (no DB-level cascade) ; the
        service owns the cleanup."""
        self._assert_scope(str(feed.account_id), "feed")
        with transaction.atomic():
            FeedItem.objects.filter(account_id=self.account_id, feed_id=feed.id).delete()
            self.source_svc.delete_for_feed(feed)
            feed.delete()

    def update_poll_state(
        self,
        feed: Feed,
        /,
        *,
        last_polled_at: datetime,
        data: dict | None,
    ) -> None:
        """Persist the poll cadence + (optionally) the config blob.

        `data=None` means "don't touch `feed.data`"; used on full-outage
        cycles where no source ran, so there are no advanced watermarks to
        persist and writing the pre-cycle snapshot back would just clobber
        any concurrent operator edit. We still advance last_polled_at /
        next_poll_at so the scheduler respects the operator's cadence
        instead of tight-looping the outage.

        Detect-and-log the poller-vs-editor race on `feed.data`: if the
        row was updated by a PUT between the start of this poll cycle and
        this save, the operator's edit is about to be reverted (we're
        writing the pre-edit shape + advanced watermarks). One single-row
        SELECT per poll cycle; observable without changing behavior, so
        we can decide whether to add a poll_lock to the PUT path later.
        """
        self._assert_scope(str(feed.account_id), "feed")
        if data is not None:
            try:
                db_updated_at = Feed.objects.only("updated_at").get(id=feed.id).updated_at
            except Feed.DoesNotExist:
                db_updated_at = feed.updated_at
            if db_updated_at and feed.updated_at and db_updated_at > feed.updated_at:
                logger.warning(
                    "feed=%s was updated mid-poll (db=%s, snapshot=%s); operator's edit may be "
                    "reverted by this save; investigate adding poll_lock to FeedService.update "
                    "if this fires in practice",
                    feed.id,
                    db_updated_at.isoformat(),
                    feed.updated_at.isoformat(),
                )
        feed.last_polled_at = last_polled_at
        feed.next_poll_at = last_polled_at + timedelta(seconds=int(feed.poll_interval_seconds))
        update_fields = ["last_polled_at", "next_poll_at", "updated_at"]
        if data is not None:
            feed.data = data
            update_fields.insert(2, "data")
        feed.save(update_fields=update_fields)
