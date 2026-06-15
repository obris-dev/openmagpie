from typing import Literal, get_args, get_origin
from unittest import mock

import feedparser
import httpx
from django.test import SimpleTestCase
from pydantic import BaseModel

from openmagpie_schema.configs import RedditSubredditSourceSpec, RssSourceSpec
from openmagpie_schema.feed import FeedItemData
from sources import (
    payload_registry,
    registry,  # noqa: F401  pulls in the connectors, which self-register their payloads
)
from sources.connectors.base import ConnectorParseError
from sources.connectors.reddit.connector import RedditSubRedditConnector
from sources.connectors.rss.connector import RssConnector, _unwrap_xml_viewer

# A minimal RSS feed and the Chromium XML-viewer HTML wrapper FlareSolverr
# returns when its headless browser "renders" that feed (the original source
# lands inside <div id="webkit-xml-viewer-source-xml">).
_FEED_XML = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<rss version="2.0"><channel><title>T</title>'
    "<item><title>One</title><guid>g1</guid>"
    "<pubDate>Mon, 01 Jun 2026 00:00:00 GMT</pubDate></item>"
    "<item><title>Two</title><guid>g2</guid>"
    "<pubDate>Tue, 02 Jun 2026 00:00:00 GMT</pubDate></item>"
    "</channel></rss>"
)
_WRAPPER = (
    '<html xmlns="http://www.w3.org/1999/xhtml"><head>'
    '<style id="xml-viewer-style">div.header { color: red; }</style></head>'
    '<body><div id="webkit-xml-viewer-source-xml">' + _FEED_XML + "</div>"
    '<div class="pretty-print">&lt;rss&gt; tree view</div></body></html>'
)


class UnwrapXmlViewerTests(SimpleTestCase):
    """`_unwrap_xml_viewer` recovers the embedded feed from the Chromium
    XML-viewer wrapper, and leaves a non-wrapper body alone."""

    def test_extracts_embedded_feed_that_then_parses(self) -> None:
        xml = _unwrap_xml_viewer(_WRAPPER.encode("utf-8"))
        parsed = feedparser.parse(xml)
        self.assertEqual(parsed.version, "rss20")
        self.assertEqual([e.title for e in parsed.entries], ["One", "Two"])

    def test_raw_feed_passes_through_unchanged(self) -> None:
        raw = _FEED_XML.encode("utf-8")
        self.assertEqual(_unwrap_xml_viewer(raw), raw)

    def test_non_feed_html_passes_through_unchanged(self) -> None:
        # No marker -> not a viewer wrapper -> returned as-is (the caller's
        # feedparser then fails it as before; we don't fabricate a feed).
        html = b"<html><body>rate limited</body></html>"
        self.assertEqual(_unwrap_xml_viewer(html), html)

    def test_cdata_with_literal_close_div_is_not_truncated(self) -> None:
        # A feed item whose CDATA body contains a literal `</div>` must not
        # truncate extraction (why we slice to the LAST </rss>, not the
        # source div's </div>).
        feed = (
            '<rss version="2.0"><channel><title>T</title>'
            "<item><title>One</title><guid>g1</guid>"
            "<pubDate>Mon, 01 Jun 2026 00:00:00 GMT</pubDate>"
            "<description><![CDATA[<div>hello</div>]]></description></item>"
            "<item><title>Two</title><guid>g2</guid>"
            "<pubDate>Tue, 02 Jun 2026 00:00:00 GMT</pubDate></item>"
            "</channel></rss>"
        )
        wrapper = '<html><body><div id="webkit-xml-viewer-source-xml">' + feed + "</div></body></html>"
        parsed = feedparser.parse(_unwrap_xml_viewer(wrapper.encode("utf-8")))
        self.assertEqual([e.title for e in parsed.entries], ["One", "Two"])


