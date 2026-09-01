"""Twitter search connector tests (offline, fake twikit results).

The connector's only I/O is the twikit client (`TwikitClient.search`); these
tests swap in a fake result iterator and pin: spec validation (the blank-query
firehose guard), the watermark filter, the lang filter, error translation
(TwitterErrorWrapper -> ConnectorParseError), and payload mapping (a duck-
typed fake Tweet -> NewTweetPayload).
"""

import time
from datetime import UTC, datetime
from unittest import mock

from django.test import SimpleTestCase
from pydantic import ValidationError
from twikit.errors import NotFound

from openmagpie_schema.configs import TwitterSearchSourceSpec
from sources.connectors.base import ConnectorParseError
from sources.connectors.twitter.client import TwitterErrorWrapper
from sources.connectors.twitter.connector import TwitterSearchConnector
from sources.connectors.twitter.errors import TwitterError, map_twikit_error
from sources.connectors.twitter.payloads import NewTweetPayload


class _FakeUser:
    def __init__(self, handle: str, name: str = "Some User"):
        self.id = "987654321"
        self.screen_name = handle
        self.username = handle
        self.name = name


class _FakeTweet:
    def __init__(
        self,
        tweet_id: str,
        handle: str = "alice",
        text: str = "hello from x",
        created: datetime | None = None,
        lang: str = "en",
    ):
        self.id = tweet_id
        self.user = _FakeUser(handle)
        self.full_text = text
        self.created_at_datetime: datetime | None = created or datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
        self.created_at: datetime | None = self.created_at_datetime
        self.lang = lang
        self.favorite_count = 10
        self.retweet_count = 2
        self.reply_count = 1
        self.quote_count = 0
        self.view_count = 100
        self.media = []
        self.in_reply_to = None
        self.quote = None
        self.retweeted_tweet = None


class TwitterSearchSourceSpecTests(SimpleTestCase):
    def test_blank_query_rejected(self):
        with self.assertRaises(ValidationError):
            TwitterSearchSourceSpec(kind="twitter_search", query="   ")

    def test_count_bounds(self):
        with self.assertRaises(ValidationError):
            TwitterSearchSourceSpec(kind="twitter_search", query="x", count=0)
        with self.assertRaises(ValidationError):
            TwitterSearchSourceSpec(kind="twitter_search", query="x", count=101)

    def test_defaults(self):
        spec = TwitterSearchSourceSpec(kind="twitter_search", query="social listening")
        self.assertEqual(spec.mode, "latest")
        self.assertEqual(spec.count, 20)
        self.assertEqual(spec.lang, "")


