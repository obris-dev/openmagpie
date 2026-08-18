"""Facebook group search connector tests (offline, fake worker results).

The connector's only I/O is the FacebookClient subprocess call; these tests
swap in a fake client result and pin: spec validation (the empty-group-ids
guard), the watermark filter, error translation (FacebookError -> ConnectorParseError),
and payload mapping (a fake normalized record -> NewFacebookPostPayload).
"""

from datetime import UTC, datetime
from unittest import mock

from django.test import SimpleTestCase
from pydantic import ValidationError

from openmagpie_schema.configs import FacebookGroupSourceSpec
from sources.connectors.base import ConnectorParseError
from sources.connectors.facebook.client import FacebookClient
from sources.connectors.facebook.connector import FacebookGroupConnector
from sources.connectors.facebook.errors import FacebookError
from sources.connectors.facebook.payloads import NewFacebookPostPayload


class _FakeGroupPost:
    """A normalized Facebook post record, matching the facebook-worker.py shape."""

    def __init__(
        self,
        post_id: str,
        group_id: str = "group_1",
        author_name: str = "Alice",
        content: str = "Hello from Facebook",
        created: datetime | None = None,
        likes: int | None = 10,
        comments: int | None = 2,
        shares: int | None = 1,
    ):
        self._record = {
            "external_id": post_id,
            "group_id": group_id,
            "author": {"name": author_name},
            "content": content,
            "occurred_at": (created or datetime(2026, 6, 1, 12, 0, tzinfo=UTC)).isoformat(),
            "url": f"https://facebook.com/groups/{group_id}/posts/{post_id}",
            "metrics": {
                "likes": likes,
                "comments": comments,
                "shares": shares,
            },
            "matched_terms": ["saas"],
        }

    def dict(self) -> dict:
        return dict(self._record)


class FacebookGroupSourceSpecTests(SimpleTestCase):
    def test_empty_group_ids_rejected(self):
        with self.assertRaises(ValidationError):
            FacebookGroupSourceSpec(kind="facebook_group", group_ids=[])

    def test_group_ids_whitespace_only_rejected(self):
        with self.assertRaises(ValidationError):
            FacebookGroupSourceSpec(kind="facebook_group", group_ids=["   "])

    def test_minimal_spec(self):
        spec = FacebookGroupSourceSpec(kind="facebook_group", group_ids=["12345"])
        self.assertEqual(spec.group_ids, ["12345"])
        self.assertEqual(spec.terms, [])
        self.assertEqual(spec.count, 20)

    def test_full_spec(self):
        spec = FacebookGroupSourceSpec(
            kind="facebook_group",
            group_ids=["12345", "67890"],
            terms=["saas", "fundraising"],
            count=50,
        )
        self.assertEqual(spec.group_ids, ["12345", "67890"])
        self.assertEqual(spec.terms, ["saas", "fundraising"])
        self.assertEqual(spec.count, 50)

    def test_terms_whitespace_stripped(self):
        spec = FacebookGroupSourceSpec(
            kind="facebook_group",
            group_ids=["12345"],
            terms=["  saas  ", "fundraising  "],
        )
        self.assertEqual(spec.terms, ["saas", "fundraising"])

    def test_count_bounds(self):
        with self.assertRaises(ValidationError):
            FacebookGroupSourceSpec(kind="facebook_group", group_ids=["x"], count=0)
        with self.assertRaises(ValidationError):
            FacebookGroupSourceSpec(kind="facebook_group", group_ids=["x"], count=101)

    def test_display(self):
        spec = FacebookGroupSourceSpec(kind="facebook_group", group_ids=["12345", "67890"])
        self.assertIn("12345", spec.display())


