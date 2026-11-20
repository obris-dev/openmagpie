"""Product Hunt launches connector tests.

Only the HTTP boundary is faked: `_FakeApi` (an httpx.MockTransport handler)
serves launches by their day and pages them by cursor, like the GraphQL API.
Pinned: the oldest-first day walk, the re-read of the watermark's day, the page
budget stopping a poll and the next poll resuming, a failed poll retried from the
old watermark, the rate-limit / token / error refusals (a missing token is
refused by the poller: feeds/tests_required_settings.py), payload mapping, the
bower columns, and spec validation.
"""

import json
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from typing import Any
from unittest import mock

import httpx
from django.test import SimpleTestCase, override_settings
from pydantic import ValidationError

from openmagpie_schema.configs import ProductHuntLaunchesSourceSpec
from sources.connectors.base import ConnectorParseError
from sources.connectors.producthunt import client as ph_client
from sources.connectors.producthunt import connector as ph_connector
from sources.connectors.producthunt.client import ProductHuntClient
from sources.connectors.producthunt.connector import ProductHuntLaunchesConnector, utc_days
from sources.connectors.producthunt.payloads import NewProductHuntLaunchPayload
from watches.services._bower_fields import scalar_fields

DAY1 = datetime(2026, 11, 16, tzinfo=UTC)


def slot(day: int, minute: int = 1) -> datetime:
    """Launch slot of the `day`th day after DAY1 (08:01 UTC, or 08:00)."""
    return DAY1 + timedelta(days=day, hours=8, minutes=minute)


def launch(post_id: str, when: datetime, **overrides: Any) -> dict[str, Any]:
    post: dict[str, Any] = {
        "id": post_id,
        "name": f"Product {post_id}",
        "tagline": f"Tagline {post_id}",
        "description": f"Description {post_id}",
        "url": f"https://www.producthunt.com/products/product-{post_id}?utm_source=graphql",
        "featuredAt": when.isoformat(),
        "makers": [{"name": "Ada Maker"}, {"name": "[REDACTED]"}],
        "topics": {"edges": [{"node": {"name": "Developer Tools"}}, {"node": {"name": "SaaS"}}]},
    }
    post.update(overrides)
    return post


class _FakeApi:
    """Serves `launches` like the posts query: the ones in [postedAfter,
    postedBefore), newest first, `first` per page, cursor = offset. `fail_on`
    makes that call (1-based) answer 500."""

    def __init__(self, launches: list[dict[str, Any]], *, fail_on: int | None = None) -> None:
        self.launches = launches
        self.fail_on = fail_on
        self.calls: list[dict[str, Any]] = []
        self.headers: list[httpx.Headers] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        variables = json.loads(request.content)["variables"]
        self.calls.append(variables)
        self.headers.append(request.headers)
        if self.fail_on == len(self.calls):
            return httpx.Response(500)
        lo, hi = datetime.fromisoformat(variables["postedAfter"]), datetime.fromisoformat(variables["postedBefore"])
        day = sorted(
            (p for p in self.launches if lo <= datetime.fromisoformat(p["featuredAt"]) < hi),
            key=lambda p: p["featuredAt"],
            reverse=True,
        )
        offset = int(variables["after"] or 0)
        end = offset + variables["first"]
        cursor = str(end) if end < len(day) else None
        connection = {
            "edges": [{"node": p} for p in day[offset:end]],
            "pageInfo": {"endCursor": cursor, "hasNextPage": cursor is not None},
        }
        return httpx.Response(200, json={"data": {"posts": connection}})


def poll(handler: Any, since: datetime, *, now: datetime, topic: str = "") -> list[NewProductHuntLaunchPayload]:
    conn = ProductHuntLaunchesConnector()
    conn._client = ProductHuntClient(_transport=httpx.MockTransport(handler))
    with mock.patch.object(ph_connector.timezone, "now", return_value=now):
        payloads = conn.poll(ProductHuntLaunchesSourceSpec(topic=topic), since=since)
        return [p for p in payloads if isinstance(p, NewProductHuntLaunchPayload)]


def ids(payloads: list[NewProductHuntLaunchPayload]) -> list[str]:
    return [p.external_id for p in payloads]


