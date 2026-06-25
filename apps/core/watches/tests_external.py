"""resolve_external_content (watches.actions._external): the shared linked-article
enrichment used by semantic_filter and extract. Covers the opt-out gate, the
no-op when an item has no external link, the fetch+extract happy path, and the
best-effort fallbacks (a fetch failure / empty extraction must never fail the
caller). The network fetch + extraction are mocked; no engine, no DB."""

from __future__ import annotations

from unittest import mock

import httpx
from django.test import SimpleTestCase

from openmagpie_schema.watch_actions import ExternalContentStatus
from watches.actions._external import resolve_external_content
from watches.actions._fetch import ExternalFetchMixin

_MOD = "watches.actions._external"
_FETCH = "watches.actions._fetch.fetch_url_safely"  # the mixin's call site (where the name is looked up)


class ResolveExternalContentTests(SimpleTestCase):
    def _call(
        self, *, enabled: bool, external_url: str = "https://example.com/a"
    ) -> tuple[str | None, ExternalContentStatus]:
        return resolve_external_content(
            ExternalFetchMixin().fetch_external_url, action_id="a1", enabled=enabled, external_url=external_url
        )

    def test_opted_out_is_disabled(self) -> None:
        with mock.patch(_FETCH) as fetch:
            self.assertEqual(self._call(enabled=False), (None, ExternalContentStatus.DISABLED))
        fetch.assert_not_called()

    def test_no_external_url_is_not_applicable(self) -> None:
        with mock.patch(_FETCH) as fetch:
            self.assertEqual(self._call(enabled=True, external_url=""), (None, ExternalContentStatus.NOT_APPLICABLE))
        fetch.assert_not_called()

    def test_fetches_and_extracts_is_included(self) -> None:
        with (
            mock.patch(_FETCH, return_value=b"<html>..</html>") as fetch,
            mock.patch(f"{_MOD}.extract_article_text", return_value="ARTICLE TEXT") as extract,
        ):
            out = self._call(enabled=True)
        self.assertEqual(out, ("ARTICLE TEXT", ExternalContentStatus.INCLUDED))
        fetch.assert_called_once()
        extract.assert_called_once()

    def test_fetch_failure_is_unavailable(self) -> None:
        # The fetch itself failed (network / blocked / timeout): UNAVAILABLE, never fails the caller.
        with mock.patch(_FETCH, side_effect=httpx.ConnectError("boom")):
            self.assertEqual(self._call(enabled=True), (None, ExternalContentStatus.UNAVAILABLE))

    def test_fetched_but_no_text_is_missing(self) -> None:
        # Fetched OK but extraction yielded nothing (paywall / JS-only): MISSING.
        with (
            mock.patch(_FETCH, return_value=b"<html></html>"),
            mock.patch(f"{_MOD}.extract_article_text", return_value=""),
        ):
            self.assertEqual(self._call(enabled=True), (None, ExternalContentStatus.MISSING))
