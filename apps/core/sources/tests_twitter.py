"""Twitter search connector tests (offline, fake twikit results).

The connector's only I/O is the twikit client (`TwikitClient.search`); these
tests swap in a fake result iterator and pin: spec validation (the blank-query
firehose guard), the watermark filter, the lang filter, error translation
(ListenerErrorWrapper -> ConnectorParseError), and payload mapping (a duck-
typed fake Tweet -> NewTweetPayload).
"""

from datetime import UTC, datetime
from unittest import mock

from django.test import SimpleTestCase
from pydantic import ValidationError
from twikit.errors import NotFound

from openmagpie_schema.configs import TwitterSearchSourceSpec
from sources.connectors.base import ConnectorParseError
from sources.connectors.twitter.client import ListenerErrorWrapper
from sources.connectors.twitter.connector import TwitterSearchConnector
from sources.connectors.twitter.errors import ListenerError, map_twikit_error
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
        self.created_at_datetime = created or datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
        self.created_at = self.created_at_datetime
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
        client.search.assert_called_once_with('"social listening"', "Latest", 20)

    def test_lang_filter(self):
        spec = TwitterSearchSourceSpec(kind="twitter_search", query="x", lang="es")
        tweets = [_FakeTweet("1", lang="en"), _FakeTweet("2", lang="es")]
        conn, _ = self._connector(tweets)
        payloads = list(conn.poll(spec, since=None))
        self.assertEqual([p.external_id for p in payloads], ["2"])

    def test_error_maps_to_connector_parse_error(self):
        spec = TwitterSearchSourceSpec(kind="twitter_search", query="x")
        err = ListenerError(code="rate_limited", message="slow down", retryable=True, action="backoff")
        client = mock.Mock()
        client.search.side_effect = ListenerErrorWrapper(err)
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
        client.search.side_effect = ListenerErrorWrapper(err)
        conn = TwitterSearchConnector()
        conn._client = client
        with self.assertRaises(ConnectorParseError) as ctx:
            list(conn.poll(spec, since=None))
        self.assertIn("search_timeline_unavailable", str(ctx.exception))

    def test_mode_top_maps_to_twikit_top(self):
        spec = TwitterSearchSourceSpec(kind="twitter_search", query="x", mode="top")
        conn, client = self._connector([_FakeTweet("1")])
        list(conn.poll(spec, since=None))
        client.search.assert_called_once_with("x", "Top", 20)


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


class NewTweetPayloadTests(SimpleTestCase):
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
