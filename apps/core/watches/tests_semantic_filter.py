"""SemanticFilterAction._external_content: the opt-out gate, the no-op when an
item has no external link, the fetch+extract happy path, and the best-effort
fallback (a fetch failure must never fail the judge).

Unit-tests the helper directly (no DB / no engine) by passing a stub action, a
real config, and a real payload; the network fetch + extraction are mocked."""

from __future__ import annotations

from unittest import mock

from django.test import SimpleTestCase

from openmagpie_schema.watch_actions import SemanticFilterConfig
from sources.connectors.hackernews.payloads import HackerNewsFeedPayload
from watches.actions.semantic_filter import SemanticFilterAction

_MOD = "watches.actions.semantic_filter"


class ExternalContentFetchTests(SimpleTestCase):
    def _payload(self, external_url: str = "https://example.com/a") -> HackerNewsFeedPayload:
        # frozen model -> model_copy to set the external link under test
        return HackerNewsFeedPayload.sample().model_copy(update={"external_url": external_url})

    def _call(self, config: SemanticFilterConfig, payload: HackerNewsFeedPayload) -> str | None:
        return SemanticFilterAction()._external_content("a1", config, payload)

    def test_opted_out_does_not_fetch(self) -> None:
        config = SemanticFilterConfig(instructions="x", fetch_external_content=False)
        with mock.patch(f"{_MOD}.fetch_url_safely") as fetch:
            self.assertIsNone(self._call(config, self._payload()))
        fetch.assert_not_called()

    def test_no_external_url_does_not_fetch(self) -> None:
        config = SemanticFilterConfig(instructions="x")  # fetch_external_content defaults True
        with mock.patch(f"{_MOD}.fetch_url_safely") as fetch:
            self.assertIsNone(self._call(config, self._payload(external_url="")))
        fetch.assert_not_called()

    def test_fetches_and_extracts_when_on_with_a_link(self) -> None:
        config = SemanticFilterConfig(instructions="x")  # default on
        with (
            mock.patch(f"{_MOD}.fetch_url_safely", return_value=b"<html>..</html>") as fetch,
            mock.patch(f"{_MOD}.extract_article_text", return_value="ARTICLE TEXT") as extract,
        ):
            out = self._call(config, self._payload())
        self.assertEqual(out, "ARTICLE TEXT")
        fetch.assert_called_once()
        extract.assert_called_once()

    def test_fetch_failure_falls_back_to_none(self) -> None:
        # Best-effort: a paywall / timeout / blocked host must not fail the judge.
        config = SemanticFilterConfig(instructions="x")
        with mock.patch(f"{_MOD}.fetch_url_safely", side_effect=RuntimeError("paywall")):
            self.assertIsNone(self._call(config, self._payload()))
