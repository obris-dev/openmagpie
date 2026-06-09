"""WatchService: account-scoped CRUD on the Watch aggregate.

A Watch is an aggregate: the Watch row + its WatchFeed subscriptions +
one WatchPath + that path's ordered WatchAction chain. This service owns
the whole-aggregate writes (create / update / delete) ; per-action chain
mutation (the sub-router) lives on `WatchActionService`.

Mirrors `FeedService`: account-scoped, `Global` for cross-tenant statics,
cursor-paginated list, build/create transaction.
"""

import builtins
from functools import cached_property

from django.db import transaction

from openmagpie_schema.watch import WatchActionInput
from watches.models import Watch, WatchAction, WatchActionDigestWindow, WatchActionRun, WatchFeed, WatchPath

from ._actions import WatchActionService
from ._global import WatchGlobal


class WatchService:
    """Account-scoped service for Watch aggregate reads + writes."""

    Global = WatchGlobal

    def __init__(self, *, account_id: str) -> None:
        if not account_id:
            raise ValueError("WatchService requires account_id")
        self.account_id = account_id

    def _assert_scope(self, account_id: str, what: str) -> None:
        if account_id != self.account_id:
            raise ValueError(f"{what} account_id mismatch: {account_id!r} not in scope {self.account_id!r}")

    @cached_property
    def action_svc(self) -> WatchActionService:
        return WatchActionService(account_id=self.account_id)

    # ── Reads ──────────────────────────────────────────────────────────

    def get(self, id: str) -> Watch:
        """Raises Watch.DoesNotExist if missing (or owned by another account)."""
        return Watch.objects.get(id=id, account_id=self.account_id)

    def find_by_name(self, name: str, /) -> Watch | None:
        """This account's watch with `name`, or None. Names aren't unique;
        returns the oldest match (id order) for a stable result."""
        return Watch.objects.filter(account_id=self.account_id, name=name).order_by("id").first()

    def list(self, *, after: str | None = None, limit: int = 50) -> builtins.list[Watch]:
        """This account's watches, newest first (by ULID pk). Cursor-
        paginated: `after=<id>` fetches rows older than that id."""
        qs = Watch.objects.filter(account_id=self.account_id)
        if after:
            qs = qs.filter(id__lt=after)
        return builtins.list(qs.order_by("-id")[:limit])

    def feed_ids(self, watch: Watch, /) -> builtins.list[str]:
        """The watch's subscribed feed ids (WatchFeed rows), creation
        order. The wire shape's `feed_ids`."""
        self._assert_scope(str(watch.account_id), "watch")
        return builtins.list(
            WatchFeed.objects.filter(account_id=self.account_id, watch_id=watch.id)
            .order_by("id")
            .values_list("feed_id", flat=True)
        )

    def watch_feeds(self, watch: Watch, /) -> builtins.list[WatchFeed]:
        """The watch's WatchFeed rows (subscription + per-feed watermark),
        creation order. The trigger pass reads `last_item_id` off each to
        scan only new FeedItems, then advances it via `advance_watermark`."""
        self._assert_scope(str(watch.account_id), "watch")
        return builtins.list(WatchFeed.objects.filter(account_id=self.account_id, watch_id=watch.id).order_by("id"))

    def advance_watermark(self, watch_feed: WatchFeed, /, *, last_item_id: str) -> None:
        """Advance one (watch, feed) cursor to the newest item it has
        triggered on. Single-column UPDATE ; called by the trigger pass
        after enqueuing runs for the items in `(prev, last_item_id]`."""
        self._assert_scope(str(watch_feed.account_id), "watch feed")
        watch_feed.last_item_id = last_item_id
        watch_feed.save(update_fields=["last_item_id", "updated_at"])

    def initial_actions(self, watch: Watch, /) -> builtins.list[WatchAction]:
        """The watch's initial-path actions, ordered by rank (the chain).

        A committed watch ALWAYS has an initial path (create makes it in
        the same transaction as the Watch row), so a blank
        `initial_path_id` is corruption, not a normal empty state ; raise
        loudly so a create-path bug surfaces instead of masquerading as a
        watch with no actions. (An actionless watch returns [] from
        `list_for_path` finding no rows, which is the legit empty case.)"""
        self._assert_scope(str(watch.account_id), "watch")
        if not watch.initial_path_id:
            raise ValueError(f"watch {watch.id} has no initial_path_id (corrupt; every watch gets one at create)")
        return self.action_svc.list_for_path(watch.initial_path_id)

    # ── Writes ─────────────────────────────────────────────────────────

    def build(self, *, name: str, is_active: bool) -> Watch:
        """An UNSAVED Watch (dry-run preview). Feed/path/action validation
        happens in the serializer + action registry before this; the
        preview row carries no id."""
        return Watch(account_id=self.account_id, name=name, is_active=is_active)

    def create(
        self,
        *,
        user_id: str,
        name: str,
        is_active: bool = True,
        feed_ids: builtins.list[str],
        actions: builtins.list[WatchActionInput],
    ) -> Watch:
        """Create the whole aggregate: the Watch row, its single WatchPath
        (pointed at by initial_path_id), one WatchFeed per feed, and the
        path's ordered WatchAction chain (dense rank 0..N-1).

        The row + path + feeds save in one transaction ; the chain is then
        written by `replace_chain`, which owns its own lock + transaction
        (the lock is chain state, see WatchActionService.replace_chain).
        The new path has no concurrent traffic yet, so its lock is
        uncontended here. `actions` are already shape+policy validated
        (serializer); list order is the dense rank."""
        with transaction.atomic():
            watch = Watch(account_id=self.account_id, user_id=user_id, name=name, is_active=is_active)
            watch.save()

            path = WatchPath(account_id=self.account_id, watch_id=watch.id)
            path.save()
            watch.initial_path_id = str(path.id)
            watch.save(update_fields=["initial_path_id", "updated_at"])

            WatchFeed.objects.bulk_create(
                [
                    WatchFeed(account_id=self.account_id, watch_id=watch.id, feed_id=fid)
                    for fid in _dedupe_preserving_order(feed_ids)
                ]
            )

        self.action_svc.replace_chain(path_id=str(path.id), actions=actions)
        return watch

    def update(
        self,
        watch: Watch,
        /,
        *,
        name: str,
        is_active: bool,
        feed_ids: builtins.list[str],
        actions: builtins.list[WatchActionInput],
    ) -> Watch:
        """Full-replace edit of the aggregate: watch scalars, the feed
        subscription set, and the action chain. `actions` are pre-validated.

        Two scopes, not one: the scalar + feed writes are one transaction;
        the chain replace is a SEPARATE lock+transaction owned by
        `replace_chain` (the chain lock serializes against concurrent
        `watch action add/remove`; nesting it inside this transaction
        would release the lock before commit). A failure in the chain
        replace after the scalars commit leaves a consistent, runnable
        watch (new name, old chain) ; acceptable for an edit. Raises
        `ConcurrentChainError` if a chain edit is already in progress."""
        self._assert_scope(str(watch.account_id), "watch")
        if not watch.initial_path_id:
            raise ValueError(f"watch {watch.id} has no initial_path_id (corrupt; every watch gets one at create)")
        path_id = watch.initial_path_id
        with transaction.atomic():
            watch.name = name
            watch.is_active = is_active
            watch.save(update_fields=["name", "is_active", "updated_at"])
            self._set_feeds(watch, feed_ids)

        self.action_svc.replace_chain(path_id=path_id, actions=actions)
        return watch

    def delete(self, watch: Watch, /) -> None:
        """Delete a Watch and everything under it: WatchFeed subscriptions,
        the WatchPath(s), their WatchActions (and any digest-window rows
        keyed on those actions), and the WatchActionRun audit rows. Plain
        CharField pointers (no FK cascade), so the service owns the cleanup ;
        atomic so a mid-cascade failure can't orphan."""
        self._assert_scope(str(watch.account_id), "watch")
        with transaction.atomic():
            WatchActionRun.objects.filter(account_id=self.account_id, watch_id=watch.id).delete()
            path_ids = builtins.list(
                WatchPath.objects.filter(account_id=self.account_id, watch_id=watch.id).values_list("id", flat=True)
            )
            if path_ids:
                action_ids = builtins.list(
                    WatchAction.objects.filter(account_id=self.account_id, path_id__in=path_ids).values_list(
                        "id", flat=True
                    )
                )
                if action_ids:
                    # Digest windows are keyed on action_id (not watch_id), so
                    # they don't fall out of the watch/path deletes ; clear them
                    # explicitly or a deleted watch leaves orphan window rows.
                    WatchActionDigestWindow.objects.filter(
                        account_id=self.account_id, action_id__in=action_ids
                    ).delete()
                WatchAction.objects.filter(account_id=self.account_id, path_id__in=path_ids).delete()
            WatchPath.objects.filter(account_id=self.account_id, watch_id=watch.id).delete()
            WatchFeed.objects.filter(account_id=self.account_id, watch_id=watch.id).delete()
            watch.delete()

    # ── internals ──────────────────────────────────────────────────────

    def _set_feeds(self, watch: Watch, feed_ids: builtins.list[str]) -> None:
        """Replace the watch's WatchFeed set with `feed_ids`, preserving
        the per-feed watermark on rows that survive (so an edit that keeps
        a feed doesn't reset its progress)."""
        desired = _dedupe_preserving_order(feed_ids)
        existing = {wf.feed_id: wf for wf in WatchFeed.objects.filter(account_id=self.account_id, watch_id=watch.id)}
        desired_set = set(desired)
        to_remove = [wf.id for fid, wf in existing.items() if fid not in desired_set]
        if to_remove:
            WatchFeed.objects.filter(id__in=to_remove).delete()
        to_add = [
            WatchFeed(account_id=self.account_id, watch_id=watch.id, feed_id=fid)
            for fid in desired
            if fid not in existing
        ]
        if to_add:
            WatchFeed.objects.bulk_create(to_add)


def _dedupe_preserving_order(feed_ids: builtins.list[str]) -> builtins.list[str]:
    """Drop duplicate feed ids, keep first-seen order (subscribing to the
    same feed twice is a no-op dup, not an error). `dict.fromkeys` (not
    `set`) so the operator's subscription order survives the round-trip ;
    blank ids are already rejected at the serializer, so no filter here."""
    return builtins.list(dict.fromkeys(feed_ids))
