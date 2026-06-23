"""`poll_combined` (the reddit multireddit batch fetch).

Split from `sources/tests.py` to keep that file under the length cap. Pins the
connector-side half of the batch optimization: ONE `/r/a+b+c/new.rss` request,
each payload tagged with its own subreddit, `since` bounding the walk. The
poll-op side (scatter + watermark converge) is pinned by
`feeds.tests_reddit_batch`.
"""

from datetime import UTC, datetime
from unittest import mock

import httpx
from django.test import SimpleTestCase

from sources.connectors.base import ConnectorParseError
from sources.connectors.reddit.connector import RedditSubRedditConnector


class RedditCombinedPollTests(SimpleTestCase):
    """`poll_combined` fetches the `/r/a+b+c/new.rss` multireddit in ONE request
    and tags each payload with its OWN subreddit (the Atom `<category term>`), so
    the poll op can scatter per-sub. `since` bounds the walk exactly like the
    single-sub poll (early-return on the first post older than the cursor)."""

    _EMPTY_ATOM = b'<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"></feed>'

    @staticmethod
    def _entry(post_id: str, sub: str, published: str) -> str:
        return (
            "<entry>"
            f"<id>t3_{post_id}</id><title>post {post_id}</title>"
            f'<link href="https://www.reddit.com/r/{sub}/comments/{post_id}/x/"/>'
            f"<published>{published}</published>"
            f'<category term="{sub}" label="r/{sub}"/>'
            "<author><name>/u/u</name></author>"
            '<content type="html">body</content>'
            "</entry>"
        )

    def _feed(self, *entries: str) -> bytes:
        return (
            '<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">' + "".join(entries) + "</feed>"
        ).encode("utf-8")

    def _poll_combined(self, subreddits, since, responses):
        queue = list(responses)
        seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(str(request.url))
            return queue.pop(0)

        transport = httpx.MockTransport(handler)
        real_client = httpx.Client
        with mock.patch(
            "sources.connectors.reddit.connector.httpx.Client",
            lambda **kw: real_client(transport=transport, **kw),
        ):
            payloads = list(RedditSubRedditConnector().poll_combined(subreddits, since=since))
        return payloads, seen

    def test_one_multireddit_request_tags_each_post_with_its_own_sub(self) -> None:
        body = self._feed(
            self._entry("a1", "alpha", "2026-06-23T12:00:00+00:00"),
            self._entry("b1", "beta", "2026-06-23T11:00:00+00:00"),
            self._entry("a2", "alpha", "2026-06-23T10:00:00+00:00"),
        )
        # Page 2 empty so pagination stops after the single populated page.
        payloads, seen = self._poll_combined(
            ["alpha", "beta"], None, [httpx.Response(200, content=body), httpx.Response(200, content=self._EMPTY_ATOM)]
        )
        self.assertIn("/r/alpha+beta/new/.rss", seen[0])
        self.assertEqual(
            [(p.external_id, p.subreddit) for p in payloads],
            [("a1", "alpha"), ("b1", "beta"), ("a2", "alpha")],
        )

    def test_since_bounds_the_combined_walk(self) -> None:
        body = self._feed(
            self._entry("a1", "alpha", "2026-06-23T12:00:00+00:00"),
            self._entry("b1", "beta", "2026-06-23T11:00:00+00:00"),
            self._entry("a2", "alpha", "2026-06-23T10:00:00+00:00"),
        )
        # a2 (10:00) is strictly older than the cursor -> early-return, no page 2.
        payloads, _ = self._poll_combined(
            ["alpha", "beta"], datetime(2026, 6, 23, 10, 30, tzinfo=UTC), [httpx.Response(200, content=body)]
        )
        self.assertEqual([p.external_id for p in payloads], ["a1", "b1"])

    def test_no_subreddits_raises_a_recoverable_error(self) -> None:
        with self.assertRaises(ConnectorParseError):
            list(RedditSubRedditConnector().poll_combined([], since=None))

    def test_entry_without_a_subreddit_tag_raises(self) -> None:
        # An entry missing its <category term> can't be routed per-sub, so we
        # surface it as a recoverable parse error rather than silently drop it.
        entry_no_category = (
            "<entry><id>t3_x1</id><title>untagged</title>"
            '<link href="https://www.reddit.com/r/alpha/comments/x1/x/"/>'
            "<published>2026-06-23T12:00:00+00:00</published>"
            "<author><name>/u/u</name></author>"
            '<content type="html">body</content></entry>'
        )
        with self.assertRaises(ConnectorParseError):
            self._poll_combined(["alpha"], None, [httpx.Response(200, content=self._feed(entry_no_category))])
