"""WatchActionService: account-scoped CRUD on the action chain of a path.

Owns everything about a path's `WatchAction` rows: the ordered chain
read, whole-chain replace (used by watch create/update), and the
per-action sub-router mutations (add at a rank, remove + renumber).

Rank is dense (0..N-1, contiguous) within a path, unique `(account_id,
path_id, rank)`. Concurrent chain mutations on one path are serialized by
`path_chain_lock(path_id)` (cache-backed, the same primitive as
`feed_set_lock`) ; the path is the right grain since rank uniqueness is
per-path, and locking the path (not its rows) also covers the
add-first-action race a row lock would miss. The loser of the race gets
`ConcurrentChainError` -> 409. At ~2-10 actions/path the renumber is free.
"""

import builtins

from django.db import transaction
from django.db.models import Count
from django.utils import timezone
from pydantic import ValidationError

from common.locks import path_chain_lock
from openmagpie_schema.watch import WatchActionInput
from openmagpie_schema.watch_actions import DeliveryConfigBase
from watches.models import WatchAction, WatchActionDigestWindow
from watches.policy import PolicyError
from watches.registry import KNOWN_KINDS, load_config, merge_config, parse_config, validate_config


class ConcurrentChainError(RuntimeError):
    """Another chain mutation holds `path_chain_lock` for this path. The
    caller maps this to a 409 ; the operator retries."""


class WatchActionGlobal:
    """Static methods only. Span all accounts. Telemetry only."""

    @staticmethod
    def count_by_kind() -> dict[str, int]:
        """{action kind: count} across all accounts (telemetry gauge)."""
        rows = WatchAction.objects.values("kind").annotate(n=Count("id"))
        return {row["kind"]: row["n"] for row in rows}


