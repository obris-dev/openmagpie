"""`POST /v1/watches/<id>/connect-bower`: the OpenBower create it makes, the
bower action it adds, and that the key is kept nowhere."""

import io
import json
import logging
from collections.abc import Callable
from typing import Any
from unittest import mock

import httpx
import ulid
from django.test import TestCase
from rest_framework.test import APIClient

from accounts.services import AccountService
from auth_api.operations.signup import SignupOperation
from feeds.models import Feed, Source
from watches.models import WatchAction, WatchFeed
from watches.policy import PolicyError

API_URL = "https://api.example.io"
KEY = "ob_live_secretkey"
WEBHOOK_URL = "https://api.example.io/v1/signals/01SIG/ingest/tok_secret"

type Answer = httpx.Response | Exception | Callable[[dict], httpx.Response]


def _created(columns: list[dict]) -> dict:
    """OpenBower's create answer: keys derived from the labels sent, unlike
    Magpie's field names, so a map keyed on the wrong side shows."""
    made = [
        {"key": "f_" + c["label"].lower().replace(" ", "_"), "label": c["label"], "type": c["type"], "kind": "data"}
        for c in columns
    ]
    return {
        "sheet": {"id": "01SIG", "label": "Mentions", "columns": made},
        "type": "custom",
        "webhook_url": WEBHOOK_URL,
    }


class _WatchWithSources(TestCase):
    """A signed-in account's watch over one reddit source."""

    def setUp(self) -> None:
        self.user = SignupOperation(email="bower@example.com", password="Str0ng-Passw0rd!").run()
        self.account_id = AccountService.Global.primary_account_id_for(user_id=str(self.user.id))
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        feed = Feed.objects.create(
            account_id=self.account_id, user_id=str(self.user.id), name="f", kind="curated", data={}
        )
        Source.objects.create(
            id=str(ulid.ulid()),
            account_id=self.account_id,
            feed_id=str(feed.id),
            kind="reddit_subreddit",
            spec={"kind": "reddit_subreddit", "subreddit": "saas"},
            spec_hash="h1",
        )
        resp = self.client.post("/v1/watches", {"name": "Mentions", "feed_ids": [str(feed.id)]}, format="json")
        self.assertEqual(resp.status_code, 201, resp.content)
        self.watch_id = resp.json()["id"]


