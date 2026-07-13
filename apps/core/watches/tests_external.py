"""resolve_external_content (watches.actions._external): the shared linked-article
enrichment used by semantic_filter and extract. Covers the opt-out gate, the
no-op when an item has no external link, the fetch+extract happy path, the
best-effort fallbacks (a fetch failure / empty extraction must never fail the
caller), AND the headless challenge-bypass fallback (with its SSRF pre-check). The
network fetch + extraction + sidecar are mocked; no engine, no DB."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest import mock

import httpx
from django.test import SimpleTestCase, override_settings

from openmagpie_schema.watch_actions import ExternalContentStatus
from sources.connectors.rss.payloads import RssEntryPayload
from sources.payloads import SourcePayload
from watches.actions._external import resolve_external_content
from watches.actions._fetch import ExternalFetchMixin

_MOD = "watches.actions._external"
_FETCH = "watches.actions._fetch.fetch_url_safely"  # the mixin's call site (where the name is looked up)
_SOLVE = f"{_MOD}.challenge_bypass_fetch"  # the challenge-bypass sidecar call
_BLOCK = f"{_MOD}.destination_block_reason"  # the SSRF host pre-check


def _call(*, enabled: bool, article_url: str = "https://example.com/a") -> tuple[str | None, ExternalContentStatus]:
    return resolve_external_content(
        ExternalFetchMixin().fetch_external_url, action_id="a1", enabled=enabled, article_url=article_url
    )


@override_settings(SOURCE_CHALLENGE_BYPASS_URL="")  # sidecar OFF: exercise the direct-fetch path only
class ResolveExternalContentTests(SimpleTestCase):
    def test_opted_out_is_disabled(self) -> None:
        with mock.patch(_FETCH) as fetch:
            self.assertEqual(_call(enabled=False), (None, ExternalContentStatus.DISABLED))
        fetch.assert_not_called()

    def test_no_external_url_is_not_applicable(self) -> None:
        with mock.patch(_FETCH) as fetch:
            self.assertEqual(_call(enabled=True, article_url=""), (None, ExternalContentStatus.NOT_APPLICABLE))
        fetch.assert_not_called()

    def test_fetches_and_extracts_is_included(self) -> None:
        with (
            mock.patch(_FETCH, return_value=b"<html>..</html>") as fetch,
            mock.patch(f"{_MOD}.extract_article_text", return_value="ARTICLE TEXT") as extract,
            mock.patch(_SOLVE) as solve,
        ):
            out = _call(enabled=True)
        self.assertEqual(out, ("ARTICLE TEXT", ExternalContentStatus.INCLUDED))
        fetch.assert_called_once()
        extract.assert_called_once()
        solve.assert_not_called()  # the happy path never consults the sidecar

    def test_fetch_failure_is_unavailable(self) -> None:
        # The fetch itself failed (network / blocked / timeout): UNAVAILABLE, never fails the caller.
        with mock.patch(_FETCH, side_effect=httpx.ConnectError("boom")):
            self.assertEqual(_call(enabled=True), (None, ExternalContentStatus.UNAVAILABLE))

    def test_malformed_url_is_unavailable_not_crash(self) -> None:
        # A malformed external_url makes the primary fetch raise httpx.InvalidURL (a
        # SIBLING of HTTPError, not caught by it); best-effort enrichment must degrade to
        # UNAVAILABLE, never let it propagate and fail the run.
        with mock.patch(_FETCH, side_effect=httpx.InvalidURL("bad url")):
            self.assertEqual(_call(enabled=True), (None, ExternalContentStatus.UNAVAILABLE))

    def test_disabled_sidecar_skips_precheck_and_solve(self) -> None:
        # Ordering invariant: with the sidecar OFF, _challenge_fallback returns before
        # the DNS-resolving SSRF pre-check AND the sidecar call. A reorder that did the
        # DNS lookup first would start doing real network in SimpleTestCase.
        with (
            mock.patch(_FETCH, side_effect=httpx.ConnectError("blocked")),
            mock.patch(_BLOCK) as block,
            mock.patch(_SOLVE) as solve,
        ):
            self.assertEqual(_call(enabled=True), (None, ExternalContentStatus.UNAVAILABLE))
        block.assert_not_called()
        solve.assert_not_called()

    def test_fetched_but_no_text_is_missing(self) -> None:
        # Fetched OK but extraction yielded nothing (paywall / JS-only): MISSING.
        with (
            mock.patch(_FETCH, return_value=b"<html></html>"),
            mock.patch(f"{_MOD}.extract_article_text", return_value=""),
        ):
            self.assertEqual(_call(enabled=True), (None, ExternalContentStatus.MISSING))


@override_settings(SOURCE_CHALLENGE_BYPASS_URL="http://flare.test/v1")  # sidecar ON
class ChallengeBypassFallbackTests(SimpleTestCase):
    """When the direct fetch hits a Cloudflare-style wall (UNAVAILABLE / MISSING) and a
    sidecar is configured, retry through it (INCLUDED_VIA_FALLBACK), but only after the
    SSRF host pre-check clears the untrusted external_url."""

    def test_unavailable_falls_back_to_sidecar(self) -> None:
        with (
            mock.patch(_FETCH, side_effect=httpx.ConnectError("blocked")),
            mock.patch(_BLOCK, return_value=None),  # host allowed
            mock.patch(_SOLVE, return_value=b"<html>solved</html>") as solve,
            mock.patch(f"{_MOD}.extract_article_text", return_value="ARTICLE"),
        ):
            self.assertEqual(_call(enabled=True), ("ARTICLE", ExternalContentStatus.INCLUDED_VIA_FALLBACK))
        solve.assert_called_once()

    def test_non_web_scheme_rejected_before_sidecar(self) -> None:
        # A hostless / non-http(s) external_url (file:// data: gopher:) must never reach
        # the sidecar's browser. The direct path rejects it (-> UNAVAILABLE) and the
        # scheme+host guard fires BEFORE the SSRF pre-check and the sidecar call.
        with (
            mock.patch(_FETCH, side_effect=httpx.UnsupportedProtocol("no file://")),
            mock.patch(_BLOCK) as block,
            mock.patch(_SOLVE) as solve,
        ):
            out = _call(enabled=True, article_url="file:///etc/passwd")
        self.assertEqual(out, (None, ExternalContentStatus.UNAVAILABLE))
        block.assert_not_called()  # guard rejects before the (DNS-resolving) pre-check
        solve.assert_not_called()

    def test_empty_host_rejected_before_sidecar(self) -> None:
        # The guard's second branch: a valid http scheme but NO host (http:///path) must
        # also be rejected before the pre-check + sidecar.
        with (
            mock.patch(_FETCH, side_effect=httpx.InvalidURL("no host")),
            mock.patch(_BLOCK) as block,
            mock.patch(_SOLVE) as solve,
        ):
            out = _call(enabled=True, article_url="http:///just/a/path")
        self.assertEqual(out, (None, ExternalContentStatus.UNAVAILABLE))
        block.assert_not_called()
        solve.assert_not_called()

    def test_malformed_port_blocked_not_crash(self) -> None:
        # An out-of-range port makes destination_block_reason raise ValueError on
        # parts.port; the (real, unmocked) pre-check must treat it as BLOCKED, not crash
        # the run. Sidecar never called; status stays UNAVAILABLE. (No DNS: the port
        # ValueError fires before getaddrinfo, so this is hermetic.)
        with (
            mock.patch(_FETCH, side_effect=httpx.ConnectError("blocked")),
            mock.patch(_SOLVE) as solve,
        ):
            out = _call(enabled=True, article_url="http://example.com:999999/x")
        self.assertEqual(out, (None, ExternalContentStatus.UNAVAILABLE))
        solve.assert_not_called()

    def test_malformed_ipv6_url_does_not_crash_with_sidecar_on(self) -> None:
        # End-to-end malformed string (unterminated IPv6): the primary fetch raises
        # httpx.InvalidURL, then _challenge_fallback re-parses the SAME string with stdlib
        # urlsplit (a DIFFERENT parser) which raises ValueError. With the sidecar ON, that
        # parse must be guarded -> UNAVAILABLE, not a crashed run. (Real urlsplit, not
        # mocked: the case a well-formed URL + mocked error can't catch.)
        with (
            mock.patch(_FETCH, side_effect=httpx.InvalidURL("bad url")),
            mock.patch(_SOLVE) as solve,
        ):
            out = _call(enabled=True, article_url="http://[::1")
        self.assertEqual(out, (None, ExternalContentStatus.UNAVAILABLE))
        solve.assert_not_called()

    def test_missing_falls_back_to_sidecar(self) -> None:
        # Direct fetch returns an interstitial (extract #1 empty -> MISSING); the sidecar's
        # solved HTML extracts (extract #2) to real text -> INCLUDED_VIA_FALLBACK.
        with (
            mock.patch(_FETCH, return_value=b"<html>cf</html>"),
            mock.patch(_BLOCK, return_value=None),
            mock.patch(_SOLVE, return_value=b"<html>solved</html>"),
            mock.patch(f"{_MOD}.extract_article_text", side_effect=["", "ARTICLE"]),
        ):
            self.assertEqual(_call(enabled=True), ("ARTICLE", ExternalContentStatus.INCLUDED_VIA_FALLBACK))

    def test_ssrf_precheck_blocks_private_host_sidecar_not_called(self) -> None:
        # A private-resolving external_url must never be handed to the sidecar (it can't
        # pin the IP); the run keeps the direct-path status.
        with (
            mock.patch(_FETCH, side_effect=httpx.ConnectError("blocked")),
            mock.patch(_BLOCK, return_value="host resolves to a blocked address (10.0.0.1)"),
            mock.patch(_SOLVE) as solve,
        ):
            self.assertEqual(_call(enabled=True), (None, ExternalContentStatus.UNAVAILABLE))
        solve.assert_not_called()

    def test_sidecar_unavailable_keeps_direct_status(self) -> None:
        with (
            mock.patch(_FETCH, side_effect=httpx.ConnectError("blocked")),
            mock.patch(_BLOCK, return_value=None),
            mock.patch(_SOLVE, return_value=None),  # sidecar unreachable / refused / oversize
        ):
            self.assertEqual(_call(enabled=True), (None, ExternalContentStatus.UNAVAILABLE))

    def test_sidecar_html_but_no_text_stays_missing(self) -> None:
        with (
            mock.patch(_FETCH, return_value=b"<html>cf</html>"),
            mock.patch(_BLOCK, return_value=None),
            mock.patch(_SOLVE, return_value=b"<html>still-cf</html>"),
            mock.patch(f"{_MOD}.extract_article_text", side_effect=["", ""]),  # neither extracts
        ):
            self.assertEqual(_call(enabled=True), (None, ExternalContentStatus.MISSING))


class ArticleUrlSelectionTests(SimpleTestCase):
    """`FeedItemPayload.article_url` is the per-kind fetch target enrichment reads: the
    default is `external_url` (aggregators point off-site), RSS overrides to `url` (the
    entry IS the article). Chosen at eval time from stored fields, so RSS enriches with
    no re-poll; a self/discussion item with no external_url stays "" -> NOT_APPLICABLE."""

    _WHEN = datetime(2026, 1, 1, tzinfo=UTC)

    def test_rss_selects_url(self) -> None:
        p = RssEntryPayload(
            external_id="e",
            kind="rss_entry",
            occurred_at=self._WHEN,
            source="rss",
            url="https://school.example/article",
            external_url="",
        )
        self.assertEqual(p.article_url, "https://school.example/article")

    def test_base_selects_external_url(self) -> None:
        # An aggregator (HN link post): article_url is the off-site link, not its own url.
        p = SourcePayload(
            external_id="e",
            kind="hn_feed",
            occurred_at=self._WHEN,
            source="hn_feed",
            url="https://news.ycombinator.com/item?id=1",
            external_url="https://blog.example/post",
        )
        self.assertEqual(p.article_url, "https://blog.example/post")

    def test_self_item_with_no_external_url_is_empty(self) -> None:
        # Reddit / Ask HN / HN comment: url is a discussion page, external_url empty ->
        # article_url "" -> enrichment no-ops (don't fetch the discussion page).
        p = SourcePayload(
            external_id="e",
            kind="new_post",
            occurred_at=self._WHEN,
            source="reddit_subreddit",
            url="https://reddit.com/r/x/comments/1",
            external_url="",
        )
        self.assertEqual(p.article_url, "")