class ChallengeBypassRecoveryTests(SimpleTestCase):
    """The RSS connector recovers a challenge-gated XML feed end to end:
    the direct fetch yields a non-feed body, the FlareSolverr fallback
    returns the XML-viewer wrapper, and the connector unwraps + parses it."""

    def test_poll_recovers_via_unwrapped_bypass_body(self) -> None:
        spec = RssSourceSpec(kind="rss", url="https://gated.example/feed", name="Gated")
        with (
            # Direct fetch hits the WAF gate: empty body -> no feed detected.
            mock.patch.object(RssConnector, "_fetch_with_ssl_fallback", return_value=b""),
            # FlareSolverr solves the challenge but returns the viewer wrapper.
            mock.patch.object(RssConnector, "challenge_bypass_fetch", return_value=_WRAPPER.encode("utf-8")),
        ):
            payloads = list(RssConnector().poll(spec, since=None))
        self.assertEqual([p.title for p in payloads], ["One", "Two"])


class RedditRateLimitBackoffTests(SimpleTestCase):
    """The Reddit connector sleeps out 429s instead of aborting the source:
    Retry-After drives the wait when present, exponential backoff when not,
    the poll-lease heartbeat ticks through each wait, and exhausted retries
    surface as the normal HTTPStatusError so the polling orchestrator's
    recoverable per-source path stays the one fault signal."""

    _SPEC = RedditSubredditSourceSpec(kind="reddit_subreddit", subreddit="devops")
    # Valid-but-empty Atom: exercises the fetch/retry seam without needing
    # full Reddit entry fixtures (poll returns no payloads on an empty page).
    _EMPTY_ATOM = b'<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"></feed>'

    def _poll_with(
        self,
        responses: list[httpx.Response],
        sleeps: list[float] | None = None,
        heartbeat=None,
    ) -> tuple[list, list[float]]:
        """Run one poll against a canned response sequence, capturing the
        backoff sleeps. Returns (payloads, sleep durations). Pass `sleeps`
        to own the capture list when the poll is expected to raise."""
        queue = list(responses)

        def handler(request: httpx.Request) -> httpx.Response:
            return queue.pop(0)

        transport = httpx.MockTransport(handler)
        real_client = httpx.Client
        captured: list[float] = sleeps if sleeps is not None else []
        with (
            mock.patch(
                "sources.connectors.reddit.connector.httpx.Client",
                lambda **kw: real_client(transport=transport, **kw),
            ),
            mock.patch("sources.connectors.reddit.connector.time.sleep", side_effect=captured.append),
        ):
            payloads = list(RedditSubRedditConnector().poll(self._SPEC, since=None, heartbeat=heartbeat))
        return payloads, captured

    def test_retry_after_drives_the_wait_then_page_succeeds(self) -> None:
        with self.assertLogs("sources", level="INFO") as logs:
            payloads, sleeps = self._poll_with(
                [
                    httpx.Response(429, headers={"Retry-After": "3"}),
                    httpx.Response(200, content=self._EMPTY_ATOM),
                ]
            )
        self.assertEqual(payloads, [])
        self.assertEqual(sleeps, [3.0])
        # The retry warns, and the eventual success closes the loop so the 429
        # lines don't read as an unresolved failure.
        joined = "\n".join(logs.output)
        self.assertIn("rate limited", joined)
        self.assertIn("succeeded after 1 retry", joined)

    def test_no_recovery_line_without_a_retry(self) -> None:
        # A clean first hit must not emit the recovery line.
        with self.assertNoLogs("sources", level="INFO"):
            self._poll_with([httpx.Response(200, content=self._EMPTY_ATOM)])

    def test_unusable_retry_after_falls_back_to_exponential(self) -> None:
        payloads, sleeps = self._poll_with(
            [
                httpx.Response(429),
                httpx.Response(429, headers={"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"}),
                # float("nan") PARSES; without the isfinite screen it sails
                # through every comparison into time.sleep(nan), whose
                # ValueError is outside the recoverable set and aborts the
                # feed's whole cycle. "inf" likewise must not become a wait.
                httpx.Response(429, headers={"Retry-After": "nan"}),
                httpx.Response(429, headers={"Retry-After": "inf"}),
                httpx.Response(429, headers={"Retry-After": "-5"}),
                httpx.Response(200, content=self._EMPTY_ATOM),
            ]
        )
        self.assertEqual(payloads, [])
        # Absent, HTTP-date, nan, inf, negative: every unusable header takes
        # the exponential arm, base * 2^attempt, capped at the delay ceiling.
        self.assertEqual(sleeps, [2.0, 4.0, 8.0, 16.0, 32.0])

    def test_exhausted_retries_raise_http_status_error(self) -> None:
        from sources.connectors.reddit.connector import MAX_RATE_LIMIT_RETRIES

        with self.assertRaises(httpx.HTTPStatusError):
            self._poll_with([httpx.Response(429, headers={"Retry-After": "1"})] * (MAX_RATE_LIMIT_RETRIES + 1))

    def test_missing_subreddit_raises_a_recoverable_error(self) -> None:
        # ConnectorParseError is in the polling seam's _RECOVERABLE_ERRORS,
        # so one bad spec row degrades to a failed source; a bare ValueError
        # here would abort the whole feed cycle for every later source.
        spec = RedditSubredditSourceSpec(kind="reddit_subreddit", subreddit="")
        with self.assertRaises(ConnectorParseError):
            list(RedditSubRedditConnector().poll(spec, since=None))

    def test_heartbeat_ticks_through_the_wait(self) -> None:
        # A 60s wait sleeps in HEARTBEAT_SLEEP_CHUNK_SECONDS (15s) chunks,
        # ticking the heartbeat after each so the poll lease renews DURING
        # the backoff, not just between sources.
        ticks = {"n": 0}

        def hb() -> bool:
            ticks["n"] += 1
            return True

        payloads, sleeps = self._poll_with(
            [
                httpx.Response(429, headers={"Retry-After": "60"}),
                httpx.Response(200, content=self._EMPTY_ATOM),
            ],
            heartbeat=hb,
        )
        self.assertEqual(payloads, [])
        self.assertEqual(sleeps, [15.0, 15.0, 15.0, 15.0])
        self.assertEqual(ticks["n"], 4)


