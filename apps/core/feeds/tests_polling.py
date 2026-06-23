"""Poll-cycle semantics for `FeedPollOperation` and `_PolledSource`.

Four seams of the same contract, each pinned at its own level:
- lease heartbeat: renewed per source, lost lease stops the cycle
  (PollHeartbeatTests);
- iterator failure semantics: every connector error propagates, a clean
  empty stream stays a success (PolledSourceFailureSemanticsTests);
- the real `_poll_source`: a mid-stream failure never advances the
  watermark, a newest-first source would otherwise permanently strand the
  unreached tail below it (PollSourceWatermarkGuardTests);
- run-loop accounting: all-sources-failed trips `full_outage` and skips
  the retention prune (FullOutagePruneGuardTests);
- the DB-level watermark write: `advance_watermark` is monotonic for a set
  mark and bootstraps a NULL one, so a (shouldn't-happen) null source
  self-heals rather than re-fetching forever (SourceServiceWatermarkTests).
"""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest import mock

import httpx
import ulid
from django.test import SimpleTestCase, TestCase

from feeds.models import Feed, Source
from feeds.services.polling import FeedPollOperation, _PolledSource
from feeds.services.sources import SourceService
from openmagpie_schema.configs import RssSourceSpec


class PollHeartbeatTests(TestCase):
    """The poll loop renews its lease per source (so a large feed polls under
    one held lock) and stops early if the lease is lost."""

    def _op_over_n_sources(self, n: int, *, heartbeat) -> FeedPollOperation:
        feed = Feed.objects.create(account_id=ulid.ulid(), user_id=ulid.ulid(), name="f", kind="curated", data={})
        op = FeedPollOperation(feed, heartbeat=heartbeat)
        sources = [
            SimpleNamespace(
                id=f"s{i}", kind="rss", spec={"kind": "rss", "url": f"https://x{i}.test/rss", "name": f"s{i}"}
            )
            for i in range(n)
        ]
        # Inject fake sources before the cached_property fires (no DB rows).
        # The poll streams via `iter_for_poll` (random order in prod) and reads
        # the total via `count`; here order is irrelevant, so iterate as-is.
        op.__dict__["source_svc"] = SimpleNamespace(
            iter_for_poll=lambda _feed, **kw: iter(sources),
            iter_by_kind=lambda _feed, kind: iter(()),  # no reddit sources in these tests
            count=lambda _feed: len(sources),
        )
        return op

    def test_heartbeat_called_once_per_source(self) -> None:
        calls = {"n": 0}

        def hb() -> bool:
            calls["n"] += 1
            return True

        op = self._op_over_n_sources(3, heartbeat=hb)
        with mock.patch.object(op, "_poll_source", return_value=(0, 0)):  # no HTTP
            op.run()
        self.assertEqual(calls["n"], 3)

    def test_lost_lease_stops_the_cycle_early(self) -> None:
        # Lease reported lost after the first source -> the loop must stop.
        calls = {"n": 0}

        def hb() -> bool:
            calls["n"] += 1
            return calls["n"] <= 1

        op = self._op_over_n_sources(5, heartbeat=hb)
        with mock.patch.object(op, "_poll_source", return_value=(0, 0)) as poll:
            op.run()
        self.assertEqual(poll.call_count, 1)  # stopped, didn't poll the other 4


class PolledSourceFailureSemanticsTests(SimpleTestCase):
    """_PolledSource propagates EVERY connector error to the per-source
    handler: a failed cycle must not count as a succeeded source (the
    full-outage prune guard) and must not advance the watermark (on a
    newest-first source, advancing over a partial stream would strand the
    unreached older-but-new posts below the watermark, permanently)."""

    _T0 = datetime(2026, 1, 1, tzinfo=UTC)

    def test_error_before_first_payload_propagates(self) -> None:
        def gen():
            raise httpx.ConnectError("rate limited into the ground")
            yield  # makes this a generator; never reached

        with self.assertRaises(httpx.ConnectError):
            list(_PolledSource(gen(), initial_newest=self._T0))

    def test_error_mid_stream_propagates_after_the_prefix(self) -> None:
        first = SimpleNamespace(occurred_at=datetime(2026, 1, 2, tzinfo=UTC))

        def gen():
            yield first
            raise httpx.ReadTimeout("paging hiccup")

        polled = _PolledSource(gen(), initial_newest=self._T0)
        stream = iter(polled)
        self.assertIs(next(stream), first)  # the prefix flows to record_items
        with self.assertRaises(httpx.ReadTimeout):
            next(stream)  # ...then the failure reaches the per-source handler
        self.assertEqual(polled.observed, 1)

    def test_clean_empty_stream_is_a_success(self) -> None:
        polled = _PolledSource(iter(()), initial_newest=self._T0)
        self.assertEqual(list(polled), [])
        self.assertEqual(polled.observed, 0)
        self.assertEqual(polled.newest, self._T0)


