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
from watches.models import WatchAction

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


class BowerConnectTests(TestCase):
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

    def _connect(self, answer: Answer, api_url: str = API_URL) -> tuple[Any, dict]:
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
                mock.patch("watches.services.bower_connect.destination_block_reason", return_value=None),
                mock.patch("watches.services.bower_connect.httpx.post", side_effect=fake_post),
            ):
                resp = self.client.post(
                    f"/v1/watches/{self.watch_id}/connect-bower",
                    {"api_url": api_url, "api_key": KEY},
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
        self.assertEqual(columns["Permalink"], "url")
        self.assertEqual(columns["Occurred at"], "text")
        self.assertEqual(columns["Subreddit"], "text")
        for plumbing in ("Kind", "External id", "Source", "Parent external id"):
            self.assertNotIn(plumbing, columns)

        action = WatchAction.objects.get(id=resp.json()["action"]["id"])
        self.assertEqual(action.kind, "bower")
        self.assertEqual(action.config["url"], WEBHOOK_URL)
        self.assertEqual(action.config["fields"]["f_subreddit"], "subreddit")
        self.assertEqual(action.config["fields"]["f_occurred_at"], "occurred_at")

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

    def test_an_openbower_refusal_is_relayed_and_nothing_is_added(self) -> None:
        refusal = httpx.Response(401, json={"detail": "Invalid API key."}, request=httpx.Request("POST", "x"))
        resp, sent = self._connect(refusal)
        self.assertEqual(resp.status_code, 400, resp.content)
        self.assertIn("Invalid API key.", resp.json()["detail"])
        self.assertNotIn(KEY, resp.content.decode())
        self.assertNotIn(KEY, sent["logs"])
        self.assertFalse(WatchAction.objects.filter(kind="bower").exists())

    def test_openbower_down_is_a_502(self) -> None:
        resp, sent = self._connect(httpx.ConnectError("refused"))
        self.assertEqual(resp.status_code, 502, resp.content)
        self.assertNotIn(KEY, sent["logs"])
        self.assertFalse(WatchAction.objects.filter(kind="bower").exists())

    def test_a_watch_whose_feeds_have_no_sources_is_refused_before_any_call(self) -> None:
        resp = self.client.post("/v1/watches", {"name": "Empty", "feed_ids": []}, format="json")
        empty_id = resp.json()["id"]
        with mock.patch("watches.services.bower_connect.httpx.post") as post:
            resp = self.client.post(
                f"/v1/watches/{empty_id}/connect-bower", {"api_url": API_URL, "api_key": KEY}, format="json"
            )
        self.assertEqual(resp.status_code, 400, resp.content)
        post.assert_not_called()

    def test_a_dry_run_names_the_columns_and_calls_openbower_not_at_all(self) -> None:
        # FAILS if the preview reaches OpenBower or adds the action.
        with mock.patch("watches.services.bower_connect.httpx.post") as post:
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

    def test_a_missing_key_is_a_400(self) -> None:
        resp = self.client.post(f"/v1/watches/{self.watch_id}/connect-bower", {"api_url": API_URL}, format="json")
        self.assertEqual(resp.status_code, 400, resp.content)
        self.assertIn("api_key", resp.json())