@override_settings(PRODUCT_HUNT_TOKEN="test-token")
@mock.patch.object(ph_client, "PAGE_SIZE", 2)
class ProductHuntWalkTests(SimpleTestCase):
    def test_steady_state_rereads_the_watermarks_day_and_nothing_older(self):
        api = _FakeApi([launch("old", slot(3)), launch("a", slot(4)), launch("b", slot(4, minute=0))])
        self.assertEqual(sorted(ids(poll(api, since=slot(4), now=slot(4) + timedelta(hours=6)))), ["a", "b"])
        self.assertEqual(api.headers[0]["Authorization"], "Bearer test-token")

    def test_a_launch_added_to_the_current_day_is_picked_up(self):
        api = _FakeApi([launch("seen", slot(4)), launch("late", slot(4))])
        self.assertIn("late", ids(poll(api, since=slot(4), now=slot(4) + timedelta(hours=6))))

    def test_days_are_read_oldest_first_one_day_at_a_time(self):
        api = _FakeApi([launch(str(d), slot(d)) for d in range(3)])
        got = poll(api, since=slot(0), now=slot(2) + timedelta(hours=1), topic="saas")
        self.assertEqual(ids(got), ["0", "1", "2"])
        midnights = [(DAY1 + timedelta(days=d)).isoformat() for d in range(4)]
        self.assertEqual([(c["postedAfter"], c["postedBefore"]) for c in api.calls], list(pairwise(midnights)))
        self.assertTrue(all(c["topic"] == "saas" for c in api.calls))
        self.assertEqual({p.source_slug() for p in got}, {"saas"})

    def test_a_backlog_past_the_budget_resumes_on_the_next_poll(self):
        launches = [launch(f"{d}-{i}", slot(d)) for d in range(5) for i in range(3)]  # 2 pages a day
        now = slot(4) + timedelta(hours=6)
        recorded: set[str] = set()
        since = slot(0)
        with mock.patch.object(ph_connector, "PAGES_PER_POLL", 3), self.assertLogs("sources", "WARNING") as logs:
            for _ in range(10):
                got = poll(_FakeApi(launches), since=since, now=now)
                recorded |= set(ids(got))
                newest = max(p.occurred_at for p in got)
                if newest == since:
                    break
                since = newest  # the poller's watermark advance
        self.assertEqual(recorded, {p["id"] for p in launches})
        self.assertIn("continuing next poll", logs.output[0])

    def test_a_poll_that_fails_midway_is_retried_from_the_old_watermark(self):
        launches = [launch(f"{d}-{i}", slot(d)) for d in range(2) for i in range(3)]
        now = slot(1) + timedelta(hours=6)
        with self.assertRaises(httpx.HTTPStatusError):
            poll(_FakeApi(launches, fail_on=2), since=slot(0), now=now)
        self.assertEqual(set(ids(poll(_FakeApi(launches), since=slot(0), now=now))), {p["id"] for p in launches})

    def test_a_malformed_edge_fails_the_source(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"data": {"posts": {"edges": [{"node": "oops"}], "pageInfo": {}}}})

        with self.assertRaises(ConnectorParseError):
            poll(handler, since=slot(4), now=slot(4) + timedelta(hours=1))

    def test_a_short_rate_limit_is_waited_out(self):
        responses = [httpx.Response(429, headers={"X-Rate-Limit-Reset": "7"})]
        api = _FakeApi([launch("1", slot(4))])

        def handler(request: httpx.Request) -> httpx.Response:
            return responses.pop(0) if responses else api(request)

        with mock.patch.object(ph_client, "sleep_with_heartbeat") as sleep:
            self.assertEqual(ids(poll(handler, since=slot(4), now=slot(4) + timedelta(hours=1))), ["1"])
        self.assertEqual(sleep.call_args.args[0], 7.0)

    def test_a_long_rate_limit_fails_at_once(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(429, headers={"X-Rate-Limit-Reset": "900"})

        with (
            mock.patch.object(ph_client, "sleep_with_heartbeat") as sleep,
            self.assertRaisesRegex(ConnectorParseError, "rate limited for 900s"),
        ):
            poll(handler, since=slot(4), now=slot(4) + timedelta(hours=1))
        sleep.assert_not_called()

    def test_graphql_errors_fail_the_source(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"errors": [{"message": "Field 'nope' doesn't exist"}]})

        with self.assertRaisesRegex(ConnectorParseError, "nope"):
            poll(handler, since=slot(4), now=slot(4) + timedelta(hours=1))

    def test_a_refused_token_names_the_setting(self):
        with self.assertRaisesRegex(ConnectorParseError, "PRODUCT_HUNT_TOKEN"):
            poll(lambda request: httpx.Response(401), since=slot(4), now=slot(4) + timedelta(hours=1))