class TwitterSearchConnectorTests(SimpleTestCase):
    def _connector(self, results):
        client = mock.Mock()
        client.search.return_value = results
        conn = TwitterSearchConnector()
        conn._client = client
        return conn, client

    def test_yields_payloads_newer_than_since(self):
        spec = TwitterSearchSourceSpec(kind="twitter_search", query='"social listening"')
        tweets = [
            _FakeTweet("2", text="newer"),
            _FakeTweet("1", text="older", created=datetime(2026, 5, 1, 12, 0, tzinfo=UTC)),
        ]
        conn, client = self._connector(tweets)
        payloads = list(conn.poll(spec, since=datetime(2026, 5, 15, tzinfo=UTC)))
        self.assertEqual(len(payloads), 1)
        self.assertEqual(payloads[0].external_id, "2")
        client.search.assert_called_once_with('"social listening"', "Latest", 20, heartbeat=None)

    def test_watermark_boundary_yields(self):
        """A tweet AT the watermark re-yields (strict `<`), so a same-second
        sibling arriving in a later cycle isn't stranded; external_id dedup
        absorbs the re-yield. Dropping on `==` would lose it permanently."""
        at = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
        spec = TwitterSearchSourceSpec(kind="twitter_search", query="x")
        conn, _ = self._connector([_FakeTweet("boundary", created=at)])
        payloads = list(conn.poll(spec, since=at))
        self.assertEqual([p.external_id for p in payloads], ["boundary"])

    def test_lang_filter(self):
        spec = TwitterSearchSourceSpec(kind="twitter_search", query="x", lang="es")
        tweets = [_FakeTweet("1", lang="en"), _FakeTweet("2", lang="es")]
        conn, _ = self._connector(tweets)
        payloads = list(conn.poll(spec, since=None))
        self.assertEqual([p.external_id for p in payloads], ["2"])

    def test_error_maps_to_connector_parse_error(self):
        spec = TwitterSearchSourceSpec(kind="twitter_search", query="x")
        err = TwitterError(code="rate_limited", message="slow down", retryable=True, action="backoff")
        client = mock.Mock()
        client.search.side_effect = TwitterErrorWrapper(err)
        conn = TwitterSearchConnector()
        conn._client = client
        with self.assertRaises(ConnectorParseError) as ctx:
            list(conn.poll(spec, since=None))
        self.assertIn("rate_limited", str(ctx.exception))

    def test_empty_404_maps_to_retryable_connector_error(self):
        """X SearchTimeline empty-404 is a transient flake, not a dead tweet."""
        spec = TwitterSearchSourceSpec(kind="twitter_search", query="x")
        # twikit renders an empty-body 404 as 'status: 404, message: ""' (see
        # client/client.py: message = f'status: {code}, message: "{text}"').
        err = map_twikit_error(NotFound('status: 404, message: ""'))
        client = mock.Mock()
        client.search.side_effect = TwitterErrorWrapper(err)
        conn = TwitterSearchConnector()
        conn._client = client
        with self.assertRaises(ConnectorParseError) as ctx:
            list(conn.poll(spec, since=None))
        self.assertIn("search_timeline_unavailable", str(ctx.exception))

    def test_mode_top_maps_to_twikit_top(self):
        spec = TwitterSearchSourceSpec(kind="twitter_search", query="x", mode="top")
        conn, client = self._connector([_FakeTweet("1")])
        list(conn.poll(spec, since=None))
        client.search.assert_called_once_with("x", "Top", 20, heartbeat=None)


class MapTwikitErrorTests(SimpleTestCase):
    """map_twikit_error: twikit's NotFound rendering vs the empty-404 flake."""

    def test_empty_body_404_maps_to_retryable(self):
        """An empty-body 404 is the transient flake: retryable, not not_found."""
        err = map_twikit_error(NotFound('status: 404, message: ""'))
        self.assertEqual(err.code, "search_timeline_unavailable")
        self.assertTrue(err.retryable)
        self.assertIn("retry with backoff", err.action)

    def test_message_404_stays_non_retryable_not_found(self):
        """A message-bearing 404 is a real not_found, not the flake."""
        err = map_twikit_error(NotFound('status: 404, message: "This tweet does not exist"'))
        self.assertEqual(err.code, "not_found")
        self.assertFalse(err.retryable)

    def test_rate_limit_reset_extracted_from_header(self):
        """A real TooManyRequests carries x-rate-limit-reset; map_twikit_error
        must surface it as the int the retry-delay math consumes (not a str)."""
        from twikit.errors import TooManyRequests

        from sources.connectors.twitter.client import _reset_delay
        from sources.connectors.twitter.errors import RATE_LIMITED

        exc = TooManyRequests("rate limited", headers={"x-rate-limit-reset": "1788888888"})
        err = map_twikit_error(exc)
        self.assertEqual(err.code, RATE_LIMITED)
        self.assertTrue(err.retryable)
        self.assertEqual(err.rate_limit_reset, 1788888888)
        # The delay math must accept it without raising (int - float).
        self.assertIsInstance(_reset_delay(err.rate_limit_reset, 0), float)