class WatchActionService:
    """Account-scoped service for a path's WatchAction chain."""

    Global = WatchActionGlobal

    def __init__(self, *, account_id: str) -> None:
        if not account_id:
            raise ValueError("WatchActionService requires account_id")
        self.account_id = account_id

    # ── Reads ──────────────────────────────────────────────────────────

    def list_for_path(self, path_id: str, /) -> builtins.list[WatchAction]:
        """The path's actions, dense rank order (the chain)."""
        return builtins.list(WatchAction.objects.filter(account_id=self.account_id, path_id=path_id).order_by("rank"))

    def count_for_path(self, path_id: str, /) -> int:
        """How many actions are on the path's chain - a COUNT, no row fetch (the
        dry-run add only needs the length to place the would-be rank)."""
        return WatchAction.objects.filter(account_id=self.account_id, path_id=path_id).count()

    def get(self, action_id: str, /) -> WatchAction:
        """Raises WatchAction.DoesNotExist if missing / other-account."""
        return WatchAction.objects.get(id=action_id, account_id=self.account_id)

    def next_in_chain(self, action: WatchAction, /) -> WatchAction | None:
        """The next action down the same path (smallest rank STRICTLY GREATER), or
        None at the chain tail. The drain calls this to advance after a SUCCEEDED
        run."""
        return self._adjacent(action, forward=True)

    def prev_in_chain(self, action: WatchAction, /) -> WatchAction | None:
        """The previous action up the same path (largest rank STRICTLY LESS), or None
        at the chain head. The backfill resolves it to find the step whose SUCCEEDED
        passes seed a re-run of `action`."""
        return self._adjacent(action, forward=False)

    def _adjacent(self, action: WatchAction, /, *, forward: bool) -> WatchAction | None:
        """The chain neighbor of `action` on its path: the next-greater rank
        (`forward`) or the next-smaller. Strictly-greater/less (not `rank +/- 1`) so a
        sparse-rank chain (the planned gap/rebalance) traverses correctly, not
        dead-ends on a gap; rides `(account, path, rank)` as a range seek."""
        rank_filter = {"rank__gt": action.rank} if forward else {"rank__lt": action.rank}
        return (
            WatchAction.objects.filter(account_id=self.account_id, path_id=action.path_id, **rank_filter)
            .order_by("rank" if forward else "-rank")
            .first()
        )

    # ── Writes ─────────────────────────────────────────────────────────

    def replace_chain(self, *, path_id: str, actions: builtins.list[WatchActionInput]) -> builtins.list[WatchAction]:
        """Reconcile a path's chain to `actions` (list order = rank 0..N-1).
        Used by watch create/update ; an empty `actions` clears the chain.
        Each input's `config` is re-validated (shape + policy) so the
        persisted blob is normalized ; `kind` is the spec's discriminator.

        UPSERT BY ID, not delete-and-recreate. Each spec carries an optional
        `id`: a spec WITH a known id updates that existing row IN PLACE, so
        the action's id and its `WatchActionRun` audit history survive an
        edit, and its masked secret restores from THAT same row (matched by
        id, never by list position). A spec with no id is a brand-new action
        (server mints the id). An existing row absent from `actions` is
        deleted. Ranks are renumbered densely by submitted order, so a
        reorder just rewrites `rank` ; nothing is destroyed.

        Identity-by-id is what makes a reorder safe: there is no
        position-pairing to mis-restore a secret across endpoints (the old
        index heuristic's auth-token-leak edge is gone by construction). The
        only masked-secret refusal left is the one that's genuinely
        unrestorable: a masked secret with no SAME-KIND prior to restore
        from (a brand-new action, or one whose kind changed).

        Takes `path_chain_lock` (like `add`/`remove`) so concurrent chain
        mutators on the path serialize ; the snapshot of existing rows is
        read INSIDE the lock. Self-locking because the lock is chain state,
        not watch state. Raises `ConcurrentChainError` on contention, or
        `PolicyError` (-> 400) on an unknown id / unrestorable masked secret."""
        with path_chain_lock(path_id) as acquired:
            if not acquired:
                raise ConcurrentChainError(f"another chain edit is in progress on path {path_id}; retry")
            existing = {str(a.id): a for a in self.list_for_path(path_id)}
            ordered: builtins.list[WatchAction] = []  # final chain order (updated + new)
            updated: builtins.list[WatchAction] = []
            created: builtins.list[WatchAction] = []
            kept_ids: set[str] = set()
            windows_to_clear: builtins.list[str] = []  # edited away from digest
            temp_rank = len(existing) + 1  # park new rows past any existing rank
            for spec in actions:
                sid = (spec.id or "").strip()
                prior_row = self._resolve_prior(sid, existing, kept_ids)
                # A same-kind prior is the only thing a masked secret can
                # restore from. Without one (new action, or kind changed),
                # a masked secret is unrestorable -> refuse, don't persist ***.
                same_kind_prior = prior_row if prior_row is not None and str(prior_row.kind) == spec.kind else None
                if same_kind_prior is None and self._submitted_has_masked_secret(spec):
                    raise PolicyError(
                        f"{spec.kind!r} action has a masked secret (***) but no matching prior to restore it "
                        f"from (a new action, or a changed kind); provide the real value"
                    )
                merged = merge_config(
                    spec.kind,
                    spec.config.model_dump(mode="json"),
                    load_config(same_kind_prior) if same_kind_prior else None,
                )
                blob = merged.model_dump(mode="json")
                is_digest = isinstance(merged, DeliveryConfigBase) and merged.is_digest()
                if prior_row is not None:
                    kept_ids.add(str(prior_row.id))
                    prior_row.kind = spec.kind
                    prior_row.config = blob
                    updated.append(prior_row)
                    ordered.append(prior_row)
                    # An existing row edited away from digest must lose its
                    # window or its now-instant runs strand (see
                    # _clear_digest_windows). New rows have no window yet.
                    if not is_digest:
                        windows_to_clear.append(str(prior_row.id))
                else:
                    row = WatchAction(
                        account_id=self.account_id, path_id=path_id, kind=spec.kind, config=blob, rank=temp_rank
                    )
                    temp_rank += 1
                    created.append(row)
                    ordered.append(row)
            removed_ids = [sid for sid in existing if sid not in kept_ids]
            # Refuse a full-replace that would delete an action the client was never
            # shown. watch_view omits a row whose stored kind isn't a known kind
            # (unrenderable: a removed kind / manual corruption), so an edit seeded
            # from that censored detail lacks its id, and this delete would silently
            # drop it AND its run history. The client can't intend to remove what it
            # can't see, so reject rather than lose it (the server has the true set).
            hidden = [sid for sid in removed_ids if str(existing[sid].kind) not in KNOWN_KINDS]
            if hidden:
                raise PolicyError(
                    f"actions {hidden} have an unreadable kind and aren't shown to the client; "
                    "resolve them (migrate or remove) before replacing this watch's action chain"
                )
            with transaction.atomic():
                if removed_ids:
                    WatchAction.objects.filter(account_id=self.account_id, id__in=removed_ids).delete()
                # Removed actions + edits leaving digest both shed their windows.
                self._clear_digest_windows(removed_ids + windows_to_clear)
                if updated:
                    WatchAction.objects.bulk_update(updated, ["kind", "config"])  # updated_at set by _renumber
                if created:
                    WatchAction.objects.bulk_create(created)
                if ordered:
                    self._renumber(ordered)
        return self.list_for_path(path_id)

    def _resolve_prior(self, sid: str, existing: dict[str, WatchAction], kept_ids: set[str]) -> WatchAction | None:
        """The existing row a submitted `id` refers to, or None for a new
        action (empty id). Raises PolicyError on an id that isn't on this
        path (stale / cross-path) or one submitted twice."""
        if not sid:
            return None
        if sid not in existing:
            raise PolicyError(f"action {sid!r} is not on this watch; omit `id` to add a new action")
        if sid in kept_ids:
            raise PolicyError(f"action {sid!r} appears more than once in the chain")
        return existing[sid]

    def _clear_digest_windows(self, action_ids: builtins.list[str]) -> None:
        """Delete the digest-window rows for these actions. MUST be called
        whenever an action is removed or its config moves AWAY from digest:
        claim_due excludes a run purely by its action HAVING a window row
        (ignoring close_at), so a lingering row strands the action's
        now-instant runs PENDING forever (the flush stops surfacing them once
        a close clears close_at). Idempotent — a no-op when no row exists
        (instant actions never have one). Mirrors WatchService.delete's
        window cleanup. Call inside the mutator's transaction."""
        if action_ids:
            WatchActionDigestWindow.objects.filter(account_id=self.account_id, action_id__in=action_ids).delete()

    def _submitted_has_masked_secret(self, spec: WatchActionInput) -> bool:
        """Whether a submitted action arrives with a still-masked secret.
        Parses shape-only (no policy). An unknown kind (KeyError) or invalid
        shape (ValidationError) returns False so the REAL error surfaces from
        `merge_config` right after, not as a confusing guard failure ; any
        other exception is a genuine bug and propagates."""
        try:
            return parse_config(spec.kind, spec.config.model_dump(mode="json")).has_masked_secret()
        except (KeyError, ValidationError):
            return False

    def add(
        self, *, path_id: str, action: WatchActionInput, rank: int | None = None, dry_run: bool = False
    ) -> WatchAction:
        """Insert one action into the chain. `rank=None` appends ; an
        explicit rank inserts there, shifting later actions up. Renumbers
        to keep the chain dense. Returns the created row. Raises
        `ConcurrentChainError` if another chain mutation holds the path lock.
        When `dry_run`, validate + build the would-be row in memory (at its
        would-be rank) and return it WITHOUT a lock, save, or renumber -
        nothing is persisted."""
        config = validate_config(action.kind, action.config.model_dump(mode="json"))
        if dry_run:
            # Build the would-be row in the service (no lock/save) so a
            # single-action preview reuses the watch_action_mutation serializer
            # for redaction + summary. The whole-watch dry-run reaches the same
            # serialization via watch_action_input_wire (serializer layer); both
            # share watch_action_wire, so neither path duplicates the preview.
            chain_len = self.count_for_path(path_id)
            insert_at = chain_len if rank is None else max(0, min(rank, chain_len))
            return WatchAction(
                account_id=self.account_id,
                path_id=path_id,
                kind=action.kind,
                config=config.model_dump(mode="json"),
                rank=insert_at,
            )
        with path_chain_lock(path_id) as acquired:
            if not acquired:
                raise ConcurrentChainError(f"another chain edit is in progress on path {path_id}; retry")
            with transaction.atomic():
                chain = self.list_for_path(path_id)
                insert_at = len(chain) if rank is None else max(0, min(rank, len(chain)))
                # Save the new row at a temporary rank past the end so it
                # can't collide with an existing rank; _renumber then
                # assigns every row its final dense rank.
                created = WatchAction.objects.create(
                    account_id=self.account_id,
                    path_id=path_id,
                    kind=action.kind,
                    config=config.model_dump(mode="json"),
                    rank=len(chain) + 1,
                )
                chain.insert(insert_at, created)
                self._renumber(chain)
        return created

    def set_config(self, action: WatchAction, /, *, spec: WatchActionInput, dry_run: bool = False) -> WatchAction:
        """Replace one action's config in place (same rank, same row). `action` is
        the existing row ; `spec` is the new desired state.

        The new config is re-validated (shape + merge + policy) so the persisted
        blob is normalized ; `kind` is the spec's top-level discriminator and MAY
        change (a node can switch kind, e.g. swap one filter for another). When the
        kind is UNCHANGED, the prior config is fed to `merge_preserving` so
        edit-round-trip state (a redacted secret the operator left masked) is
        carried forward ; on a kind change there's no comparable prior, so the
        submitted config wins wholesale.

        No chain lock: rank is irrelevant here (a digest is allowed at any position,
        head included), so there's no chain-state race to guard. The row write +
        window cleanup share one transaction so an edit AWAY from digest can't
        half-apply (config instant but window lingering = stranded runs, since
        claim_due excludes a run while its action has a window). An edit INTO digest
        just leaves any existing window alone; the trigger or the advance opens one
        when the next item arrives.

        When `dry_run`, apply the validated + merged config in memory and return the
        row WITHOUT saving or clearing windows - nothing is persisted."""
        if str(action.account_id) != self.account_id:
            raise ValueError(f"action account_id mismatch: {action.account_id!r} not in scope {self.account_id!r}")
        prior = load_config(action) if str(action.kind) == spec.kind else None
        merged = merge_config(spec.kind, spec.config.model_dump(mode="json"), prior)
        action.kind = spec.kind
        action.config = merged.model_dump(mode="json")
        if dry_run:  # validated + merged in memory; persist nothing, touch no window
            return action
        is_digest = isinstance(merged, DeliveryConfigBase) and merged.is_digest()
        with transaction.atomic():
            action.save(update_fields=["kind", "config", "updated_at"])
            if not is_digest:
                self._clear_digest_windows([str(action.id)])
        return action

    def remove(self, action: WatchAction, /) -> None:
        """Delete one action and close the rank gap on its path. Drops the
        removed action's digest window (if any). Raises `ConcurrentChainError`
        if another chain mutation holds the lock."""
        if str(action.account_id) != self.account_id:
            raise ValueError(f"action account_id mismatch: {action.account_id!r} not in scope {self.account_id!r}")
        path_id = str(action.path_id)
        action_id = str(action.id)  # capture before delete() nulls it in memory
        with path_chain_lock(path_id) as acquired:
            if not acquired:
                raise ConcurrentChainError(f"another chain edit is in progress on path {path_id}; retry")
            with transaction.atomic():
                action.delete()
                self._clear_digest_windows([action_id])
                chain = self.list_for_path(path_id)
                self._renumber(chain)

    def _renumber(self, ordered: builtins.list[WatchAction]) -> None:
        """Assign dense ranks 0..N-1 in list order (all rows already saved).

        Two-phase to dodge the unique `(path_id, rank)` constraint: offset
        past the max LIVE rank, then write finals. The offset is
        `max(rank)+1`, NOT `len(ordered)`: replace_chain parks new rows
        above the survivor count, and SQLite checks the constraint per-row
        mid-`UPDATE`, so a count-based offset can collide with a parked
        rank. `updated_at` is set by hand on the final pass (bulk_update
        skips `auto_now`); the offset pass writes `rank` only."""
        now = timezone.now()
        offset = max((row.rank for row in ordered), default=0) + 1
        for i, row in enumerate(ordered):
            row.rank = i + offset
        WatchAction.objects.bulk_update(ordered, ["rank"])
        for i, row in enumerate(ordered):
            row.rank = i
            row.updated_at = now
        WatchAction.objects.bulk_update(ordered, ["rank", "updated_at"])
