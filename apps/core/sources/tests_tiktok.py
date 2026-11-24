"""TikTok (SerpBase) connector tests.

Only the HTTP boundary is faked: `_FakeApi` (an httpx.MockTransport handler)
serves SerpBase's `/tiktok/search` envelope. Pinned: the business-status check
(HTTP 200 with a non-zero `status` is a failure, never an empty search), the
retryable vs non-retryable split, the watermark filter, payload mapping, the
URL host allowlist, and spec validation.
"""

import json
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest import mock

import httpx
from django.test import SimpleTestCase, override_settings

from openmagpie_schema.configs import TikTokSearchSourceSpec
from sources.connectors.base import ConnectorParseError
from sources.connectors.tiktok.client import SerpBaseClient
from sources.connectors.tiktok.connector import TikTokSearchConnector
from sources.connectors.tiktok.payloads import NewTikTokVideoPayload, parse_timestamp

NOW = datetime(2026, 11, 21, 12, 0, tzinfo=UTC)


def post(post_id: str, *, published: datetime | None = None, **overrides: Any) -> dict[str, Any]:
    """One `/tiktok/search` post, shaped like the live API."""
    item: dict[str, Any] = {
        "id": post_id,
        "text": f"Caption {post_id} #sociallistening",
        "published_at": (published or NOW).isoformat().replace("+00:00", "Z"),
        "url": f"https://www.tiktok.com/@creator{post_id}/video/{post_id}",
        "link": f"https://www.tiktok.com/@creator{post_id}/video/{post_id}",
        "author": {"username": f"creator{post_id}", "name": f"Creator {post_id}"},
        "hashtags": ["sociallistening", "tools"],
        "likes_count": "120",
        "comments_count": "7",
        "shares_count": "3",
        "views_count": "4500",
        "saves_count": "11",
    }
    item.update(overrides)
    return item


def envelope(
    posts: list[dict[str, Any]], *, status: int = 0, has_more: bool = False, cursor: str = "16"
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "status": status,
        "credits_charged": 1,
        "request_id": "r-test",
        "product_id": "tiktok_search",
        "posts": posts,
        "pagination": {"has_more": has_more, "next_cursor": cursor},
    }
    if status != 0:
        body.pop("posts")
        body["message"] = "failed"
    return body


class _FakeApi:
    """Serves a queue of replies; records every request."""

    def __init__(self, *replies: tuple[int, dict[str, Any]]) -> None:
        self.replies = list(replies)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        status, body = self.replies.pop(0)
        return httpx.Response(status, json=body)


def poll(
    *replies: tuple[int, dict[str, Any]], since: datetime | None = None, **spec_kwargs: Any
) -> list[NewTikTokVideoPayload]:
    connector = TikTokSearchConnector()
    connector._client = SerpBaseClient(_transport=httpx.MockTransport(_FakeApi(*replies)))
    spec = TikTokSearchSourceSpec(query="social listening", **spec_kwargs)
    return [payload for payload in connector.poll(spec, since=since) if isinstance(payload, NewTikTokVideoPayload)]


@override_settings(SERPBASE_API_KEY="test-key")
@mock.patch("sources.connectors.tiktok.client.sleep_with_heartbeat", lambda *_: None)
class TikTokPollTests(SimpleTestCase):
    def test_maps_a_post_to_the_canonical_payload(self):
        got = poll((200, envelope([post("1")])))

        self.assertEqual(len(got), 1)
        payload = got[0]
        self.assertEqual(payload.external_id, "1")
        self.assertEqual(payload.kind, NewTikTokVideoPayload.PAYLOAD_KIND)
        self.assertEqual(payload.url, "https://www.tiktok.com/@creator1/video/1")
        self.assertEqual(payload.author, "Creator 1")
        self.assertEqual(payload.handle, "creator1")
        self.assertEqual(payload.source_slug(), "creator1")
        self.assertEqual(payload.hashtags, "sociallistening, tools")
        self.assertEqual(payload.source_query, "social listening")
        self.assertEqual(payload.metrics["views_count"], 4500)
        self.assertEqual(payload.occurred_at, NOW)

    def test_the_request_carries_the_key_the_query_and_the_count(self):
        api = _FakeApi((200, envelope([post("1")])))
        connector = TikTokSearchConnector()
        connector._client = SerpBaseClient(_transport=httpx.MockTransport(api))

        list(connector.poll(TikTokSearchSourceSpec(query="open source", count=7), since=None))

        request = api.requests[0]
        self.assertEqual(request.headers["X-API-Key"], "test-key")
        body = json.loads(request.content)
        self.assertEqual(body, {"q": "open source", "count": 7})

    def test_posts_at_or_before_the_watermark_are_skipped(self):
        since = NOW - timedelta(hours=1)
        got = poll(
            (200, envelope([post("old", published=since), post("new", published=NOW)])),
            since=since,
        )
        self.assertEqual([p.external_id for p in got], ["new"])

    def test_a_post_without_a_parsed_published_at_is_skipped_not_raised(self):
        got = poll((200, envelope([post("1", published_at="not-a-time"), post("2")])))
        self.assertEqual([p.external_id for p in got], ["2"])

    def test_an_off_platform_url_is_dropped_rather_than_trusted(self):
        got = poll((200, envelope([post("1", url="https://evil.example/x", link="")])))
        self.assertEqual(got[0].url, "")

    def test_a_url_query_string_is_stripped(self):
        got = poll((200, envelope([post("1", url="https://www.tiktok.com/@a/video/1?is_from_webapp=1")])))
        self.assertEqual(got[0].url, "https://www.tiktok.com/@a/video/1")


