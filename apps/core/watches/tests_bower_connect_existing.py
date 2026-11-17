"""`POST /v1/watches/<id>/connect-bower` with `webhook_url`: an existing
signal's columns read from its URL and matched to the item fields."""

import io
import logging
from typing import Any
from unittest import mock

import httpx

from watches.models import WatchAction
from watches.tests_bower_connect import API_URL, KEY, _WatchWithSources

SIGNAL_URL = "https://api.example.io/v1/signals/01EXISTING/ingest/tok_existing"


def _schema(*columns: tuple[str, str, str]) -> httpx.Response:
    body = {"columns": [{"key": key, "label": label, "type": kind} for key, label, kind in columns]}
    return httpx.Response(200, json=body, request=httpx.Request("GET", "x"))


class BowerConnectExistingTests(_WatchWithSources):
    """`webhook_url`: an existing signal, its columns read from the URL."""

    def _attach(
        self, answer: httpx.Response, *, dry_run: bool = False, url: str = SIGNAL_URL, **extra: Any
    ) -> tuple[Any, Any]:
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        logger = logging.getLogger("watches")
        logger.addHandler(handler)
        query = "?dry_run=true" if dry_run else ""
        try:
            with (
                mock.patch("watches.services._bower_http.destination_block_reason", return_value=None),
                mock.patch("watches.services._bower_http.get", return_value=answer) as get,
                mock.patch("watches.services._bower_http.post") as post,
            ):
                resp = self.client.post(
                    f"/v1/watches/{self.watch_id}/connect-bower{query}", {"webhook_url": url, **extra}, format="json"
                )
        finally:
            logger.removeHandler(handler)
        post.assert_not_called()
        self.assertNotIn("tok_existing", stream.getvalue())
        self.assertNotIn("tok_existing", resp.content.decode())
        return resp, get

    def test_an_existing_signal_is_matched_by_label_and_type(self) -> None:
        answer = _schema(
            ("c_title", "title", "text"),
            ("c_when", "Occurred at", "date"),
            ("c_notes", "Notes", "text"),
            ("c_sub", "Subreddit", "number"),
        )
        resp, get = self._attach(answer)
        self.assertEqual(resp.status_code, 201, resp.content)
        get.assert_called_once()
        self.assertEqual(get.call_args.args[0], SIGNAL_URL)
        body = resp.json()
        self.assertEqual(body["signal_id"], "01EXISTING")
        fields = {c["key"]: c["field"] for c in body["columns"]}
        # FAILS if labels match case-sensitively, a date column refuses a
        # timestamp, or a number column takes text.
        self.assertEqual(fields, {"c_title": "title", "c_when": "occurred_at", "c_notes": "", "c_sub": ""})
        self.assertIn("subreddit", body["unmatched_fields"])
        self.assertNotIn("title", body["unmatched_fields"])
        action = WatchAction.objects.get(id=body["action"]["id"])
        self.assertEqual(action.config["url"], SIGNAL_URL)
        self.assertEqual(action.config["columns"], {"c_title": "title", "c_when": "occurred_at"})

    def test_a_dry_run_reads_the_columns_and_adds_nothing(self) -> None:
        resp, get = self._attach(_schema(("c_title", "Title", "text")), dry_run=True)
        self.assertEqual(resp.status_code, 200, resp.content)
        get.assert_called_once()
        self.assertTrue(resp.json()["dry_run"])
        self.assertIsNone(resp.json()["action"])
        self.assertFalse(WatchAction.objects.filter(kind="bower").exists())

    def test_fields_match_only_the_chosen_fields(self) -> None:
        answer = _schema(("c_title", "Title", "text"), ("c_sub", "Subreddit", "text"))
        resp, _get = self._attach(answer, fields=["title"])
        self.assertEqual(resp.status_code, 201, resp.content)
        body = resp.json()
        # FAILS if a column whose field was left out is still filled.
        self.assertEqual({c["key"]: c["field"] for c in body["columns"]}, {"c_title": "title", "c_sub": ""})
        self.assertEqual(body["unmatched_fields"], [])
        self.assertIn("subreddit", body["left_out_fields"])
        action = WatchAction.objects.get(id=body["action"]["id"])
        self.assertEqual(action.config["columns"], {"c_title": "title"})

    def test_a_chosen_field_no_column_takes_is_refused(self) -> None:
        resp, _get = self._attach(_schema(("c_title", "Title", "text")), dry_run=True, fields=["title", "subreddit"])
        self.assertEqual(resp.status_code, 400, resp.content)
        self.assertIn("no column of the signal takes subreddit", resp.json()["detail"])

    def test_a_signal_no_field_matches_is_refused(self) -> None:
        resp, _get = self._attach(_schema(("c_x", "Deal size", "number")))
        self.assertEqual(resp.status_code, 400, resp.content)
        self.assertFalse(WatchAction.objects.filter(kind="bower").exists())

    def test_a_refused_url_says_to_copy_the_current_one(self) -> None:
        refusal = httpx.Response(404, json={"detail": "Not found."}, request=httpx.Request("GET", "x"))
        resp, _get = self._attach(refusal)
        self.assertEqual(resp.status_code, 400, resp.content)
        self.assertIn("current URL", resp.json()["detail"])
        self.assertFalse(WatchAction.objects.filter(kind="bower").exists())

    def test_openbower_unable_to_answer_now_is_a_502_not_a_rotated_url(self) -> None:
        outage = httpx.Response(503, json={"detail": "down"}, request=httpx.Request("GET", "x"))
        resp, _get = self._attach(outage)
        # FAILS if an outage tells the user to copy a new URL.
        self.assertEqual(resp.status_code, 502, resp.content)
        self.assertNotIn("current URL", resp.json()["detail"])

    def test_another_refusal_does_not_blame_the_url(self) -> None:
        refusal = httpx.Response(400, json={"detail": "bad"}, request=httpx.Request("GET", "x"))
        resp, _get = self._attach(refusal)
        self.assertEqual(resp.status_code, 400, resp.content)
        self.assertNotIn("current URL", resp.json()["detail"])

    def test_a_blocked_webhook_url_is_refused_before_any_call(self) -> None:
        with (
            mock.patch("watches.services._bower_http.destination_block_reason", return_value="a private address"),
            mock.patch("watches.services._bower_http.get") as get,
        ):
            resp = self.client.post(
                f"/v1/watches/{self.watch_id}/connect-bower", {"webhook_url": SIGNAL_URL}, format="json"
            )
        # FAILS if the SSRF check is skipped before the schema read.
        self.assertEqual(resp.status_code, 400, resp.content)
        self.assertIn("webhook_url rejected: a private address", resp.json()["detail"])
        self.assertNotIn("tok_existing", resp.content.decode())
        get.assert_not_called()

    def test_a_url_that_is_not_a_signal_webhook_is_refused_before_any_call(self) -> None:
        resp, get = self._attach(_schema(("c_title", "Title", "text")), url="https://api.example.io/v1/other/thing")
        self.assertEqual(resp.status_code, 400, resp.content)
        get.assert_not_called()

    def test_a_body_naming_no_route_says_which_to_give(self) -> None:
        resp = self.client.post(f"/v1/watches/{self.watch_id}/connect-bower", {"rank": 0}, format="json")
        self.assertEqual(resp.status_code, 400, resp.content)
        # FAILS if a body with neither URL falls to one route's missing-field error.
        self.assertIn("give webhook_url to connect an existing signal", resp.content.decode())

    def test_both_connects_at_once_is_a_400(self) -> None:
        resp = self.client.post(
            f"/v1/watches/{self.watch_id}/connect-bower",
            {"webhook_url": SIGNAL_URL, "api_url": API_URL, "api_key": KEY},
            format="json",
        )
        self.assertEqual(resp.status_code, 400, resp.content)
        self.assertIn("leave out api_url, api_key and label", resp.content.decode())
        self.assertNotIn(KEY, resp.content.decode())
