from typing import Literal, get_args, get_origin
from unittest import mock

import feedparser
from django.test import SimpleTestCase
from pydantic import BaseModel

from openmagpie_schema.configs import RssSourceSpec
from openmagpie_schema.feed import FeedItemData
from sources import (
    payload_registry,
    registry,  # noqa: F401  pulls in the connectors, which self-register their payloads
)
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