class UtcDaysTests(SimpleTestCase):
    def test_days_from_a_mid_day_start(self):
        days = list(utc_days(DAY1 + timedelta(hours=7), until=DAY1 + timedelta(days=2, hours=1)))
        self.assertEqual(days, [DAY1, DAY1 + timedelta(days=1), DAY1 + timedelta(days=2)])

    def test_a_start_on_midnight_and_a_same_day_until(self):
        self.assertEqual(list(utc_days(DAY1, until=DAY1 + timedelta(hours=5))), [DAY1])


class ProductHuntPayloadTests(SimpleTestCase):
    def test_maps_a_post(self):
        spec = ProductHuntLaunchesSourceSpec(topic="developer-tools")
        payload = NewProductHuntLaunchPayload.from_post(launch("42", slot(4)), spec, occurred_at=slot(4))
        self.assertEqual(payload.external_id, "42")
        self.assertEqual(payload.title, "Product 42")
        self.assertEqual(payload.content, "Tagline 42\n\nDescription 42")
        self.assertEqual(payload.url, "https://www.producthunt.com/products/product-42")
        self.assertEqual(payload.external_url, "")  # the API's website is a bot-challenged redirect
        self.assertEqual((payload.makers, payload.author), ("Ada Maker", "Ada Maker"))  # redacted maker dropped
        self.assertEqual(payload.topics, "Developer Tools, SaaS")
        self.assertEqual((payload.source_topic, payload.source_slug()), ("developer-tools", "developer-tools"))

    def test_a_source_over_every_topic_has_no_slug(self):
        payload = NewProductHuntLaunchPayload.from_post(
            launch("1", slot(4)), ProductHuntLaunchesSourceSpec(), occurred_at=slot(4)
        )
        self.assertIsNone(payload.source_slug())

    def test_malformed_topics_are_skipped(self):
        payload = NewProductHuntLaunchPayload.from_post(
            launch("1", slot(4), topics=["x"]), ProductHuntLaunchesSourceSpec(), occurred_at=slot(4)
        )
        self.assertEqual(payload.topics, "")

    def test_bower_columns(self):
        expected = {"occurred_at": "datetime", "title": "text", "content": "text", "url": "url", "external_url": "url"}
        expected |= {"author": "text", "tagline": "text", "makers": "text", "topics": "text", "source_topic": "text"}
        self.assertEqual(scalar_fields(NewProductHuntLaunchPayload), expected)


class ProductHuntSpecTests(SimpleTestCase):
    def test_a_slug_or_any_spelling_of_its_topic_url_gives_the_slug(self):
        for value in (
            "developer-tools",
            "Developer-Tools",
            "https://www.producthunt.com/topics/Developer-Tools/",
            "http://www.producthunt.com/topics/developer-tools",
            "https://producthunt.com/topics/developer-tools",
            "producthunt.com/topics/developer-tools",
            "https://www.producthunt.com/topics/developer-tools?ref=home#top",
        ):
            with self.subTest(value=value):
                self.assertEqual(ProductHuntLaunchesSourceSpec(topic=value).topic, "developer-tools")

    def test_blank_means_every_topic(self):
        self.assertEqual(ProductHuntLaunchesSourceSpec(topic="  ").topic, "")

    def test_anything_else_is_refused_never_read_as_every_topic(self):
        for value in (
            "https://www.producthunt.com/topics/",
            "https://example.com/topics/developer-tools",
            "https://www.producthunt.com/posts/developer-tools",
            "dev tools",
        ):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                ProductHuntLaunchesSourceSpec(topic=value)

    def test_display(self):
        self.assertEqual(ProductHuntLaunchesSourceSpec(topic="saas").display(), "Product Hunt launches: saas")
