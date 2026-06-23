"""Scatter/advance semantics for `_reddit_batch.poll_reddit_group`.

One combined fetch per chunk, results scattered to each source by subreddit, and
each source's watermark advanced to ITS OWN sub's newest post (not the chunk's
global newest) - monotonically, so a source already ahead never rewinds, and a
sub with no fetched posts keeps its mark to re-read next cycle.
"""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest import mock

import ulid
from django.test import SimpleTestCase, TestCase

from feeds.models import Feed
from feeds.services._reddit_batch import _iter_url_budget_chunks
from feeds.services.polling import FeedPollOperation
from sources.connectors.reddit.connector import (
    COMBINED_URL_OVERHEAD,
    MAX_COMBINED_URL_BYTES,
    RedditSubRedditConnector,
)


def _src(sub: str, mark: datetime | None) -> SimpleNamespace:
    return SimpleNamespace(
        id=f"s-{sub}",
        kind="reddit_subreddit",
        spec={"kind": "reddit_subreddit", "subreddit": sub},
        last_event_at=mark,
        meta=None,
    )


def _post(sub: str, when: datetime) -> SimpleNamespace:
    return SimpleNamespace(subreddit=sub, occurred_at=when)


class RedditBatchGroupTests(TestCase):
    """Drives `poll_reddit_group` with a mocked combined fetch + mocked services,
    so it pins the group/scatter/converge orchestration only (the fetch itself is
    pinned by `RedditCombinedPollTests` in sources)."""

    _T9 = datetime(2026, 6, 23, 9, 0, tzinfo=UTC)
    _T10 = datetime(2026, 6, 23, 10, 0, tzinfo=UTC)
    _T11 = datetime(2026, 6, 23, 11, 0, tzinfo=UTC)
    _T12 = datetime(2026, 6, 23, 12, 0, tzinfo=UTC)
    _T13 = datetime(2026, 6, 23, 13, 0, tzinfo=UTC)

    def _op(self) -> FeedPollOperation:
        feed = Feed.objects.create(account_id=ulid.ulid(), user_id=ulid.ulid(), name="f", kind="curated", data={})
        op = FeedPollOperation(feed)
        self._advance = mock.Mock()  # captured here so its type is Mock, not the real method
        op.__dict__["source_svc"] = SimpleNamespace(advance_watermark=self._advance)
        self._recorded: list[tuple[str, int]] = []

        def record_items(feed, source_label, source_meta, payloads):
            items = list(payloads)
            self._recorded.append((source_label, len(items)))
            return len(items)

        op.__dict__["feed_item_svc"] = SimpleNamespace(record_items=record_items)
        return op

    def _run(self, op, sources, posts):
        connector = RedditSubRedditConnector()  # real, so the isinstance gate passes
        with (
            mock.patch.object(connector, "poll_combined", return_value=iter(posts)),
            mock.patch("feeds.services._reddit_batch.source_registry.get", return_value=connector),
        ):
            return op.poll_reddit_group(sources)

    def test_scatters_posts_to_their_source_by_subreddit(self) -> None:
        op = self._op()
        sources = [_src("alpha", self._T9), _src("beta", self._T9)]
        posts = [_post("alpha", self._T12), _post("beta", self._T11), _post("alpha", self._T10)]
        observed, recorded, succeeded = self._run(op, sources, posts)
        self.assertEqual(sorted(self._recorded), [("r/alpha", 2), ("r/beta", 1)])
        self.assertEqual((observed, recorded, succeeded), (3, 3, 2))

    def test_advances_each_source_to_its_own_newest(self) -> None:
        op = self._op()
        sources = [_src("alpha", self._T9), _src("beta", self._T10)]  # marks differ
        posts = [_post("alpha", self._T12), _post("beta", self._T11)]
        self._run(op, sources, posts)
        # Each source advances to ITS OWN sub's newest, NOT the chunk's global
        # newest - so a sub starved of budget by a flooded sibling isn't stranded.
        advance = self._advance
        self.assertEqual(advance.call_count, 2)
        targets = {c.args[0].id: c.args[1] for c in advance.call_args_list}
        self.assertEqual(targets, {"s-alpha": self._T12, "s-beta": self._T11})

    def test_sub_with_no_fetched_posts_keeps_its_mark(self) -> None:
        op = self._op()
        # beta is BEHIND (T9) but got no posts this fetch (e.g. truncated out by a
        # flooded sibling sharing the chunk budget). It must NOT jump to alpha's
        # newest - it keeps its mark and re-reads next cycle.
        sources = [_src("alpha", self._T9), _src("beta", self._T9)]
        posts = [_post("alpha", self._T12)]
        self._run(op, sources, posts)
        advance = self._advance
        advance.assert_called_once()  # only alpha advances
        self.assertEqual(advance.call_args.args[0].id, "s-alpha")
        self.assertEqual(advance.call_args.args[1], self._T12)

    def test_source_ahead_of_the_newest_post_never_rewinds(self) -> None:
        op = self._op()
        # beta's mark (13:00) is AHEAD of the newest post (12:00): max() keeps it,
        # so beta is left untouched while alpha advances.
        sources = [_src("alpha", self._T9), _src("beta", self._T13)]
        posts = [_post("alpha", self._T12)]
        self._run(op, sources, posts)
        advance = self._advance
        advance.assert_called_once()
        self.assertEqual(advance.call_args.args[0].id, "s-alpha")
        self.assertEqual(advance.call_args.args[1], self._T12)

    def test_ahead_sub_drops_sub_mark_overlap_but_keeps_the_boundary(self) -> None:
        op = self._op()
        # beta (mark T9) drags the combined `since` to T9, so alpha (mark T10) gets
        # older overlap in its bucket. The per-sub `>=` cutoff drops what alpha is
        # already past (T9, below its mark) but KEEPS the exact-mark tie (T10) - a
        # same-second new post must not be silently dropped; the FeedItem dedup is
        # the backstop. So alpha records 2 (T10, T12), not 3 (no filter) or 1 (`>`),
        # and observed is the filtered 3, not the 4 fetched.
        sources = [_src("alpha", self._T10), _src("beta", self._T9)]
        posts = [
            _post("alpha", self._T9),  # below alpha's mark -> overlap, dropped
            _post("alpha", self._T10),  # == alpha's mark -> kept by `>=`
            _post("alpha", self._T12),  # new -> kept
            _post("beta", self._T11),  # above beta's mark -> kept
        ]
        observed, _recorded, _succeeded = self._run(op, sources, posts)
        self.assertEqual(sorted(self._recorded), [("r/alpha", 2), ("r/beta", 1)])
        self.assertEqual(observed, 3)

    def test_null_mark_bootstraps_without_aborting(self) -> None:
        op = self._op()
        # last_event_at is non-null by invariant; if one were null the batch must
        # NOT TypeError on min()/the converge, and the null source bootstraps to
        # its first-seen newest (advance_watermark's isnull branch sets a NULL row -
        # see SourceServiceWatermarkTests for the real-DB proof) instead of staying
        # stuck. Both subs advance.
        sources = [_src("alpha", None), _src("beta", self._T9)]
        posts = [_post("alpha", self._T12), _post("beta", self._T11)]
        _observed, _recorded, succeeded = self._run(op, sources, posts)
        self.assertEqual(succeeded, 2)
        targets = {c.args[0].id: c.args[1] for c in self._advance.call_args_list}
        self.assertEqual(targets, {"s-alpha": self._T12, "s-beta": self._T11})

    def test_case_only_duplicate_rows_are_deduped(self) -> None:
        op = self._op()
        # Two rows differing only by case (a legacy pair from before the slug
        # validator lowercased): the sub must be fetched + recorded ONCE, not
        # twice (both would otherwise route to the same case-folded bucket).
        sources = [_src("python", self._T9), _src("Python", self._T9)]
        posts = [_post("python", self._T12)]
        _observed, recorded, succeeded = self._run(op, sources, posts)
        self.assertEqual(recorded, 1)
        self.assertEqual(succeeded, 1)
        self.assertEqual(self._recorded, [("r/python", 1)])


