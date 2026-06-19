from datetime import UTC, datetime
from typing import Any
from unittest import mock

import httpx
from django.test import SimpleTestCase
from pydantic import ValidationError

from openmagpie_schema.configs import HackerNewsCommentSourceSpec, HackerNewsFeedSourceSpec
from sources.connectors.base import ConnectorParseError
from sources.connectors.hackernews.algolia import PAGE_SIZE, AlgoliaSearch
from sources.connectors.hackernews.connector import HackerNewsCommentConnector, HackerNewsFeedConnector
from sources.connectors.hackernews.payloads import HackerNewsFeedPayload


def _hn_hit(item_id: int, created_at_i: int | None, **over: Any) -> dict[str, Any]:
    """One Algolia `search_by_date` hit, link-post shaped by default."""
    hit: dict[str, Any] = {
        "objectID": str(item_id),
        "title": f"Story {item_id}",
        "url": f"https://example.com/{item_id}",
        "author": "alice",
        "points": 42,
        "num_comments": 7,
        "created_at_i": created_at_i,
    }
    hit.update(over)
    return hit


class HackerNewsFeedConnectorTests(SimpleTestCase):
    """The HN feed connector walks Algolia `search_by_date` via AlgoliaSearch:
    it pushes the `since` watermark into a `created_at_i` numericFilter,
    hydrates each hit, pages until a short/empty page, skips schema-broken
    hits, and degrades a non-JSON / wrong-shape body to a recoverable
    ConnectorParseError."""

    _SPEC = HackerNewsFeedSourceSpec(kind="hn_feed", feed="new")

    def _poll_with(self, pages: list[dict], spec: HackerNewsFeedSourceSpec | None = None, since=None):
        """Run one poll against a canned sequence of Algolia JSON pages,
        capturing each request's query params. Returns (payloads, requests)."""
        queue = list(pages)
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(200, json=queue.pop(0))

        transport = httpx.MockTransport(handler)
        real_client = httpx.Client
        with mock.patch(
            "sources.connectors.hackernews.algolia.httpx.Client",
            lambda **kw: real_client(transport=transport, **kw),
        ):
            payloads = list(HackerNewsFeedConnector().poll(spec or self._SPEC, since=since))
        return payloads, requests

    def test_poll_hydrates_a_hit_into_a_typed_payload(self) -> None:
        payloads, _ = self._poll_with([{"hits": [_hn_hit(101, 1_700_000_000, story_text="<p>hi &amp; bye</p>")]}])
        self.assertEqual(len(payloads), 1)
        p = payloads[0]
        self.assertEqual(p.kind, "hn_feed")
        self.assertEqual(p.external_id, "101")
        self.assertEqual(p.title, "Story 101")
        self.assertEqual(p.url, "https://news.ycombinator.com/item?id=101")  # the item's home (discussion)
        self.assertEqual(p.external_url, "https://example.com/101")  # the off-site link the engine fetches
        self.assertEqual(p.author, "alice")
        self.assertEqual(p.points, 42)
        self.assertEqual(p.num_comments, 7)
        self.assertEqual(p.feed, "new")
        # story_text is HTML-stripped + entity-unescaped into content.
        self.assertEqual(p.content, "hi & bye")

    def test_text_post_has_no_external_url_and_url_is_the_discussion(self) -> None:
        # Ask HN posts carry no outbound link: external_url is empty (nothing to
        # fetch), and url is always the HN discussion.
        spec = HackerNewsFeedSourceSpec(kind="hn_feed", feed="ask")
        payloads, requests = self._poll_with([{"hits": [_hn_hit(202, 1_700_000_000, url=None)]}], spec=spec)
        self.assertEqual(payloads[0].url, "https://news.ycombinator.com/item?id=202")
        self.assertEqual(payloads[0].external_url, "")
        self.assertEqual(requests[0].url.params["tags"], "ask_hn")

    def test_since_is_pushed_into_the_numeric_filter(self) -> None:
        since = datetime(2023, 11, 14, 22, 13, 20, tzinfo=UTC)  # 1_700_000_000
        _, requests = self._poll_with([{"hits": []}], since=since)
        self.assertEqual(requests[0].url.params["numericFilters"], "created_at_i>=1700000000")

    def test_pagination_walks_until_a_short_page(self) -> None:
        full = {"hits": [_hn_hit(i, 1_700_000_000 - i) for i in range(PAGE_SIZE)]}
        tail = {"hits": [_hn_hit(9001, 1_699_000_000)]}
        payloads, requests = self._poll_with([full, tail])
        self.assertEqual(len(payloads), PAGE_SIZE + 1)
        self.assertEqual([int(r.url.params["page"]) for r in requests], [0, 1])

    def test_hit_missing_created_at_is_skipped_not_fatal(self) -> None:
        page = {"hits": [_hn_hit(1, 1_700_000_000), _hn_hit(2, None), _hn_hit(3, 1_699_999_000)]}
        payloads, _ = self._poll_with([page])
        self.assertEqual([p.external_id for p in payloads], ["1", "3"])

    def test_non_json_body_raises_recoverable_parse_error(self) -> None:
        transport = httpx.MockTransport(lambda req: httpx.Response(200, content=b"<html>rate limited</html>"))
        real_client = httpx.Client
        with (
            mock.patch(
                "sources.connectors.hackernews.algolia.httpx.Client",
                lambda **kw: real_client(transport=transport, **kw),
            ),
            self.assertRaises(ConnectorParseError),
        ):
            list(HackerNewsFeedConnector().poll(self._SPEC, since=None))

    def test_missing_hits_array_raises_recoverable_parse_error(self) -> None:
        with self.assertRaises(ConnectorParseError):
            self._poll_with([{"nbHits": 0}])

    def test_count_matches_the_poll_walk(self) -> None:
        # count() is the BaseConnector poll-walk default (no nbHits override),
        # so it must agree with the number of payloads poll yields. Serve the
        # same short page to both the count walk and the poll walk.
        page = {"hits": [_hn_hit(i, 1_700_000_000 - i) for i in range(3)]}
        transport = httpx.MockTransport(lambda req: httpx.Response(200, json=page))
        real_client = httpx.Client
        with mock.patch(
            "sources.connectors.hackernews.algolia.httpx.Client",
            lambda **kw: real_client(transport=transport, **kw),
        ):
            conn = HackerNewsFeedConnector()
            n = conn.count(self._SPEC, since=None)
            payloads = list(conn.poll(self._SPEC, since=None))
        self.assertEqual(n, 3)
        self.assertEqual(n, len(payloads))

    def test_oversize_body_raises_recoverable_parse_error(self) -> None:
        # A body over the cap surfaces as ConnectorParseError (recoverable), not
        # an OOM. A tiny max_body_bytes makes a normal page trip the cap.
        search = AlgoliaSearch(max_body_bytes=10)
        transport = httpx.MockTransport(lambda req: httpx.Response(200, json={"hits": [_hn_hit(1, 1_700_000_000)]}))
        real_client = httpx.Client
        with (
            mock.patch(
                "sources.connectors.hackernews.algolia.httpx.Client",
                lambda **kw: real_client(transport=transport, **kw),
            ),
            self.assertRaises(ConnectorParseError),
        ):
            list(
                search.walk(
                    tag="story",
                    since=None,
                    to_payload=lambda hit, when: HackerNewsFeedPayload.from_algolia_hit(hit, self._SPEC, when),
                )
            )

    def test_query_and_match_any_pass_through_for_the_feed(self) -> None:
        # The feed connector also accepts the optional query pre-filter; match:
        # any -> Algolia optionalWords (OR), the same path the comment connector
        # exercises.
        spec = HackerNewsFeedSourceSpec(kind="hn_feed", feed="new", query="rust zig", match="any")
        _, requests = self._poll_with([{"hits": []}], spec=spec)
        self.assertEqual(requests[0].url.params["query"], "rust zig")
        self.assertEqual(requests[0].url.params["optionalWords"], "rust zig")

    def test_page_cap_is_enforced_and_warns(self) -> None:
        # The cap (page_size * max_pages) is the load-bearing volume guard. With
        # every page full, walk stops at exactly max_pages requests and cap-many
        # payloads, and warns that an older tail went unfetched (silent loss).
        search = AlgoliaSearch(page_size=2, max_pages=2)
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            base = 2_000_000_000 - len(requests) * 10
            return httpx.Response(200, json={"hits": [_hn_hit(base - i, base - i) for i in range(2)]})

        transport = httpx.MockTransport(handler)
        real_client = httpx.Client
        with (
            mock.patch(
                "sources.connectors.hackernews.algolia.httpx.Client",
                lambda **kw: real_client(transport=transport, **kw),
            ),
            self.assertLogs("sources", level="WARNING") as logs,
        ):
            payloads = list(
                search.walk(
                    tag="story",
                    since=None,
                    to_payload=lambda hit, when: HackerNewsFeedPayload.from_algolia_hit(hit, self._SPEC, when),
                )
            )
        self.assertEqual(len(requests), 2)  # exactly max_pages, no more
        self.assertEqual(len(payloads), 4)  # page_size * max_pages
        self.assertIn("page cap", "\n".join(logs.output))


