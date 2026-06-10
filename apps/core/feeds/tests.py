"""Regression tests for the feeds app.

`SpecHashCanonicalTests` pins the sha256 produced by
`feeds.services.sources._hash_spec` for a known spec (one case per
spec kind plus an order-independence check). If a future change to
the SourceSpec field set or field-declaration order silently
changes the hash, these tests break; the fix is to add a data
migration that recomputes existing rows' `spec_hash` column, then
update the pinned values below to the new canonical hash. The `(account_id, feed_id,
spec_hash)` unique constraint is the dedup key on set_sources, so
a silent hash drift would break re-imports.
"""

from types import SimpleNamespace
from unittest import mock

import ulid
from django.test import SimpleTestCase, TestCase
from rest_framework.test import APIClient

from auth_api.operations.signup import SignupOperation
from feeds.models import Feed, FeedItem
from feeds.services.polling import FeedPollOperation
from feeds.services.sources import _hash_spec
from openmagpie_schema.configs import RedditSubredditSourceSpec, RssSourceSpec


class PollHeartbeatTests(TestCase):
    """The poll loop renews its lease per source (so a large feed polls under
    one held lock) and stops early if the lease is lost."""

    def _op_over_n_sources(self, n: int, *, heartbeat) -> FeedPollOperation:
        feed = Feed.objects.create(account_id=ulid.ulid(), user_id=ulid.ulid(), name="f", kind="curated", data={})
        op = FeedPollOperation(feed, heartbeat=heartbeat)
        sources = [
            SimpleNamespace(id=f"s{i}", spec={"kind": "rss", "url": f"https://x{i}.test/rss", "name": f"s{i}"})
            for i in range(n)
        ]
        # Inject fake sources before the cached_property fires (no DB rows).
        # The poll streams via `iter_for_poll` (random order in prod) and reads
        # the total via `count`; here order is irrelevant, so iterate as-is.
        op.__dict__["source_svc"] = SimpleNamespace(
            iter_for_poll=lambda _feed: iter(sources),
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


class FeedCreateDryRunTests(TestCase):
    """The create dry-run reports the would-be source count without
    persisting anything (the preview feed is built without Source rows, so
    the count must come from the validated request)."""

    def setUp(self) -> None:
        self.user = SignupOperation(email="dryrun@example.com", password="Str0ng-Passw0rd!").run()
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def test_dry_run_reports_submitted_source_count(self) -> None:
        body = {
            "name": "f",
            "kind": "curated",
            "poll_interval_seconds": 300,
            "data": {"retention_days": 30},
            "sources": [
                {"spec": {"kind": "rss", "url": "https://a.test/rss", "name": "A"}},
                {"spec": {"kind": "rss", "url": "https://b.test/rss", "name": "B"}},
            ],
        }
        resp = self.client.post("/v1/feeds?dry_run=true", body, format="json")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["dry_run"])
        self.assertEqual(data["source_count"], 2)  # would-be count, not the persisted 0
        self.assertEqual(Feed.objects.count(), 0)  # nothing persisted


class SpecHashCanonicalTests(SimpleTestCase):
    def test_reddit_subreddit_pinned_hash(self) -> None:
        spec = RedditSubredditSourceSpec(subreddit="ClaudeAI")
        self.assertEqual(
            _hash_spec(spec),
            "0da1f0763888956b29fd3ed95ef61a7b847f94c982ee458fedc7562a6c171a80",
        )

    def test_rss_pinned_hash(self) -> None:
        spec = RssSourceSpec(url="https://example.com/feed.rss", name="Example")
        self.assertEqual(
            _hash_spec(spec),
            "fd85eb925e162f0a2644eff338e98f1a4693d2afe74d7597852c99c912c8f903",
        )

    def test_hash_is_order_independent_for_dict_inputs(self) -> None:
        """Two specs with the same fields produce the same hash
        regardless of which order they were constructed in. Guards
        against a future field reorder on the SourceSpec subclass
        producing a different `model_dump` ordering."""
        a = RssSourceSpec(url="https://example.com/feed.rss", name="A")
        b = RssSourceSpec(name="A", url="https://example.com/feed.rss")
        self.assertEqual(_hash_spec(a), _hash_spec(b))


class FeedItemAndSourceRouteTests(TestCase):
    """By-own-id detail routes for a feed's items + sources, plus the
    cursor-paginated item list. Items are read-only; a source can be deleted by
    its own id. Account isolation throughout."""

    def setUp(self) -> None:
        self.user = SignupOperation(email="frt@example.com", password="Str0ng-Passw0rd!").run()
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        resp = self.client.post(
            "/v1/feeds",
            {
                "name": "f",
                "kind": "curated",
                "poll_interval_seconds": 300,
                "data": {"retention_days": 30},
                "sources": [{"spec": {"kind": "rss", "url": "https://a.test/rss", "name": "A"}}],
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 201, resp.content)
        self.feed_id = resp.json()["id"]
        self.account_id = Feed.objects.get(id=self.feed_id).account_id

    def _item(self, external_id: str) -> FeedItem:
        return FeedItem.objects.create(
            account_id=self.account_id,
            feed_id=self.feed_id,
            source_kind="rss",
            external_id=external_id,
            source_label="A",
            data={"title": f"t-{external_id}", "url": "https://a.test/x"},
        )

    def _source_id(self) -> str:
        return self.client.get(f"/v1/feeds/{self.feed_id}/sources").json()["items"][0]["id"]

    def test_feed_source_detail_and_delete_by_own_id(self) -> None:
        source_id = self._source_id()
        got = self.client.get(f"/v1/feed-sources/{source_id}")
        self.assertEqual(got.status_code, 200, got.content)
        self.assertEqual(got.json()["id"], source_id)
        # delete by own id (no feed in the path)
        self.assertEqual(self.client.delete(f"/v1/feed-sources/{source_id}").status_code, 204)
        self.assertEqual(self.client.get(f"/v1/feed-sources/{source_id}").status_code, 404)
        self.assertEqual(self.client.delete(f"/v1/feed-sources/{ulid.ulid()}").status_code, 404)

    def test_feed_items_list_and_detail(self) -> None:
        a = self._item("a")
        b = self._item("b")
        listed = self.client.get(f"/v1/feeds/{self.feed_id}/items")
        self.assertEqual(listed.status_code, 200, listed.content)
        self.assertEqual({r["id"] for r in listed.json()["items"]}, {str(a.id), str(b.id)})
        got = self.client.get(f"/v1/feed-items/{a.id}")
        self.assertEqual(got.status_code, 200, got.content)
        self.assertEqual(got.json()["id"], str(a.id))
        self.assertEqual(got.json()["data"]["title"], "t-a")
        self.assertEqual(self.client.get(f"/v1/feed-items/{ulid.ulid()}").status_code, 404)

    def test_account_isolation(self) -> None:
        item = self._item("a")
        source_id = self._source_id()
        other = APIClient()
        other.force_authenticate(user=SignupOperation(email="frt-other@example.com", password="Str0ng-Passw0rd!").run())
        self.assertEqual(other.get(f"/v1/feed-items/{item.id}").status_code, 404)
        self.assertEqual(other.get(f"/v1/feed-sources/{source_id}").status_code, 404)
        self.assertEqual(other.delete(f"/v1/feed-sources/{source_id}").status_code, 404)
        # the owner's source is untouched by the other account's failed delete
        self.assertEqual(self.client.get(f"/v1/feed-sources/{source_id}").status_code, 200)
