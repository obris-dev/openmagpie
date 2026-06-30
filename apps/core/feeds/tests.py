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

import ulid
from django.test import SimpleTestCase, TestCase
from rest_framework.test import APIClient

from auth_api.operations.signup import SignupOperation
from feeds.models import Feed, FeedItem
from feeds.services.sources import _hash_spec
from openmagpie_schema.configs import RedditSubredditSourceSpec, RssSourceSpec
from openmagpie_schema.feed import (
    FeedItemData,
    FeedItemPayload,
    FeedItemWire,
    NewRedditPostPayload,
    RssEntryPayload,
    SourceWire,
)


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
        # "ClaudeAI" validates to the bare, lowercased "claudeai"; the pin is the
        # hash of that canonical dump (changed when the slug validator landed).
        spec = RedditSubredditSourceSpec(subreddit="ClaudeAI")
        self.assertEqual(
            _hash_spec(spec),
            "3f21fb2a64266e815c8ca2da8f440528ae864e1acec97a2322fda9e565cbdeb6",
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


class FeedPauseResumeTests(TestCase):
    """PATCH /v1/feeds/<id> toggles is_active (pause/resume) without touching config or
    sources; create can start a feed paused; account-scoped."""

    def setUp(self) -> None:
        self.user = SignupOperation(email="fpr@example.com", password="Str0ng-Passw0rd!").run()
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

    def test_patch_pauses_and_resumes_keeping_sources(self) -> None:
        resp = self.client.patch(f"/v1/feeds/{self.feed_id}", {"is_active": False}, format="json")
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertFalse(resp.json()["is_active"])
        self.assertFalse(Feed.objects.get(id=self.feed_id).is_active)
        # a pause is config-neutral: the source set survives (a PUT would replace it)
        self.assertEqual(len(self.client.get(f"/v1/feeds/{self.feed_id}/sources").json()["items"]), 1)
        resp = self.client.patch(f"/v1/feeds/{self.feed_id}", {"is_active": True}, format="json")
        self.assertTrue(resp.json()["is_active"])

    def test_create_paused(self) -> None:
        resp = self.client.post(
            "/v1/feeds",
            {
                "name": "p",
                "kind": "curated",
                "poll_interval_seconds": 300,
                "is_active": False,
                "data": {"retention_days": 30},
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 201, resp.content)
        self.assertFalse(Feed.objects.get(id=resp.json()["id"]).is_active)

    def test_put_without_is_active_resets_to_active(self) -> None:
        # PUT is full-replace: omitting is_active takes the serializer default (True),
        # exactly like an omitted poll_interval. The flag-only toggle is pause/resume.
        self.client.patch(f"/v1/feeds/{self.feed_id}", {"is_active": False}, format="json")
        resp = self.client.put(
            f"/v1/feeds/{self.feed_id}",
            {"name": "f", "kind": "curated", "poll_interval_seconds": 300, "data": {"retention_days": 30}},
            format="json",
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertTrue(Feed.objects.get(id=self.feed_id).is_active)  # PUT-omit reset it to active

    def test_account_isolation(self) -> None:
        other = APIClient()
        other.force_authenticate(user=SignupOperation(email="fpr-other@example.com", password="Str0ng-Passw0rd!").run())
        self.assertEqual(other.patch(f"/v1/feeds/{self.feed_id}", {"is_active": False}, format="json").status_code, 404)
        self.assertTrue(Feed.objects.get(id=self.feed_id).is_active)  # untouched


class FeedItemPayloadUnionTests(SimpleTestCase):
    """`FeedItemWire.data` parses the connector payload dump into the typed
    `FeedItemData` union: a known `kind` resolves to its variant (typed
    connector fields), an unknown or kind-less dump falls to the permissive
    base so a newer connector / older row can't break the read."""

    def _data(self, payload: dict) -> FeedItemData:
        wire = FeedItemWire.model_validate(
            {"id": str(ulid.ulid()), "source_kind": "x", "external_id": "e", "data": payload}
        )
        return wire.data

    def test_known_reddit_kind_resolves_to_typed_variant(self) -> None:
        data = self._data({"kind": "new_post", "subreddit": "ClaudeAI", "permalink": "/r/x/1", "title": "T"})
        assert isinstance(data, NewRedditPostPayload)  # narrows the union for the typed reads below
        self.assertEqual(data.subreddit, "ClaudeAI")
        self.assertEqual(data.title, "T")

    def test_known_rss_kind_resolves_to_typed_variant(self) -> None:
        data = self._data({"kind": "rss_entry", "categories": ["a", "b"], "feed_url": "https://f.test"})
        assert isinstance(data, RssEntryPayload)  # narrows the union for the typed read below
        self.assertEqual(data.categories, ["a", "b"])

    def test_unknown_kind_falls_to_base_keeping_extras(self) -> None:
        data = self._data({"kind": "github_issue", "repo": "o/r", "title": "T"})
        self.assertIs(type(data), FeedItemPayload)
        self.assertEqual((data.model_extra or {}).get("repo"), "o/r")
        self.assertEqual(data.title, "T")

    def test_kindless_dump_falls_to_base_not_first_variant(self) -> None:
        # The subtle invariant: variants REQUIRE their `kind` literal, so a
        # kind-less dict can't greedily match the first union member; it lands
        # on the base instead of becoming a (wrong) RssEntryPayload.
        self.assertIs(type(self._data({"title": "T"})), FeedItemPayload)

    def test_malformed_known_kind_degrades_to_base(self) -> None:
        # A dump whose `kind` matches a variant but whose other fields fail that
        # variant (here categories must be list[str]) degrades to the permissive
        # base rather than raising - the documented robustness trade-off. The raw
        # bad value is kept in model_extra; canonical fields still read.
        data = self._data({"kind": "rss_entry", "categories": "oops", "title": "T"})
        self.assertIs(type(data), FeedItemPayload)
        self.assertEqual((data.model_extra or {}).get("categories"), "oops")
        self.assertEqual(data.title, "T")


class SourceWireDisplayTests(SimpleTestCase):
    """SourceWire exposes the kind-polymorphic source label as a computed `display`
    field on the wire, so a consumer reads one labeled field instead of
    re-deriving `spec.display()` per kind. Output-only: a dump round-trips."""

    def test_display_computed_per_kind_and_in_dump(self) -> None:
        reddit = SourceWire(id="s1", spec=RedditSubredditSourceSpec(subreddit="ClaudeAI"))
        self.assertEqual(reddit.model_dump(mode="json")["display"], "r/claudeai")  # slug stored lowercased
        rss_named = SourceWire(id="s2", spec=RssSourceSpec(url="https://a.test/rss", name="Example"))
        self.assertEqual(rss_named.model_dump(mode="json")["display"], "Example")
        rss_bare = SourceWire(id="s3", spec=RssSourceSpec(url="https://b.test/rss"))
        self.assertEqual(rss_bare.model_dump(mode="json")["display"], "https://b.test/rss")

    def test_display_is_output_only_and_round_trips(self) -> None:
        # A dumped wire carries `display`; re-validating it must NOT error AND must
        # IGNORE the incoming value, recomputing from `spec` (the field is output-only).
        dumped = SourceWire(id="s1", spec=RedditSubredditSourceSpec(subreddit="x")).model_dump(mode="json")
        self.assertIn("display", dumped)
        dumped["display"] = "WRONG"  # a stale / tampered incoming value must be dropped
        self.assertEqual(SourceWire.model_validate(dumped).display, "r/x")
