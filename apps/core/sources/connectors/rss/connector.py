"""Generic RSS / Atom connector.

One GET per cycle (RSS feeds don't paginate), parsed by `feedparser`,
yielding `RssEntryPayload` for entries with `occurred_at > since`.
Unlike Reddit's connector this works against any feed URL ; the per-
publisher quirks (which key holds the body, which holds the author)
are absorbed by the `field_map` override threaded through from the
Source row + feed default."""

import logging
import ssl
from collections.abc import Callable, Iterator
from datetime import datetime

import feedparser
import httpx
from django.conf import settings

from openmagpie_schema.configs import RssSourceSpec
from sources.payload_registry import register
from sources.payloads import SourcePayload

from ..base import BaseConnector, ConnectorParseError, read_response_capped, validate_request_url
from ..challenge_bypass import ChallengeBypassMixin
from .payloads import RssEntryPayload

logger = logging.getLogger("sources")

# Polite default UA ; some publishers 403 the bare `python-httpx/...` UA.
# Identify the project so a publisher can correlate traffic if they look.
RSS_USER_AGENT = "openmagpie-rss/1.0 (+https://github.com/obris-dev/openmagpie)"

# Cap how many bytes we accumulate from a single feed in one cycle.
# RSS feeds are typically <100KB; a >5MB body is either a misconfigured
# endpoint (serving the full archive) or a hostile target. Streamed +
# checked per chunk so we never buffer past the cap (unlike a
# `response.content`-then-len check, which materializes the full body
# before deciding).
MAX_BODY_BYTES = 5 * 1024 * 1024

# FlareSolverr renders pages in headless Chromium. For an XML feed, Chromium
# returns its built-in XML VIEWER -- an HTML page that pretty-prints the
# document and stashes the ORIGINAL source in a
# <div id="webkit-xml-viewer-source-xml">. So a challenge-bypass fetch of an
# RSS/Atom URL comes back as that wrapper, not raw feed XML, and feedparser
# sees HTML and bails. The marker is how we recognise the wrapper.
_XML_VIEWER_MARKER = "webkit-xml-viewer-source-xml"


def _unwrap_xml_viewer(body: bytes) -> bytes:
    """Return the feed XML embedded in a Chromium XML-viewer wrapper, or
    `body` unchanged when it isn't one (safe to call on any bypass body).

    Slices from the feed root (`<?xml` / `<rss` / `<feed`) to the LAST
    `</rss>` / `</feed>`, rather than matching the source div's `</div>`:
    a feed whose CDATA description contains a literal `</div>` would
    truncate a naive div match. The first root marker is the real root
    (it precedes any item content); the last close is the real close."""
    text = body.decode("utf-8", "replace")
    if _XML_VIEWER_MARKER not in text:
        return body
    starts = [i for i in (text.find("<?xml"), text.find("<rss"), text.find("<feed")) if i != -1]
    ends = [i + len(tag) for tag in ("</rss>", "</feed>") if (i := text.rfind(tag)) != -1]
    if not starts or not ends:
        return body
    start, end = min(starts), max(ends)
    if end <= start:
        return body
    return text[start:end].encode("utf-8")