class RateLimitRetryTests(SimpleTestCase):
    """The client's rate-limit retry loop: wait on `rate_limited` (honoring the
    reset epoch) and retry, but pass other errors straight through."""

    def _rate_limited(self, reset=None):
        err = TwitterError(code="rate_limited", message="slow down", retryable=True, action="wait")
        err.rate_limit_reset = reset
        return TwitterErrorWrapper(err)

    def test_retries_then_succeeds(self):
        from sources.connectors.twitter.client import TwikitClient

        client = TwikitClient(cookies={"auth_token": "a", "ct0": "c"})
        calls = {"n": 0}

        def fake_search(q, m, c):
            calls["n"] += 1
            if calls["n"] < 3:
                raise self._rate_limited()
            return ["ok"]

        with (
            mock.patch.object(client, "_run_search", side_effect=fake_search),
            mock.patch("sources.connectors.twitter.client.sleep_with_heartbeat") as sleep,
        ):
            self.assertEqual(client.search("q"), ["ok"])
        self.assertEqual(calls["n"], 3)
        self.assertEqual(sleep.call_count, 2)

    def test_gives_up_after_max_retries(self):
        from sources.connectors.twitter.client import MAX_RATE_LIMIT_RETRIES, TwikitClient

        client = TwikitClient(cookies={"auth_token": "a", "ct0": "c"})
        with (
            mock.patch.object(client, "_run_search", side_effect=self._rate_limited()),
            mock.patch("sources.connectors.twitter.client.sleep_with_heartbeat") as sleep,
            self.assertRaises(TwitterErrorWrapper),
        ):
            client.search("q")
        self.assertEqual(sleep.call_count, MAX_RATE_LIMIT_RETRIES)

    def test_non_rate_limit_error_not_retried(self):
        from sources.connectors.twitter.client import TwikitClient

        err = TwitterError(code="forbidden", message="no", retryable=False, action="rotate")
        client = TwikitClient(cookies={"auth_token": "a", "ct0": "c"})
        with (
            mock.patch.object(client, "_run_search", side_effect=TwitterErrorWrapper(err)),
            mock.patch("sources.connectors.twitter.client.sleep_with_heartbeat") as sleep,
            self.assertRaises(TwitterErrorWrapper),
        ):
            client.search("q")
        sleep.assert_not_called()

    def test_delay_honors_reset_epoch(self):
        from sources.connectors.twitter.client import _reset_delay

        # A reset ~30s in the future is used as the wait; a past reset falls back.
        soon = int(time.time()) + 30
        self.assertGreater(_reset_delay(soon, 0), 20)
        self.assertLessEqual(_reset_delay(soon, 0), 60)
        past = int(time.time()) - 100
        self.assertEqual(_reset_delay(past, 0), 5.0)  # base backoff, attempt 0


class NewTweetPayloadTests(SimpleTestCase):
    def test_missing_created_at_raises_instead_of_now(self):
        """A timestamp-less tweet must not mint a synthetic now() (it would
        advance the watermark past every older-but-new tweet)."""
        bad = _FakeTweet("9")
        bad.created_at_datetime = None
        bad.created_at = None
        with self.assertRaises(ValueError):
            NewTweetPayload.from_tweet(bad)

    def test_connector_skips_unmappable_tweet_and_keeps_the_rest(self):
        bad = _FakeTweet("9")
        bad.created_at_datetime = None
        bad.created_at = None
        spec = TwitterSearchSourceSpec(kind="twitter_search", query="x")
        client = mock.Mock()
        client.search.return_value = [bad, _FakeTweet("10")]
        conn = TwitterSearchConnector()
        conn._client = client
        payloads = list(conn.poll(spec, since=None))
        self.assertEqual([p.external_id for p in payloads], ["10"])

    def test_from_tweet(self):
        p = NewTweetPayload.from_tweet(_FakeTweet("123", handle="alice", text="hi"))
        self.assertEqual(p.external_id, "123")
        self.assertEqual(p.handle, "alice")
        self.assertEqual(p.content, "hi")
        self.assertEqual(p.source, "twitter_search")
        self.assertEqual(p.url, "https://x.com/alice/status/123")
        self.assertEqual(p.metrics["likes"], 10)
        self.assertEqual(p.refs["in_reply_to"], None)

    def test_sample_distinct(self):
        a = NewTweetPayload.sample(0)
        b = NewTweetPayload.sample(1)
        self.assertNotEqual(a.external_id, b.external_id)
        self.assertEqual(a.PAYLOAD_KIND, "new_tweet")
