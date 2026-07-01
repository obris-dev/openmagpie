import time
from datetime import timedelta

import ulid
from django.conf import settings
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from auth_api.operations.signup import SignupOperation
from openmagpie_schema.watch import build_watch_action_input
from openmagpie_schema.watch_actions import WebhookConfig
from openmagpie_schema.watch_enums import WatchActionRunState
from watches.management.commands.process_due_runs import _breakdown, _fmt_duration, _progress
from watches.models import Watch, WatchAction, WatchActionRun
from watches.policy import PolicyError
from watches.registry import load_config
from watches.services import WatchActionRunService, WatchService
from watches.services.runs._common import completion_ts


class ReplaceChainUpsertTests(TestCase):
    """replace_chain upserts by action id: known id updates in place, no id
    is new, absent rows are deleted, ranks renumber densely."""

    def setUp(self) -> None:
        self.account_id = ulid.ulid()
        self.wsvc = WatchService(account_id=self.account_id)
        self.asvc = self.wsvc.action_svc

    def _logs(self, prefixes):
        watch = self.wsvc.create(
            user_id=ulid.ulid(),
            name="t",
            feed_ids=[],
            actions=[build_watch_action_input(kind="log", config={"prefix": p}) for p in prefixes],
        )
        return watch, self.asvc.list_for_path(watch.initial_path_id)

    def test_remove_and_add_in_one_edit(self) -> None:
        # Regression: delete + add in one edit must not hit the unique
        # (path, rank) constraint during the dense renumber.
        watch, chain = self._logs(["[A]", "[B]", "[C]"])
        by = {r.config["prefix"]: r for r in chain}
        rows = self.asvc.replace_chain(
            path_id=watch.initial_path_id,
            actions=[
                build_watch_action_input(id=str(by["[C]"].id), kind="log", config={"prefix": "[C]"}),
                build_watch_action_input(id=str(by["[A]"].id), kind="log", config={"prefix": "[A]"}),
                build_watch_action_input(kind="log", config={"prefix": "[D]"}),
            ],
        )
        self.assertEqual([(r.config["prefix"], r.rank) for r in rows], [("[C]", 0), ("[A]", 1), ("[D]", 2)])
        self.assertEqual(str(rows[0].id), str(by["[C]"].id))
        self.assertFalse(WatchAction.objects.filter(id=by["[B]"].id).exists())

    def test_reorder_preserves_ids(self) -> None:
        watch, (a, b) = self._logs(["[A]", "[B]"])
        rows = self.asvc.replace_chain(
            path_id=watch.initial_path_id,
            actions=[
                build_watch_action_input(id=str(b.id), kind="log", config={"prefix": "[B]"}),
                build_watch_action_input(id=str(a.id), kind="log", config={"prefix": "[A]"}),
            ],
        )
        self.assertEqual([str(r.id) for r in rows], [str(b.id), str(a.id)])
        self.assertEqual([r.rank for r in rows], [0, 1])

    def test_edit_preserves_action_id_and_run_history(self) -> None:
        watch, (a, b) = self._logs(["[A]", "[B]"])
        run = WatchActionRun.objects.create(
            account_id=self.account_id,
            watch_id=str(watch.id),
            action_id=str(a.id),
            feed_item_id=ulid.ulid(),
            state="succeeded",
            scheduled_at=timezone.now(),
        )
        self.asvc.replace_chain(
            path_id=watch.initial_path_id,
            actions=[
                build_watch_action_input(id=str(a.id), kind="log", config={"prefix": "[A2]"}),
                build_watch_action_input(id=str(b.id), kind="log", config={"prefix": "[B]"}),
            ],
        )
        self.assertTrue(WatchActionRun.objects.filter(id=run.id, action_id=str(a.id)).exists())

    def test_unknown_id_rejected(self) -> None:
        watch, _ = self._logs(["[A]"])
        with self.assertRaises(PolicyError):
            self.asvc.replace_chain(
                path_id=watch.initial_path_id,
                actions=[build_watch_action_input(id=ulid.ulid(), kind="log", config={"prefix": "[X]"})],
            )

    def test_reorder_two_webhooks_keeps_each_secret_with_its_endpoint(self) -> None:
        # The fixed 3b case: masked reorder restores each token to its own row.
        watch = self.wsvc.create(
            user_id=ulid.ulid(),
            name="t",
            feed_ids=[],
            actions=[
                build_watch_action_input(
                    kind="webhook", config={"url": "https://a.example.com/h", "headers": {"Authorization": "tokA"}}
                ),
                build_watch_action_input(
                    kind="webhook", config={"url": "https://b.example.com/h", "headers": {"Authorization": "tokB"}}
                ),
            ],
        )
        a, b = self.asvc.list_for_path(watch.initial_path_id)

        def masked(action):
            return build_watch_action_input(
                id=str(action.id), kind="webhook", config=load_config(action).redacted_dump()
            )

        rows = self.asvc.replace_chain(path_id=watch.initial_path_id, actions=[masked(b), masked(a)])
        cfg = {str(r.id): load_config(r) for r in rows}
        ca, cb = cfg[str(a.id)], cfg[str(b.id)]
        assert isinstance(ca, WebhookConfig) and isinstance(cb, WebhookConfig)
        self.assertEqual(ca.headers["Authorization"], "tokA")
        self.assertEqual(cb.headers["Authorization"], "tokB")

    def test_new_webhook_with_masked_secret_rejected(self) -> None:
        watch, _ = self._logs([])
        with self.assertRaises(PolicyError):
            self.asvc.replace_chain(
                path_id=watch.initial_path_id,
                actions=[
                    build_watch_action_input(
                        kind="webhook", config={"url": "https://h.example.com/x", "headers": {"Authorization": "***"}}
                    ),
                ],
            )