class PollSourceWatermarkGuardTests(TestCase):
    """Drives failures through the REAL `_poll_source` (fake connector +
    services, real consumption path): a mid-stream connector error must
    propagate AND leave `advance_watermark` uncalled - on a newest-first
    source, advancing over the partial prefix would permanently strand the
    unreached tail below the watermark. The clean-path contrast pins that
    the guard isn't vacuous."""

    _SINCE = datetime(2026, 1, 1, tzinfo=UTC)
    _SPEC = RssSourceSpec(kind="rss", url="https://x.test/rss", name="s")

    def _run_poll_source(self, connector_poll):
        feed = Feed.objects.create(account_id=ulid.ulid(), user_id=ulid.ulid(), name="f", kind="curated", data={})
        op = FeedPollOperation(feed)
        source = SimpleNamespace(
            id="s1",
            kind="rss",
            spec={"kind": "rss", "url": "https://x.test/rss", "name": "s"},
            last_event_at=self._SINCE,
            field_map=None,
            meta=None,
        )
        source_svc = SimpleNamespace(advance_watermark=mock.Mock())
        op.__dict__["source_svc"] = source_svc
        # Consumes the payload stream like the real record_items does; the
        # mid-stream raise must surface through this consumption.
        op.__dict__["feed_item_svc"] = SimpleNamespace(
            record_items=lambda feed, source_label, source_meta, payloads: sum(1 for _ in payloads),
        )
        registry_patch = mock.patch(
            "feeds.services.polling.source_registry.get",
            return_value=SimpleNamespace(poll=connector_poll),
        )
        return op, source_svc.advance_watermark, registry_patch, source

    def test_mid_stream_failure_does_not_advance_watermark(self) -> None:
        first = SimpleNamespace(occurred_at=datetime(2026, 1, 2, tzinfo=UTC))

        def poll(spec, since, field_map=None, heartbeat=None):
            yield first
            raise httpx.ReadTimeout("page 2 died")

        op, advance, registry_patch, source = self._run_poll_source(poll)
        with registry_patch, self.assertRaises(httpx.ReadTimeout):
            op._poll_source(source, self._SPEC)
        advance.assert_not_called()

    def test_clean_stream_advances_watermark(self) -> None:
        first = SimpleNamespace(occurred_at=datetime(2026, 1, 2, tzinfo=UTC))

        def poll(spec, since, field_map=None, heartbeat=None):
            yield first

        op, advance, registry_patch, source = self._run_poll_source(poll)
        with registry_patch:
            op._poll_source(source, self._SPEC)
        advance.assert_called_once_with(source, first.occurred_at)


class FullOutagePruneGuardTests(TestCase):
    """A cycle where every source fails must skip the retention prune
    (`full_outage`); one healthy source is enough to prune normally.
    `_poll_source` is mocked here, so this pins the run-loop accounting
    only (failed source -> not sources_succeeded -> prune decision);
    the real `_poll_source` failure path is pinned by
    `PollSourceWatermarkGuardTests` and the iterator semantics by
    `PolledSourceFailureSemanticsTests`."""

    def _op_with_sources(self, n: int) -> tuple[FeedPollOperation, mock.Mock]:
        feed = Feed.objects.create(account_id=ulid.ulid(), user_id=ulid.ulid(), name="f", kind="curated", data={})
        op = FeedPollOperation(feed)
        sources = [
            SimpleNamespace(
                id=f"s{i}", kind="rss", spec={"kind": "rss", "url": f"https://x{i}.test/rss", "name": f"s{i}"}
            )
            for i in range(n)
        ]
        op.__dict__["source_svc"] = SimpleNamespace(
            iter_for_poll=lambda _feed, **kw: iter(sources),
            iter_by_kind=lambda _feed, kind: iter(()),  # no reddit sources in these tests
            count=lambda _feed: len(sources),
        )
        item_svc = mock.Mock()
        item_svc.prune_items.return_value = 0
        op.__dict__["feed_item_svc"] = item_svc
        return op, item_svc

    def test_all_sources_failing_skips_the_prune(self) -> None:
        op, item_svc = self._op_with_sources(2)
        with mock.patch.object(op, "_poll_source", side_effect=httpx.ConnectError("down")):
            result = op.run()
        item_svc.prune_items.assert_not_called()
        self.assertEqual(result.pruned, 0)

    def test_one_healthy_source_prunes_normally(self) -> None:
        op, item_svc = self._op_with_sources(2)
        with mock.patch.object(op, "_poll_source", side_effect=[httpx.ConnectError("down"), (1, 1)]):
            op.run()
        item_svc.prune_items.assert_called_once()


class SourceServiceWatermarkTests(TestCase):
    """The REAL `advance_watermark` against the DB (the poll-path tests mock it):
    monotonic for a set mark, and a NULL mark bootstraps instead of no-op'ing on
    the `last_event_at__lt` guard - the self-heal the batch/single-source converge
    rely on for a (shouldn't-happen) null watermark."""

    _T1 = datetime(2026, 1, 1, tzinfo=UTC)
    _T2 = datetime(2026, 1, 2, tzinfo=UTC)
    _T3 = datetime(2026, 1, 3, tzinfo=UTC)

    def _null_source(self) -> tuple[SourceService, Source]:
        account_id = str(ulid.ulid())
        feed = Feed.objects.create(account_id=account_id, user_id=ulid.ulid(), name="f", kind="curated", data={})
        source = Source.objects.create(
            id=str(ulid.ulid()),
            account_id=account_id,
            feed_id=str(feed.id),
            kind="reddit_subreddit",
            spec={"kind": "reddit_subreddit", "subreddit": "python"},
            spec_hash="h1",
            last_event_at=None,
        )
        return SourceService(account_id=account_id), source

    def test_null_mark_bootstraps_then_advances_monotonically(self) -> None:
        svc, source = self._null_source()
        # NULL bootstraps to the first-seen value (the `__lt` guard alone would
        # no-op here, since `NULL < value` is unknown).
        self.assertEqual(svc.advance_watermark(source, self._T2), 1)
        source.refresh_from_db()
        self.assertEqual(source.last_event_at, self._T2)
        # Now monotonic: an earlier value is a no-op, a later one advances.
        self.assertEqual(svc.advance_watermark(source, self._T1), 0)
        source.refresh_from_db()
        self.assertEqual(source.last_event_at, self._T2)
        self.assertEqual(svc.advance_watermark(source, self._T3), 1)
        source.refresh_from_db()
        self.assertEqual(source.last_event_at, self._T3)