class BowerConnectTests(_WatchWithSources):
    def _connect(self, answer: Answer, api_url: str = API_URL, **extra: Any) -> tuple[Any, dict]:
        """POST a connect with OpenBower's side faked by `answer`: a response,
        an exception to raise, or a function of what was sent. Returns the
        endpoint's response and what was sent, with the logs written."""
        sent: dict = {}

        def fake_post(url, **kwargs):
            sent.update(url=url, **kwargs)
            if isinstance(answer, Exception):
                raise answer
            if isinstance(answer, httpx.Response):
                return answer
            return answer(sent)

        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        logger = logging.getLogger("watches")
        logger.addHandler(handler)
        try:
            with (
                mock.patch("watches.services._bower_http.destination_block_reason", return_value=None),
                mock.patch("watches.services._bower_http.post", side_effect=fake_post),
            ):
                resp = self.client.post(
                    f"/v1/watches/{self.watch_id}/connect-bower",
                    {"api_url": api_url, "api_key": KEY, **extra},
                    format="json",
                )
        finally:
            logger.removeHandler(handler)
        sent["logs"] = stream.getvalue()
        return resp, sent

    def test_connect_creates_the_signal_from_the_item_fields_and_adds_the_action(self) -> None:
        def created(sent: dict) -> httpx.Response:
            return httpx.Response(201, json=_created(sent["json"]["columns"]), request=httpx.Request("POST", "x"))

        resp, sent = self._connect(created, api_url=API_URL + "/")
        self.assertEqual(resp.status_code, 201, resp.content)

        self.assertEqual(sent["url"], "https://api.example.io/v1/signals")
        self.assertEqual(sent["headers"], {"Authorization": f"Bearer {KEY}"})
        self.assertEqual(sent["json"]["type"], "custom")
        self.assertEqual(sent["json"]["label"], "Mentions")
        columns = {c["label"]: c["type"] for c in sent["json"]["columns"]}
        # The shared fields lead; reddit's own follow; plumbing never rides.
        self.assertEqual(
            [c["label"] for c in sent["json"]["columns"]][:6],
            ["Title", "Content", "Url", "External url", "Author", "Occurred at"],
        )
        self.assertEqual(columns["Url"], "url")
        # A path, not a link: typed url it would show as a link that goes nowhere.
        self.assertEqual(columns["Permalink"], "text")
        self.assertEqual(columns["Occurred at"], "datetime")
        self.assertEqual(columns["Subreddit"], "text")
        for plumbing in ("Kind", "External id", "Source", "Parent external id"):
            self.assertNotIn(plumbing, columns)

        action = WatchAction.objects.get(id=resp.json()["action"]["id"])
        self.assertEqual(action.kind, "bower")
        self.assertEqual(action.config["url"], WEBHOOK_URL)
        self.assertEqual(action.config["columns"]["f_subreddit"], "subreddit")
        self.assertEqual(action.config["columns"]["f_occurred_at"], "occurred_at")

        body = resp.json()
        self.assertFalse(body["dry_run"])
        self.assertEqual(body["signal_id"], "01SIG")
        self.assertIn(
            {"key": "f_subreddit", "label": "Subreddit", "type": "text", "field": "subreddit"}, body["columns"]
        )
        # FAILS if the key is stored or answered, or the answer shows the token.
        self.assertNotIn(KEY, json.dumps(action.config))
        self.assertNotIn(KEY, resp.content.decode())
        self.assertNotIn("tok_secret", resp.content.decode())
        self.assertNotIn(KEY, sent["logs"])

    def test_rank_places_the_action_ahead_of_an_existing_delivery(self) -> None:
        resp = self.client.post(
            f"/v1/watches/{self.watch_id}/actions", {"kind": "log", "config": {"prefix": "[x]"}}, format="json"
        )
        self.assertEqual(resp.status_code, 201, resp.content)

        def created(sent: dict) -> httpx.Response:
            return httpx.Response(201, json=_created(sent["json"]["columns"]), request=httpx.Request("POST", "x"))

        resp, _sent = self._connect(created, rank=0)
        self.assertEqual(resp.status_code, 201, resp.content)
        # FAILS if rank is dropped and the action appends after the log.
        chain = list(
            WatchAction.objects.filter(kind__in=["log", "bower"]).order_by("rank").values_list("kind", flat=True)
        )
        self.assertEqual(chain, ["bower", "log"])

    def test_an_openbower_refusal_is_relayed_and_nothing_is_added(self) -> None:
        refusal = httpx.Response(401, json={"detail": "Invalid API key."}, request=httpx.Request("POST", "x"))
        resp, sent = self._connect(refusal)
        self.assertEqual(resp.status_code, 400, resp.content)
        self.assertIn("Invalid API key.", resp.json()["detail"])
        self.assertNotIn(KEY, resp.content.decode())
        self.assertNotIn(KEY, sent["logs"])
        self.assertFalse(WatchAction.objects.filter(kind="bower").exists())

    def test_openbower_unable_to_answer_now_is_a_502(self) -> None:
        for status in (503, 429):
            with self.subTest(status=status):
                answer = httpx.Response(status, json={"detail": "busy"}, request=httpx.Request("POST", "x"))
                resp, _sent = self._connect(answer)
                # FAILS if OpenBower's own outage is reported as the request's fault.
                self.assertEqual(resp.status_code, 502, resp.content)
                self.assertIn("try again later", resp.json()["detail"])
        self.assertFalse(WatchAction.objects.filter(kind="bower").exists())

    def test_a_blocked_api_url_is_refused_before_any_call(self) -> None:
        with (
            mock.patch("watches.services._bower_http.destination_block_reason", return_value="a private address"),
            mock.patch("watches.services._bower_http.post") as post,
        ):
            resp = self.client.post(
                f"/v1/watches/{self.watch_id}/connect-bower", {"api_url": API_URL, "api_key": KEY}, format="json"
            )
        # FAILS if the SSRF check is skipped before the create.
        self.assertEqual(resp.status_code, 400, resp.content)
        self.assertIn("api_url rejected: a private address", resp.json()["detail"])
        post.assert_not_called()

    def test_openbower_down_is_a_502(self) -> None:
        resp, sent = self._connect(httpx.ConnectError("refused"))
        self.assertEqual(resp.status_code, 502, resp.content)
        self.assertNotIn(KEY, sent["logs"])
        self.assertFalse(WatchAction.objects.filter(kind="bower").exists())

    def test_a_watch_whose_feeds_have_no_sources_is_refused_before_any_call(self) -> None:
        resp = self.client.post("/v1/watches", {"name": "Empty", "feed_ids": []}, format="json")
        empty_id = resp.json()["id"]
        with mock.patch("watches.services._bower_http.post") as post:
            resp = self.client.post(
                f"/v1/watches/{empty_id}/connect-bower", {"api_url": API_URL, "api_key": KEY}, format="json"
            )
        self.assertEqual(resp.status_code, 400, resp.content)
        post.assert_not_called()

    def test_a_deleted_feed_still_subscribed_is_skipped(self) -> None:
        # A subscription the feed's delete left behind (it never removes them).
        WatchFeed.objects.create(account_id=self.account_id, watch_id=self.watch_id, feed_id=str(ulid.ulid()))
        # FAILS (a 500) if a subscription to a deleted feed is looked up as a feed.
        resp = self.client.post(
            f"/v1/watches/{self.watch_id}/connect-bower?dry_run=true",
            {"api_url": API_URL, "api_key": KEY},
            format="json",
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertIn("subreddit", [c["field"] for c in resp.json()["columns"]])

    def test_a_dry_run_names_the_columns_and_calls_openbower_not_at_all(self) -> None:
        # FAILS if the preview reaches OpenBower or adds the action.
        with mock.patch("watches.services._bower_http.post") as post:
            resp = self.client.post(
                f"/v1/watches/{self.watch_id}/connect-bower?dry_run=true",
                {"api_url": API_URL, "api_key": KEY, "label": "Forum mentions"},
                format="json",
            )
        self.assertEqual(resp.status_code, 200, resp.content)
        post.assert_not_called()
        body = resp.json()
        self.assertTrue(body["dry_run"])
        self.assertIsNone(body["signal_id"])
        self.assertIsNone(body["action"])
        self.assertEqual(body["signal_label"], "Forum mentions")
        self.assertIn({"key": "", "label": "Subreddit", "type": "text", "field": "subreddit"}, body["columns"])
        self.assertFalse(WatchAction.objects.filter(kind="bower").exists())

    def test_fields_choose_the_columns_and_their_order(self) -> None:
        def created(sent: dict) -> httpx.Response:
            return httpx.Response(201, json=_created(sent["json"]["columns"]), request=httpx.Request("POST", "x"))

        resp, sent = self._connect(created, fields=["url", "title"])
        self.assertEqual(resp.status_code, 201, resp.content)
        # FAILS if fields is ignored or the columns keep the derived order.
        self.assertEqual([c["label"] for c in sent["json"]["columns"]], ["Url", "Title"])
        action = WatchAction.objects.get(id=resp.json()["action"]["id"])
        self.assertEqual(action.config["columns"], {"f_url": "url", "f_title": "title"})
        left_out = resp.json()["left_out_fields"]
        self.assertIn("subreddit", left_out)
        self.assertNotIn("title", left_out)

    def test_a_field_the_watch_does_not_produce_is_refused_before_any_call(self) -> None:
        with mock.patch("watches.services._bower_http.post") as post:
            resp = self.client.post(
                f"/v1/watches/{self.watch_id}/connect-bower?dry_run=true",
                {"api_url": API_URL, "api_key": KEY, "fields": ["title", "likes"]},
                format="json",
            )
        self.assertEqual(resp.status_code, 400, resp.content)
        post.assert_not_called()
        detail = resp.json()["detail"]
        self.assertIn("no field likes", detail)
        self.assertIn("subreddit", detail)

    def test_a_repeated_field_is_a_400(self) -> None:
        resp = self.client.post(
            f"/v1/watches/{self.watch_id}/connect-bower",
            {"api_url": API_URL, "api_key": KEY, "fields": ["title", "title"]},
            format="json",
        )
        self.assertEqual(resp.status_code, 400, resp.content)
        self.assertIn("repeats title", resp.content.decode())

    def test_a_dry_run_lists_what_fields_left_out(self) -> None:
        resp = self.client.post(
            f"/v1/watches/{self.watch_id}/connect-bower?dry_run=true",
            {"api_url": API_URL, "api_key": KEY, "fields": ["title"]},
            format="json",
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        body = resp.json()
        self.assertEqual([c["field"] for c in body["columns"]], ["title"])
        self.assertIn("content", body["left_out_fields"])

    def test_a_bad_cadence_is_refused_before_openbower_is_called(self) -> None:
        # FAILS if the cadence is checked only when the action is added,
        # after the signal already exists in OpenBower.
        for cadence in (
            {"delivery": "digest", "digest_interval_seconds": 0},
            {"delivery": "digest", "digest_interval_seconds": 5},
        ):
            for query in ("", "?dry_run=true"):
                with (
                    self.subTest(cadence=cadence, query=query),
                    mock.patch("watches.services._bower_http.post") as post,
                ):
                    resp = self.client.post(
                        f"/v1/watches/{self.watch_id}/connect-bower{query}",
                        {"api_url": API_URL, "api_key": KEY, **cadence},
                        format="json",
                    )
                    self.assertEqual(resp.status_code, 400, resp.content)
                    post.assert_not_called()
        self.assertFalse(WatchAction.objects.filter(kind="bower").exists())

    def test_an_action_not_added_after_a_create_names_the_signal(self) -> None:
        def created(sent: dict) -> httpx.Response:
            return httpx.Response(201, json=_created(sent["json"]["columns"]), request=httpx.Request("POST", "x"))

        with mock.patch(
            "watches.services.bower_connect.BowerConnectService._add_action", side_effect=PolicyError("chain is full")
        ):
            resp, _sent = self._connect(created)
        self.assertEqual(resp.status_code, 409, resp.content)
        detail = resp.json()["detail"]
        self.assertIn("01SIG", detail)
        self.assertIn("chain is full", detail)
        self.assertIn("webhook URL", detail)

    def test_a_create_answer_missing_a_column_names_the_signal(self) -> None:
        def created(sent: dict) -> httpx.Response:
            body = _created(sent["json"]["columns"][1:])
            return httpx.Response(201, json=body, request=httpx.Request("POST", "x"))

        resp, _sent = self._connect(created)
        self.assertEqual(resp.status_code, 502, resp.content)
        self.assertIn("01SIG", resp.json()["detail"])
        self.assertFalse(WatchAction.objects.filter(kind="bower").exists())

    def test_a_missing_key_is_a_400(self) -> None:
        resp = self.client.post(f"/v1/watches/{self.watch_id}/connect-bower", {"api_url": API_URL}, format="json")
        self.assertEqual(resp.status_code, 400, resp.content)
        self.assertIn("api_key", resp.content.decode())