class SetActiveTests(TestCase):
    """set_active is the lightweight pause/resume: it flips is_active and leaves the
    action chain alone (unlike update(), which full-replaces and recreates it)."""

    def setUp(self) -> None:
        self.account_id = ulid.ulid()
        self.wsvc = WatchService(account_id=self.account_id)

    def test_pause_resume_preserves_action_ids(self) -> None:
        watch = self.wsvc.create(
            user_id=ulid.ulid(),
            name="t",
            feed_ids=[],
            actions=[build_watch_action_input(kind="log", config={"prefix": "[A]"})],
        )
        ids = [str(r.id) for r in self.wsvc.action_svc.list_for_path(watch.initial_path_id)]
        self.wsvc.set_active(watch, is_active=False)
        watch.refresh_from_db()
        self.assertFalse(watch.is_active)
        # the chain is untouched: same rows, not recreated (a full update() would churn them)
        self.assertEqual([str(r.id) for r in self.wsvc.action_svc.list_for_path(watch.initial_path_id)], ids)
        self.wsvc.set_active(watch, is_active=True)
        watch.refresh_from_db()
        self.assertTrue(watch.is_active)


class WatchPutOmitResetsActiveTests(TestCase):
    """PUT is full-replace: a watch edit that OMITS is_active resets it to active (the
    serializer defaults it True), like an omitted feed_ids/actions clearing those. The
    flag-only toggle is pause/resume (PATCH). Mirrors the feed-side guarantee."""

    def setUp(self) -> None:
        self.user = SignupOperation(email="wpr@example.com", password="Str0ng-Passw0rd!").run()
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        resp = self.client.post(
            "/v1/watches",
            {"name": "w", "feed_ids": [], "actions": [{"kind": "log", "config": {"prefix": "[A]"}}]},
            format="json",
        )
        self.assertEqual(resp.status_code, 201, resp.content)
        self.watch_id = resp.json()["id"]

    def test_put_without_is_active_resets_to_active(self) -> None:
        self.client.patch(f"/v1/watches/{self.watch_id}", {"is_active": False}, format="json")
        resp = self.client.put(
            f"/v1/watches/{self.watch_id}",
            {"name": "w", "feed_ids": [], "actions": [{"kind": "log", "config": {"prefix": "[A]"}}]},
            format="json",
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertTrue(Watch.objects.get(id=self.watch_id).is_active)  # PUT-omit reset it to active


class ActionRunListTests(TestCase):
    """list_for_action: newest-first, scoped to the action, state filter."""

    def setUp(self) -> None:
        self.account_id = ulid.ulid()
        self.runs = WatchActionRunService(account_id=self.account_id)

    def _run(self, action_id: str, state: str) -> WatchActionRun:
        return WatchActionRun.objects.create(
            account_id=self.account_id,
            watch_id=ulid.ulid(),
            action_id=action_id,
            feed_item_id=ulid.ulid(),
            state=state,
            scheduled_at=timezone.now(),
        )

    def test_newest_first_state_filter_and_scoping(self) -> None:
        aid = ulid.ulid()
        made = [self._run(aid, s) for s in ("succeeded", "gated", "succeeded")]
        # newest-first = descending id (ULIDs in one ms aren't creation-ordered).
        expected = sorted((str(r.id) for r in made), reverse=True)
        self.assertEqual([str(r.id) for r in self.runs.list_for_action(aid)], expected)
        succeeded = {str(made[0].id), str(made[2].id)}
        self.assertEqual({str(r.id) for r in self.runs.list_for_action(aid, state="succeeded")}, succeeded)
        self.assertEqual(self.runs.list_for_action(ulid.ulid()), [])


class CountDueTests(TestCase):
    """count_due (sizes the drain's progress/ETA line) must report exactly
    what claim_due would yield — same `_due_runs` filter, no claim."""

    def setUp(self) -> None:
        self.now = timezone.now()

    def _run(self, *, state: str, scheduled_at, attempts: int = 0) -> WatchActionRun:
        return WatchActionRun.objects.create(
            account_id=ulid.ulid(),
            watch_id=ulid.ulid(),
            action_id=ulid.ulid(),
            feed_item_id=ulid.ulid(),
            state=state,
            attempts=attempts,
            scheduled_at=scheduled_at,
        )

    def test_count_matches_claim_due_and_honors_the_due_filter(self) -> None:
        past, future = self.now - timedelta(minutes=1), self.now + timedelta(minutes=1)
        # Due: pending + retryable-failed, scheduled in the past, under the cap.
        self._run(state="pending", scheduled_at=past)
        self._run(state="failed", scheduled_at=past)
        # Not due: future schedule, terminal states, attempts at the cap.
        self._run(state="pending", scheduled_at=future)
        self._run(state="succeeded", scheduled_at=past)
        self._run(state="gated", scheduled_at=past)
        self._run(state="pending", scheduled_at=past, attempts=settings.WATCH_RUN_MAX_ATTEMPTS)

        self.assertEqual(WatchActionRunService.Global.count_due(now=self.now), 2)
        # claim_due drains (mutates) the same set, so count it last.
        claimed = list(WatchActionRunService.Global.claim_due(now=self.now))
        self.assertEqual(len(claimed), 2)


class ProgressFormatTests(TestCase):
    """The drain's ETA string: coarse h/m/s, remaining floored at 0."""

    def test_fmt_duration_buckets(self) -> None:
        self.assertEqual(_fmt_duration(9), "9s")
        self.assertEqual(_fmt_duration(184), "3m04s")
        self.assertEqual(_fmt_duration(3700), "1h01m")

    def test_progress_eta_and_floor(self) -> None:
        # 2 of 10 done in 20s -> 10s/run avg, 8 left -> ~80s = 1m20s.
        self.assertEqual(_progress(2, 10, time.monotonic() - 20), "[2/10, ~1m20s left]")
        # More fell due than the snapshot: remaining floors at 0, never negative.
        self.assertEqual(_progress(12, 10, time.monotonic() - 20), "[12/10, ~0s left]")

    def test_breakdown(self) -> None:
        # Lifecycle order (succeeded before gated, per the enum), commas not
        # dots, "none" if empty — regardless of insertion order.
        self.assertEqual(_breakdown({"gated": 342, "succeeded": 3}), "3 succeeded, 342 gated")
        self.assertEqual(_breakdown({}), "none")


class CompletedAtRuleTests(TestCase):
    """completion_ts is the one rule: completed_at is set iff the run is
    TERMINAL — never on a retry-pending FAILED. Plus the live invariant that
    complete() honors it."""

    def test_terminal_states_complete(self) -> None:
        now = timezone.now()
        for state in ("succeeded", "gated", "errored", "skipped"):
            self.assertEqual(completion_ts(state, 1, now), now)

    def test_failed_is_terminal_only_when_exhausted(self) -> None:
        now = timezone.now()
        self.assertIsNone(completion_ts("failed", 1, now))  # under the cap -> retry
        self.assertEqual(completion_ts("failed", settings.WATCH_RUN_MAX_ATTEMPTS, now), now)  # exhausted

    def test_backlog_states_never_complete(self) -> None:
        now = timezone.now()
        self.assertIsNone(completion_ts("pending", 1, now))
        self.assertIsNone(completion_ts("running", 99, now))

    def test_complete_leaves_retryable_failed_uncompleted(self) -> None:
        account_id = ulid.ulid()
        svc = WatchActionRunService(account_id=account_id)
        run = WatchActionRun.objects.create(
            account_id=account_id,
            watch_id=ulid.ulid(),
            action_id=ulid.ulid(),
            feed_item_id=ulid.ulid(),
            state="running",
            attempts=1,  # under WATCH_RUN_MAX_ATTEMPTS (3)
            scheduled_at=timezone.now(),
            started_at=timezone.now(),
        )
        done = svc.complete(run, state=WatchActionRunState.FAILED, error="boom")
        assert done is not None
        # Retry-pending: FAILED but NOT terminal, so no completed_at.
        self.assertEqual(done.state, "failed")
        self.assertIsNone(done.completed_at)
        done.refresh_from_db()
        self.assertIsNone(done.completed_at)

    def test_complete_stamps_terminal_success(self) -> None:
        account_id = ulid.ulid()
        svc = WatchActionRunService(account_id=account_id)
        run = WatchActionRun.objects.create(
            account_id=account_id,
            watch_id=ulid.ulid(),
            action_id=ulid.ulid(),
            feed_item_id=ulid.ulid(),
            state="running",
            attempts=1,
            scheduled_at=timezone.now(),
            started_at=timezone.now(),
        )
        done = svc.complete(run, state=WatchActionRunState.SUCCEEDED)
        assert done is not None and done.completed_at is not None
