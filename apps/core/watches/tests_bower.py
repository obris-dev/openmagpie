from unittest import mock

import httpx
import ulid
from django.test import SimpleTestCase

from openmagpie_schema.watch_actions import (
    BOWER_MAX_PUSH_BYTES,
    BOWER_MAX_ROWS_PER_PUSH,
    BOWER_REFUSAL_MAX_LENGTH,
    BowerConfig,
)
from openmagpie_schema.watch_actions._secrets import REDACTED
from openmagpie_schema.watch_enums import DeliveryCadence, WatchActionRunState
from watches import run_messages
from watches.actions.bower import BowerAction, build_push
from watches.actions.protocol import ActionContext, ActionItem, ActionResult, OutboundActionResult
from watches.models import WatchAction
from watches.policy import PolicyError, enforce_action_policy

URL = "https://api.example.io/v1/signals/01ABC/ingest/tok_secret"
COLUMNS = {"title": "title", "subreddit": "subreddit", "posted_at": "occurred_at", "points": "points"}


def _item(external_id: str, **data: object) -> ActionItem:
    return ActionItem(
        data={"external_id": external_id, **data},
        key=f"reddit_subreddit:{external_id}",
        source_label="r/saas",
        source_kind="reddit_subreddit",
    )


def _config(**overrides: object) -> BowerConfig:
    return BowerConfig.model_validate({"url": URL, "columns": COLUMNS, **overrides})


class BowerPushTests(SimpleTestCase):
    """The envelope a bower action sends: OpenBower's push body."""

    def test_a_row_per_item_keyed_on_the_item_and_carrying_mapped_scalars(self) -> None:
        # FAILS if external_id is not the item key (a resend would land twice)
        # or if a list field rides as a cell (OpenBower refuses the push).
        item = _item(
            "abc",
            title="Looking for a CRM",
            subreddit="saas",
            occurred_at="2026-11-16T12:00:00Z",
            points=42,
            categories=["a", "b"],
            content="not mapped",
        )
        config = _config(columns={**COLUMNS, "tags": "categories", "missing": "nope"})
        push = build_push("act_1", config, [item])
        self.assertEqual(len(push.rows), 1)
        self.assertEqual(push.rows[0].external_id, "reddit_subreddit:abc")
        self.assertEqual(
            push.rows[0].cells,
            {"title": "Looking for a CRM", "subreddit": "saas", "posted_at": "2026-11-16T12:00:00Z", "points": "42"},
        )

    def test_request_id_is_the_same_for_a_resent_batch_and_differs_for_another(self) -> None:
        # FAILS if request_id varies for the same batch: a retry after a lost
        # answer would be a new request rather than the one already taken.
        config = _config()
        one, two = _item("a", title="A"), _item("b", title="B")
        first = build_push("act_1", config, [one, two]).request_id
        self.assertEqual(first, build_push("act_1", config, [two, one]).request_id)
        self.assertNotEqual(first, build_push("act_1", config, [one]).request_id)
        self.assertNotEqual(first, build_push("act_2", config, [one, two]).request_id)
        self.assertRegex(first, r"^magpie-[0-9a-f]{32}$")

    def test_a_batch_past_the_push_cap_is_refused(self) -> None:
        items = [_item(str(n)) for n in range(BOWER_MAX_ROWS_PER_PUSH + 1)]
        with self.assertRaises(ValueError):
            build_push("act_1", _config(), items)

    def test_a_digest_slice_is_never_larger_than_one_push(self) -> None:
        # FAILS if the flush could gather a slice build_push refuses.
        self.assertEqual(BowerAction.max_batch_items, BOWER_MAX_ROWS_PER_PUSH)
        # FAILS if a slice could outgrow the push size OpenBower takes.
        self.assertEqual(BowerAction.max_batch_bytes, BOWER_MAX_PUSH_BYTES)