@override_settings(SERPBASE_API_KEY="test-key")
@mock.patch("sources.connectors.tiktok.client.sleep_with_heartbeat", lambda *_: None)
class TikTokEnvelopeTests(SimpleTestCase):
    def test_http_200_with_a_business_error_status_raises(self):
        # 1001 = bad key. A 200 must not be read as "no matches".
        with self.assertRaisesRegex(ConnectorParseError, "status 1001.*not valid"):
            poll((200, envelope([], status=1001)))

    def test_a_non_json_body_raises(self):
        connector = TikTokSearchConnector()
        connector._client = SerpBaseClient(
            _transport=httpx.MockTransport(lambda request: httpx.Response(200, text="<html>"))
        )
        with self.assertRaisesRegex(ConnectorParseError, "non-JSON"):
            list(connector.poll(TikTokSearchSourceSpec(query="q"), since=None))

    def test_a_reply_without_posts_raises(self):
        connector = TikTokSearchConnector()
        connector._client = SerpBaseClient(
            _transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"status": 0}))
        )
        with self.assertRaisesRegex(ConnectorParseError, "no `posts`"):
            list(connector.poll(TikTokSearchSourceSpec(query="q"), since=None))

    def test_a_retryable_business_status_is_retried_then_succeeds(self):
        got = poll((200, envelope([], status=1029)), (200, envelope([post("1")])))
        self.assertEqual([p.external_id for p in got], ["1"])

    def test_a_retryable_status_fails_the_source_after_the_retry_budget(self):
        with self.assertRaisesRegex(ConnectorParseError, "rate limited"):
            poll((200, envelope([], status=1029)), (200, envelope([], status=1029)), (200, envelope([], status=1029)))

    def test_http_429_is_retried(self):
        got = poll((429, {}), (200, envelope([post("1")])))
        self.assertEqual([p.external_id for p in got], ["1"])

    def test_a_missing_key_refuses_before_any_request(self):
        connector = TikTokSearchConnector()
        connector._client = SerpBaseClient(_transport=httpx.MockTransport(lambda request: httpx.Response(500)))
        with (
            override_settings(SERPBASE_API_KEY=""),
            self.assertRaisesRegex(ConnectorParseError, "SERPBASE_API_KEY"),
        ):
            list(connector.poll(TikTokSearchSourceSpec(query="q"), since=None))

    def test_the_connector_declares_its_required_setting(self):
        self.assertEqual(list(TikTokSearchConnector.required_settings), ["SERPBASE_API_KEY"])


class TikTokSpecTests(SimpleTestCase):
    def test_the_query_is_required_and_normalized(self):
        self.assertEqual(TikTokSearchSourceSpec(query="  open source  ").query, "open source")
        for blank in ("", "   ", "\n"):
            with self.assertRaises(ValueError, msg=repr(blank)):
                TikTokSearchSourceSpec(query=blank)

    def test_the_count_is_bounded(self):
        self.assertEqual(TikTokSearchSourceSpec(query="q").count, 16)
        for bad in (0, 31):
            with self.assertRaises(ValueError, msg=bad):
                TikTokSearchSourceSpec(query="q", count=bad)

    def test_display_names_the_query(self):
        self.assertEqual(TikTokSearchSourceSpec(query="ai agents").display(), 'TikTok search: "ai agents"')

    def test_the_sample_payload_is_distinct_per_variant(self):
        first, second = NewTikTokVideoPayload.sample(0), NewTikTokVideoPayload.sample(1)
        self.assertNotEqual(first.external_id, second.external_id)
        self.assertNotEqual(first.url, second.url)


class TikTokTimestampTests(SimpleTestCase):
    def test_a_zulu_timestamp_parses_as_utc(self):
        self.assertEqual(parse_timestamp("2026-04-01T23:30:56Z"), datetime(2026, 4, 1, 23, 30, 56, tzinfo=UTC))

    def test_garbage_is_none(self):
        for raw in (None, "", "yesterday", 1_700_000_000):
            self.assertIsNone(parse_timestamp(raw), raw)