class IterUrlBudgetChunksTests(SimpleTestCase):
    """`_iter_url_budget_chunks` streams (source, spec) pairs into chunks whose
    combined URL stays under Reddit's ~8KB wall: a realistic feed is one chunk, a
    1000+ sub feed splits, and the chunks partition the input in order."""

    @staticmethod
    def _pairs(names: list[str]):
        return [(None, SimpleNamespace(subreddit=n)) for n in names]

    def test_realistic_feed_is_a_single_chunk(self) -> None:
        names = [f"sub{i:03d}" for i in range(50)]
        chunks = list(_iter_url_budget_chunks(iter(self._pairs(names))))
        self.assertEqual(len(chunks), 1)
        self.assertEqual([spec.subreddit for _, spec in chunks[0]], names)

    def test_oversized_feed_splits_into_in_budget_chunks(self) -> None:
        names = [f"subreddit{i:05d}" for i in range(3000)]
        chunks = list(_iter_url_budget_chunks(iter(self._pairs(names))))
        self.assertGreater(len(chunks), 1)
        budget = MAX_COMBINED_URL_BYTES - COMBINED_URL_OVERHEAD
        for chunk in chunks:
            slug = "+".join(spec.subreddit for _, spec in chunk)
            self.assertLessEqual(len(slug), budget)  # built URL stays under ~8192
        flat = [spec.subreddit for c in chunks for _, spec in c]
        self.assertEqual(flat, names)  # partition, in order, nothing dropped

    def test_empty_yields_no_chunks(self) -> None:
        self.assertEqual(list(_iter_url_budget_chunks(iter(()))), [])
