"""SemanticFilterAction._external_content: the opt-out gate, the no-op when an
item has no external link, the fetch+extract happy path, and the best-effort
fallback (a fetch failure must never fail the judge).

Unit-tests the helper directly (no DB / no engine) by passing a stub action, a
real config, and a real payload; the network fetch + extraction are mocked."""

from __future__ import annotations

from unittest import mock

import httpx
from django.test import SimpleTestCase

from openmagpie_schema.watch_actions import ExternalContentStatus, SemanticFilterConfig
from sources.connectors.hackernews.payloads import HackerNewsFeedPayload
from watches.actions.semantic_filter import SemanticFilterAction

_MOD = "watches.actions.semantic_filter"
_FETCH = "watches.actions._fetch.fetch_url_safely"  # the mixin's call site (where the name is looked up)


class ExternalContentFetchTests(SimpleTestCase):
    def _payload(self, external_url: str = "https://example.com/a") -> HackerNewsFeedPayload:
        # frozen model -> model_copy to set the external link under test
        return HackerNewsFeedPayload.sample().model_copy(update={"external_url": external_url})

    def _call(
        self, config: SemanticFilterConfig, payload: HackerNewsFeedPayload
    ) -> tuple[str | None, ExternalContentStatus]:
        return SemanticFilterAction()._external_content("a1", config, payload)

    def test_opted_out_is_disabled(self) -> None:
        config = SemanticFilterConfig(instructions="x", fetch_external_content=False)
        with mock.patch(_FETCH) as fetch:
            self.assertEqual(self._call(config, self._payload()), (None, ExternalContentStatus.DISABLED))
        fetch.assert_not_called()

    def test_no_external_url_is_not_applicable(self) -> None:
        config = SemanticFilterConfig(instructions="x")  # fetch_external_content defaults True
        with mock.patch(_FETCH) as fetch:
            self.assertEqual(
                self._call(config, self._payload(external_url="")), (None, ExternalContentStatus.NOT_APPLICABLE)
            )
        fetch.assert_not_called()

    def test_fetches_and_extracts_is_included(self) -> None:
        config = SemanticFilterConfig(instructions="x")  # default on
        with (
            mock.patch(_FETCH, return_value=b"<html>..</html>") as fetch,
            mock.patch(f"{_MOD}.extract_article_text", return_value="ARTICLE TEXT") as extract,
        ):
            out = self._call(config, self._payload())
        self.assertEqual(out, ("ARTICLE TEXT", ExternalContentStatus.INCLUDED))
        fetch.assert_called_once()
        extract.assert_called_once()

    def test_fetch_failure_is_unavailable(self) -> None:
        # The fetch itself failed (network / blocked / timeout): UNAVAILABLE, never fails the judge.
        config = SemanticFilterConfig(instructions="x")
        with mock.patch(_FETCH, side_effect=httpx.ConnectError("boom")):
            self.assertEqual(self._call(config, self._payload()), (None, ExternalContentStatus.UNAVAILABLE))

    def test_fetched_but_no_text_is_missing(self) -> None:
        # Fetched OK but extraction yielded nothing (paywall / JS-only): MISSING.
        config = SemanticFilterConfig(instructions="x")
        with (
            mock.patch(_FETCH, return_value=b"<html></html>"),
            mock.patch(f"{_MOD}.extract_article_text", return_value=""),
        ):
            self.assertEqual(self._call(config, self._payload()), (None, ExternalContentStatus.MISSING))