class HackerNewsCommentConnectorTests(SimpleTestCase):
    """The comment connector walks `tags=comment`, maps the parent story title
    onto the canonical title (engine context), pushes the `query` pre-filter
    (and optionalWords for OR), and requires a query at the spec layer."""

    _SPEC = HackerNewsCommentSourceSpec(kind="hn_comment", query="kubernetes")

    def _poll_with(self, pages: list[dict], spec: HackerNewsCommentSourceSpec | None = None, since=None):
        queue = list(pages)
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(200, json=queue.pop(0))

        transport = httpx.MockTransport(handler)
        real_client = httpx.Client
        with mock.patch(
            "sources.connectors.hackernews.algolia.httpx.Client",
            lambda **kw: real_client(transport=transport, **kw),
        ):
            payloads = list(HackerNewsCommentConnector().poll(spec or self._SPEC, since=since))
        return payloads, requests

    def test_comment_maps_story_title_into_title_for_engine_context(self) -> None:
        hit = {
            "objectID": "999",
            "comment_text": "<p>great point &amp; more</p>",
            "author": "bob",
            "story_id": 555,
            "story_title": "Some Story",
            "created_at_i": 1_700_000_000,
        }
        payloads, _ = self._poll_with([{"hits": [hit]}])
        p = payloads[0]
        self.assertEqual(p.kind, "hn_comment")
        self.assertEqual(p.external_id, "999")
        self.assertEqual(p.title, "Some Story")  # parent headline -> title (engine context)
        self.assertEqual(p.content, "great point & more")
        self.assertEqual(p.parent_external_id, "555")
        self.assertEqual(p.story_title, "Some Story")
        self.assertEqual(p.feed, "comments")
        self.assertEqual(p.url, "https://news.ycombinator.com/item?id=999")

    def test_query_is_pushed_as_the_pre_filter(self) -> None:
        _, requests = self._poll_with([{"hits": []}])
        self.assertEqual(requests[0].url.params["tags"], "comment")
        self.assertEqual(requests[0].url.params["query"], "kubernetes")
        self.assertNotIn("optionalWords", requests[0].url.params)

    def test_match_any_adds_optional_words_for_or(self) -> None:
        spec = HackerNewsCommentSourceSpec(kind="hn_comment", query="kubernetes nomad", match="any")
        _, requests = self._poll_with([{"hits": []}], spec=spec)
        self.assertEqual(requests[0].url.params["optionalWords"], "kubernetes nomad")

    def test_query_is_required_at_the_spec_layer(self) -> None:
        # The firehose guard: a comment source with no query can't be built.
        # Built via model_validate (runtime dict) so the deliberate omission
        # isn't a static type error.
        with self.assertRaises(ValidationError):
            HackerNewsCommentSourceSpec.model_validate({"kind": "hn_comment"})
