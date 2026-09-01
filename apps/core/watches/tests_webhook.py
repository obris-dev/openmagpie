from datetime import timedelta
from unittest import mock

import httpx
import ulid
from django.test import TestCase
from django.utils import timezone

from openmagpie_schema.watch_enums import DeliveryCadence, WatchActionRunState
from watches import run_messages
from watches.actions.protocol import ActionContext, ActionItem, ActionResult, OutboundActionResult
from watches.actions.webhook import WebhookAction
from watches.models import WatchAction


class WebhookRunTests(TestCase):
    """WebhookAction HTTP-status classification + the unified payload shape (no
    DB needed: load_config is pure shape validation on the in-memory action)."""

    def _run_with_status(self, status: int) -> ActionResult:
        action = WatchAction(id=ulid.ulid(), kind="webhook", config={"url": "https://h.example.com/hook"})
        req = httpx.Request("POST", "https://h.example.com/hook")
        resp = httpx.Response(status, headers={"Location": "https://elsewhere.example/x"}, request=req)
        item = ActionItem(data={"source": "x", "external_id": "1"}, key="x:1", source_label="x", source_kind="x")
        context = ActionContext(watch_id="w", watch_name="n", delivery=DeliveryCadence.INSTANT)
        # follow_redirects stays off, so a 3xx is a returned response, not a hop.
        with (
            mock.patch("watches.actions.webhook.destination_block_reason", return_value=None),
            mock.patch("watches.actions.webhook.httpx.request", return_value=resp),
        ):
            return WebhookAction().run(action, items=[item], context=context)

    def test_redirect_is_permanent_error_not_transient(self) -> None:
        # A 3xx never reaches the receiver and re-redirects on retry, so it's a
        # permanent misconfig -> ERRORED (not FAILED, not SUCCEEDED). The failed
        # attempt is still recorded: the OutboundCall carries the 302.
        result = self._run_with_status(302)
        self.assertEqual(result.state, WatchActionRunState.ERRORED)
        self.assertEqual(result.error, run_messages.WEBHOOK_REDIRECT)
        self.assertEqual(result.result["http_status"], 302)
        assert isinstance(result, OutboundActionResult)
        self.assertEqual(result.outbound.http_status, 302)

    def test_transient_status_is_failed_with_call(self) -> None:
        # A 5xx is transient -> FAILED (retryable), and the attempt is logged
        # with its status on the OutboundCall.
        result = self._run_with_status(503)
        self.assertEqual(result.state, WatchActionRunState.FAILED)
        assert isinstance(result, OutboundActionResult)
        self.assertEqual(result.outbound.http_status, 503)

    def test_success_carries_http_status(self) -> None:
        result = self._run_with_status(200)
        self.assertEqual(result.state, WatchActionRunState.SUCCEEDED)
        assert isinstance(result, OutboundActionResult)
        self.assertEqual(result.outbound.http_status, 200)
        self.assertEqual(result.result["http_status"], 200)

    def _capture_request(self, action: WatchAction, item: ActionItem, context: ActionContext) -> dict:
        """Run with a mocked transport, returning the captured request kwargs
        ({method, json, ...}) so a test can assert the wire shape."""
        captured: dict = {}
        req = httpx.Request("POST", "https://h.example.com/hook")
        resp = httpx.Response(200, request=req)

        def fake_request(method, url, **kwargs):
            captured.update(method=method, url=url, **kwargs)
            return resp

        with (
            mock.patch("watches.actions.webhook.destination_block_reason", return_value=None),
            mock.patch("watches.actions.webhook.httpx.request", side_effect=fake_request),
        ):
            WebhookAction().run(action, items=[item], context=context)
        return captured

    def test_payload_is_self_describing(self) -> None:
        # The unified body carries watch ref, cadence, the digest window, and
        # per-item key/source plus the include_fields-filtered item.
        action = WatchAction(
            id=ulid.ulid(),
            kind="webhook",
            config={"url": "https://h.example.com/hook", "include_fields": ["title"]},
        )
        item = ActionItem(
            data={"source": "reddit", "external_id": "abc", "title": "T", "url": "U"},
            key="reddit:abc",
            source_label="r/ClaudeAI",
            source_kind="reddit_subreddit",
        )
        since = timezone.now()
        context = ActionContext(
            watch_id="w1",
            watch_name="ai-webhook",
            delivery=DeliveryCadence.DIGEST,
            window_since=since,
            window_until=since + timedelta(seconds=3600),
        )
        body = self._capture_request(action, item, context)["json"]
        self.assertEqual(body["watch"], {"id": "w1", "name": "ai-webhook"})
        self.assertEqual(body["delivery"], "digest")
        self.assertIsNotNone(body["window"])
        (sent,) = body["items"]
        self.assertEqual(sent["key"], "reddit:abc")
        self.assertEqual(sent["source"], {"label": "r/ClaudeAI", "kind": "reddit_subreddit", "pattern_id": None})
        self.assertEqual(sent["item"], {"title": "T"})  # url dropped by include_fields

    def test_pattern_id_flows_from_source_meta(self) -> None:
        # The contract extension: operator-supplied pattern_id on the source
        # (FeedItem.source_meta, copied from Source.meta at record time) rides
        # through to the wire body's per-item source so receivers can attribute
        # yield by listening pattern. Absent meta -> pattern_id None, never a
        # missing key (the shape stays self-describing).
        action = WatchAction(id=ulid.ulid(), kind="webhook", config={"url": "https://h.example.com/hook"})
        tagged = ActionItem(
            data={"source": "twitter_search", "external_id": "t1"},
            key="twitter_search:t1",
            source_label="automation.manually",
            source_kind="twitter_search",
            source_meta={"pattern_id": "automation.manually", "lane": "AUTOMATION_BUILD"},
        )
        untagged = ActionItem(
            data={"source": "twitter_search", "external_id": "t2"},
            key="twitter_search:t2",
            source_label="general-listener",
            source_kind="twitter_search",
        )
        context = ActionContext(watch_id="w", watch_name="n", delivery=DeliveryCadence.INSTANT)
        body = self._capture_request(action, tagged, context)["json"]
        (tagged_sent,) = body["items"]
        self.assertEqual(tagged_sent["source"]["pattern_id"], "automation.manually")
        body = self._capture_request(action, untagged, context)["json"]
        (untagged_sent,) = body["items"]
        self.assertEqual(untagged_sent["source"]["pattern_id"], None)
        self.assertEqual(
            untagged_sent["source"], {"label": "general-listener", "kind": "twitter_search", "pattern_id": None}
        )

    def test_method_is_dispatched(self) -> None:
        # A configured PUT is the verb actually sent (not hard-coded POST).
        action = WatchAction(
            id=ulid.ulid(), kind="webhook", config={"url": "https://h.example.com/hook", "method": "PUT"}
        )
        item = ActionItem(data={"source": "x", "external_id": "1"}, key="x:1", source_label="x", source_kind="x")
        context = ActionContext(watch_id="w", watch_name="n", delivery=DeliveryCadence.INSTANT)
        self.assertEqual(self._capture_request(action, item, context)["method"], "PUT")