class RssConnector(ChallengeBypassMixin, BaseConnector[RssSourceSpec]):
    """Polls a single RSS or Atom feed URL.

    Live-mode semantics: every cycle yields entries newer than `since`,
    where `since` is the Source row's `last_event_at`. `feeds.policy`
    stamps `last_event_at = now()` at save time so the first cycle just
    returns whatever's been published since the source was created.

    Backfill is opt-in at source creation via `SourceInput.last_event_at`
    (the operator can pin a past datetime). There's no walk-the-archive
    mode ; RSS feeds typically don't expose history past the latest N
    items anyway, so a true backfill needs a different mechanism
    (sitemap, archive-only feeds, ...) that doesn't belong in this
    connector.

    `field_map` recognised keys: `external_id`, `title`, `url`,
    `content`, `author`, `published`. Each is the feedparser-entry
    key to read INSTEAD of the canonical default (e.g. `entry.id` for
    external_id). Most feeds need no overrides ; feedparser normalizes
    RSS / Atom / dc:* differences itself. Unknown override keys are
    read from the entry as-is so a publisher with a namespaced field
    can use `{"author": "itunes_author"}` without a connector change.
    Unknown canonical names in `field_map` are silently dropped."""

    kind = RssSourceSpec.SOURCE_KIND
    payloads: list[type[SourcePayload]] = [RssEntryPayload]

    def _fetch_with_ssl_fallback(self, url: str) -> bytes:
        """Stream the URL body. When `SOURCE_ALLOW_INSECURE_TLS=true`,
        retry once with `verify=False` if the first attempt fails an SSL
        check ; some publisher feeds sit behind stale / self-signed /
        wrong-name certs but serve a valid body once chain verification
        is dropped. The retry is opt-in because verify=False is a real
        MITM downgrade ; operators who want feed-content integrity over
        broken-publisher tolerance leave the setting off (the default)
        and SSL failures propagate normally.

        SSL detection is type-based (`isinstance(exc.__cause__,
        ssl.SSLError)`) not string-matched — see the bozo comment below
        for why we generally avoid matching on exception text.

        The httpx client carries `_validate_request_url` as a request
        hook so every redirect target is re-checked under
        `SOURCE_BLOCK_PRIVATE_IPS` (a 302 from a public host to a
        link-local / metadata-service address raises before httpx
        fetches the inner target). `read_response_capped` streams +
        checks per chunk so the MAX_BODY_BYTES gate runs before bytes
        are buffered."""

        def _stream(verify: bool) -> bytes:
            with (
                httpx.Client(
                    event_hooks={"request": [validate_request_url]},
                    follow_redirects=True,
                    timeout=15.0,
                    headers={"User-Agent": RSS_USER_AGENT},
                    verify=verify,
                ) as client,
                client.stream("GET", url) as response,
            ):
                response.raise_for_status()
                return read_response_capped(response, max_bytes=MAX_BODY_BYTES, url_label=f"rss feed {url}")

        try:
            return _stream(verify=True)
        except httpx.ConnectError as exc:
            if isinstance(exc.__cause__, ssl.SSLError) and settings.SOURCE_ALLOW_INSECURE_TLS:
                # WARN (not INFO) because verify=False is consequential
                # enough that an operator watching the log should see it
                # even when they enabled the setting deliberately.
                logger.warning(
                    "rss: SSL verify failed for %s, retrying with verify=False (SOURCE_ALLOW_INSECURE_TLS=true)",
                    url,
                )
                return _stream(verify=False)
            raise

    def poll(
        self,
        spec: RssSourceSpec,
        since: datetime | None,
        field_map: dict[str, str] | None = None,
        # Accepted per the Connector contract; unused, this connector has no
        # long waits to tick a heartbeat through (single fetch, no retry sleeps).
        heartbeat: Callable[[], bool] | None = None,
    ) -> Iterator[RssEntryPayload]:
        field_map = field_map or {}

        # Client carries the per-request hook so every redirect target
        # is re-checked under SOURCE_BLOCK_PRIVATE_IPS (a 302 to a
        # link-local / metadata-service address raises before httpx
        # fetches the inner target). `read_response_capped` streams +
        # checks per chunk so the MAX_BODY_BYTES gate runs before the
        # bytes are buffered.
        body = self._fetch_with_ssl_fallback(spec.url)

        parsed = feedparser.parse(body)
        # The "is this even a feed?" gate keys on `parsed.version`. Real
        # feeds always set `version` (`'rss20'`, `'atom10'`, ...) ; HTML
        # rate-limit / block pages parse as bozo=False, version=''
        # which would otherwise silently land as "no new posts" and
        # never surface the upstream block.
        #
        # Bozo is intentionally NOT a fail trigger here. feedparser
        # raises bozo=1 with a SAX exception for non-fatal quirks like
        # an undeclared `dc:` namespace prefix (very common: most RSS
        # 2.0 feeds use `<dc:date>` without declaring xmlns:dc), and a
        # truncated body raises the same exception class. Failing on
        # bozo would mean a valid feed with a warning + a genuinely
        # empty cycle gets thrown out, AND we couldn't reliably
        # discriminate from a hard parse failure without matching on
        # exception messages. Trade-off: truncated XML returning zero
        # recovered entries reads as "empty cycle" instead of an
        # error ; next poll picks it up when the publisher recovers.
        # `getattr(..., "")` because some inputs (empty body, completely
        # non-XML) cause feedparser to return a FeedParserDict that
        # raises AttributeError on `.version` access instead of returning
        # the empty string. Defensive accessor keeps a single bad source
        # from aborting the whole feed's poll cycle.
        if not parsed.entries and not getattr(parsed, "version", ""):
            # Anti-bot JS-challenge fallback (Cloudflare, Imperva,
            # DDoS-Guard, ...): hand the URL to the FlareSolverr sidecar,
            # which drives a real browser to pass the challenge and
            # returns the eventual response body. If THAT body parses,
            # continue with the recovered payloads ; if not, raise
            # so the per-source skip path logs and moves on. No-op when
            # the sidecar URL is empty or unreachable.
            bypass_body = self.challenge_bypass_fetch(spec.url, max_bytes=MAX_BODY_BYTES)
            if bypass_body:
                # FlareSolverr's headless Chromium renders an XML feed as the
                # browser's XML-viewer HTML wrapper ; recover the embedded
                # source so feedparser sees real feed XML, not the viewer page.
                bypass_body = _unwrap_xml_viewer(bypass_body)
                bypass_parsed = feedparser.parse(bypass_body)
                if bypass_parsed.entries or getattr(bypass_parsed, "version", ""):
                    logger.info("rss: challenge-bypass succeeded for %s", spec.url)
                    parsed = bypass_parsed
            if not parsed.entries and not getattr(parsed, "version", ""):
                raise ConnectorParseError(
                    f"rss feed {spec.url} returned an unparseable body (no feed format detected; "
                    "version='', 0 entries) ; likely an HTML block / rate-limit page or non-feed URL"
                )

        for entry in parsed.entries:
            payload, missing = RssEntryPayload.from_feedparser_entry(entry, spec, field_map)
            if payload is None:
                # Named so the operator can spot which `field_map`
                # override the publisher needs (e.g. "missing
                # external_id" on a feed that puts the id in
                # `<media:content url=...>` -> set `field_map:
                # external_id: media_content`). DEBUG by default
                # because well-behaved feeds shouldn't trip this;
                # WARN here would spam production logs for a
                # publisher who's missing one row's pubDate.
                logger.debug(
                    "rss: skipped entry on %s (missing %s): %r",
                    spec.url,
                    missing,
                    entry.get("title", "<no title>"),
                )
                continue
            # Strict `<`, not `<=`: two distinct entries can share a
            # pubDate to the second (publisher batches, scheduled
            # cron). Dropping on tie permanently loses the second
            # entry because the watermark already crossed its second
            # in the prior cycle. The downstream `external_id` dedup
            # on FeedItem is idempotent, so re-yielding the boundary
            # item that legitimately repeats is suppressed at the
            # recorder seam.
            if since is not None and payload.occurred_at < since:
                continue
            yield payload


# Register payloads for hydration of FeedItem.data, single source of truth via the class attrs.
register(RssConnector.kind, RssConnector.payloads)