class BowerRunTests(SimpleTestCase):
    """The run: the POST it makes and how OpenBower's answer settles it."""

    def _run(self, status: int, body: object = None) -> tuple[ActionResult, dict]:
        action = WatchAction(id=ulid.ulid(), kind="bower", config={"url": URL, "columns": COLUMNS})
        context = ActionContext(watch_id="w", watch_name="n", delivery=DeliveryCadence.INSTANT)
        captured: dict = {}

        def fake_request(method, url, **kwargs):
            captured.update(method=method, url=url, **kwargs)
            return httpx.Response(status, json=body, request=httpx.Request(method, url))

        with (
            mock.patch("watches.actions._http_delivery.destination_block_reason", return_value=None),
            mock.patch("watches.actions._http_delivery.httpx.request", side_effect=fake_request),
        ):
            result = BowerAction().run(action, items=[_item("abc", title="T")], context=context)
        return result, captured

    def test_success_posts_the_envelope_and_records_what_was_accepted(self) -> None:
        result, sent = self._run(202, {"request_id": "x", "accepted": 1})
        self.assertEqual(sent["method"], "POST")
        self.assertEqual(sent["url"], URL)
        self.assertEqual(sent["headers"], {})
        self.assertEqual(set(sent["json"]), {"request_id", "rows"})
        self.assertEqual(sent["json"]["rows"], [{"external_id": "reddit_subreddit:abc", "cells": {"title": "T"}}])
        self.assertEqual(result.state, WatchActionRunState.SUCCEEDED)
        self.assertEqual(result.result, {"http_status": 202, "accepted": 1, "error": "", "detail": ""})
        assert isinstance(result, OutboundActionResult)
        self.assertEqual(result.outbound.item_count, 1)
        self.assertEqual(result.outbound.request_payload, sent["json"])

    def test_a_refusal_errors_and_a_server_failure_retries(self) -> None:
        refused, _ = self._run(400, {"error": "ingest_invalid", "detail": "row 0: unknown column 'c_x'"})
        self.assertEqual(refused.state, WatchActionRunState.ERRORED)
        # FAILS if a bower refusal reads as a webhook's, or OpenBower's reason is dropped.
        self.assertEqual(refused.error, run_messages.BOWER_REJECTED)
        self.assertEqual(refused.result["error"], "ingest_invalid")
        self.assertEqual(refused.result["detail"], "row 0: unknown column 'c_x'")
        gone, _ = self._run(404)
        self.assertEqual(gone.state, WatchActionRunState.ERRORED)
        # FAILS if a rotated URL is not told apart from refused rows.
        self.assertEqual(gone.error, run_messages.BOWER_URL_GONE)
        down, _ = self._run(503)
        self.assertEqual(down.state, WatchActionRunState.FAILED)
        self.assertEqual(down.error, run_messages.TRANSIENT)

    def test_a_long_refusal_is_cut_to_its_bound(self) -> None:
        refused, _ = self._run(400, {"error": "e", "detail": "x" * (BOWER_REFUSAL_MAX_LENGTH + 50)})
        self.assertEqual(len(refused.result["detail"]), BOWER_REFUSAL_MAX_LENGTH)

    def test_a_blocked_destination_uses_the_bower_message(self) -> None:
        action = WatchAction(id=ulid.ulid(), kind="bower", config={"url": URL, "columns": COLUMNS})
        context = ActionContext(watch_id="w", watch_name="n", delivery=DeliveryCadence.INSTANT)
        with mock.patch("watches.actions._http_delivery.destination_block_reason", return_value="private address"):
            result = BowerAction().run(action, items=[_item("abc", title="T")], context=context)
        self.assertEqual(result.error, run_messages.BOWER_BLOCKED)


class BowerConfigTests(SimpleTestCase):
    """The URL is the one secret: masked on read, restored on an edit."""

    def test_the_url_is_masked_and_an_exact_masked_echo_restores_it(self) -> None:
        # FAILS if the read leaks the token, or an edit that leaves the URL
        # masked stores the mask.
        prior = _config()
        shown = prior.redacted_dump()
        self.assertEqual(shown["url"], f"https://api.example.io/{REDACTED}")
        self.assertNotIn("tok_secret", str(shown))
        edited = BowerConfig.model_validate({**shown, "columns": {"title": "title"}})
        self.assertTrue(edited.has_masked_secret())
        merged = edited.merge_preserving(prior)
        self.assertEqual(merged.url, URL)
        self.assertEqual(merged.columns, {"title": "title"})

    def test_a_masked_url_with_no_bower_prior_is_kept_as_sent(self) -> None:
        masked = _config(url=f"https://api.example.io/{REDACTED}")
        self.assertEqual(masked.merge_preserving(_config(url="https://other.example.io/x")).url, masked.url)

    def test_an_empty_field_map_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            _config(columns={})

    def test_the_write_time_url_policy_applies(self) -> None:
        with self.settings(WEBHOOK_BLOCK_PRIVATE_IPS=True), self.assertRaises(PolicyError):
            enforce_action_policy(_config(url="http://10.0.0.5/v1/signals/x/ingest/t"))