class FeedItemPayloadParityTests(SimpleTestCase):
    """Guard the hand-mirrored schema payloads against silent drift.

    `openmagpie_schema.feed.FeedItemData` mirrors the server's SourcePayload
    classes (apps/core/sources/connectors/*) so the CLI can type FeedItem.data.
    The schema package is zero-Django, so it CAN'T import the producers to check
    itself; this test can import both sides. It walks the connector payload
    registry and asserts every PAYLOAD_KIND has a matching schema variant with
    the same field set. When a connector adds/renames a payload field (or a whole
    payload), mirror it in the schema and this goes green again.

    Field NAMES only: a type or required-ness change (e.g. `categories: list[str]`
    -> `dict`, or dropping a default) is NOT caught, because a strict annotation
    compare would false-positive on the deliberate differences (server's `kind:
    str` vs the schema variant's `kind: Literal[...]`; server-required fields the
    read wire defaults). Name parity is the cheap, false-positive-free guard."""

    def _schema_variants_by_kind(self) -> dict[str, type[BaseModel]]:
        # FeedItemData is Annotated[RssEntryPayload | ... | FeedItemPayload, Field].
        union = get_args(FeedItemData)[0]
        out: dict[str, type[BaseModel]] = {}
        for member in get_args(union):
            ann = member.model_fields["kind"].annotation
            if get_origin(ann) is Literal:  # variants only; the base's `kind: str` is the fallback arm
                (literal,) = get_args(ann)
                out[literal] = member
        return out

    def test_connector_payloads_and_schema_variants_match(self) -> None:
        schema = self._schema_variants_by_kind()
        registered = payload_registry.registered()
        server_kinds = {kind for (_source, kind) in registered}
        self.assertEqual(
            server_kinds,
            set(schema),
            "connector PAYLOAD_KINDs and schema FeedItemData variants drifted; "
            "add/remove the mirror in openmagpie_schema.feed",
        )
        for (_source, kind), server_cls in registered.items():
            with self.subTest(kind=kind):
                self.assertEqual(
                    set(server_cls.model_fields),
                    set(schema[kind].model_fields),
                    f"{server_cls.__name__} fields drifted from schema {schema[kind].__name__}; "
                    "update the mirror in openmagpie_schema.feed",
                )