class FacebookGroupConnectorTests(SimpleTestCase):
    def _connector(self, worker_result: dict):
        client = mock.Mock(spec=FacebookClient)
        client.search_group.return_value = worker_result
        conn = FacebookGroupConnector()
        conn._client = client  # inject the fake before first poll
        return conn, client

    def test_yields_payloads_newer_than_since(self):
        spec = FacebookGroupSourceSpec(kind="facebook_group", group_ids=["123"])
        newer = _FakeGroupPost("2", created=datetime(2026, 6, 15, 12, 0, tzinfo=UTC))
        older = _FakeGroupPost("1", created=datetime(2026, 5, 1, 12, 0, tzinfo=UTC))
        worker_result = {
            "ok": True,
            "result": {
                "results": [newer.dict(), older.dict()],
                "cursor": {},
                "matched_terms": [],
                "events": ["groups.search_completed"],
            },
            "new_cookies": [],
        }
        conn, client = self._connector(worker_result)
        payloads = list(conn.poll(spec, since=datetime(2026, 5, 15, tzinfo=UTC)))
        self.assertEqual(len(payloads), 1)
        self.assertEqual(payloads[0].external_id, "2")
        client.search_group.assert_called_once_with(["123"], [], 20)

    def test_all_payloads_when_no_since(self):
        spec = FacebookGroupSourceSpec(kind="facebook_group", group_ids=["123"])
        p1 = _FakeGroupPost("1")
        p2 = _FakeGroupPost("2")
        worker_result = {
            "ok": True,
            "result": {
                "results": [p1.dict(), p2.dict()],
                "cursor": {},
                "matched_terms": [],
                "events": ["groups.search_completed"],
            },
            "new_cookies": [],
        }
        conn, _ = self._connector(worker_result)
        payloads = list(conn.poll(spec, since=None))
        self.assertEqual(len(payloads), 2)

    def test_terms_passed_to_client(self):
        spec = FacebookGroupSourceSpec(
            kind="facebook_group", group_ids=["123"], terms=["saas", "fundraising"]
        )
        worker_result = {
            "ok": True,
            "result": {"results": [], "cursor": {}, "matched_terms": [], "events": ["groups.search_completed"]},
            "new_cookies": [],
        }
        conn, client = self._connector(worker_result)
        list(conn.poll(spec, since=None))
        client.search_group.assert_called_once_with(["123"], ["saas", "fundraising"], 20)

    def test_worker_error_maps_to_connector_parse_error(self):
        spec = FacebookGroupSourceSpec(kind="facebook_group", group_ids=["123"])
        err = FacebookError(
            code="worker_error",
            message="worker failed",
            retryable=True,
            action="retry",
        )
        client = mock.Mock(spec=FacebookClient)
        client.search_group.side_effect = err
        conn = FacebookGroupConnector()
        conn._client = client
        with self.assertRaises(ConnectorParseError) as ctx:
            list(conn.poll(spec, since=None))
        self.assertIn("worker_error", str(ctx.exception))

    def test_empty_results_is_not_an_error(self):
        spec = FacebookGroupSourceSpec(kind="facebook_group", group_ids=["123"])
        worker_result = {
            "ok": True,
            "result": {"results": [], "cursor": {}, "matched_terms": [], "events": ["groups.search_completed"]},
            "new_cookies": [],
        }
        conn, _ = self._connector(worker_result)
        payloads = list(conn.poll(spec, since=None))
        self.assertEqual(payloads, [])


class NewFacebookPostPayloadTests(SimpleTestCase):
    def test_from_record(self):
        record = {
            "external_id": "123",
            "group_id": "group_1",
            "author": {"name": "Alice"},
            "content": "Hello from Facebook",
            "occurred_at": "2026-06-01T12:00:00+00:00",
            "url": "https://facebook.com/groups/group_1/posts/123",
            "metrics": {"likes": 10, "comments": 2, "shares": 1},
            "matched_terms": ["saas"],
        }
        p = NewFacebookPostPayload.from_record(record, query_terms=["saas", "fundraising"])
        self.assertEqual(p.external_id, "123")
        self.assertEqual(p.group_id, "group_1")
        self.assertEqual(p.author, "Alice")
        self.assertEqual(p.content, "Hello from Facebook")
        self.assertEqual(p.source, "facebook_group")
        self.assertEqual(p.url, "https://facebook.com/groups/group_1/posts/123")
        self.assertEqual(p.metrics["likes"], 10)
        self.assertEqual(p.metrics["comments"], 2)
        self.assertEqual(p.metrics["shares"], 1)
        # The record's own matched_terms win; query_terms is only the fallback
        # when a record carries none.
        self.assertEqual(p.matched_terms, ["saas"])

    def test_from_record_author_as_string(self):
        record = {
            "external_id": "456",
            "group_id": "group_2",
            "author": "Bob",
            "content": "Another post",
            "occurred_at": "2026-06-02T12:00:00+00:00",
            "metrics": {"likes": 5, "comments": 0, "shares": 0},
        }
        p = NewFacebookPostPayload.from_record(record)
        self.assertEqual(p.author, "Bob")
        self.assertEqual(p.matched_terms, [])

    def test_from_record_missing_optional_fields(self):
        record = {
            "external_id": "789",
            "group_id": "group_3",
            "content": "Minimal post",
        }
        p = NewFacebookPostPayload.from_record(record)
        self.assertEqual(p.external_id, "789")
        self.assertEqual(p.group_id, "group_3")
        self.assertEqual(p.author, "")
        self.assertEqual(p.metrics, {"likes": None, "comments": None, "shares": None})
        self.assertEqual(p.matched_terms, [])

    def test_from_record_uses_query_terms_as_fallback(self):
        record = {
            "external_id": "abc",
            "group_id": "group_5",
            "content": "Fallback terms",
        }
        p = NewFacebookPostPayload.from_record(record, query_terms=["saas", "fundraising"])
        self.assertEqual(p.matched_terms, ["saas", "fundraising"])

    def test_from_record_url_built_if_missing(self):
        record = {
            "external_id": "abc",
            "group_id": "group_4",
            "content": "Post with generated URL",
        }
        p = NewFacebookPostPayload.from_record(record)
        self.assertEqual(p.url, "https://facebook.com/groups/group_4/posts/abc")

    def test_sample_distinct(self):
        a = NewFacebookPostPayload.sample(0)
        b = NewFacebookPostPayload.sample(1)
        self.assertNotEqual(a.external_id, b.external_id)
        self.assertEqual(a.PAYLOAD_KIND, "new_fb_post")

    def test_source_slug_returns_group_id(self):
        p = NewFacebookPostPayload.sample(0)
        self.assertEqual(p.source_slug(), p.group_id)

    def test_source_slug_none_when_group_id_empty(self):
        p = NewFacebookPostPayload(
            kind="new_fb_post",
            external_id="1",
            source="facebook_group",
            occurred_at=datetime(2026, 1, 1, tzinfo=UTC),
            title="",
            content="",
            group_id="",
        )
        self.assertIsNone(p.source_slug())
