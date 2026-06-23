"""Unit tests for `parse_rate_limit_wait` (sources/connectors/base.py).

Split out of `sources/tests.py` for the file-length cap. The Reddit connector's
end-to-end 429 backoff is in `RedditRateLimitBackoffTests` (sources/tests.py);
this covers the shared header-parsing helper in isolation.
"""

import httpx
from django.test import SimpleTestCase

from sources.connectors.base import parse_rate_limit_wait


class ParseRateLimitWaitTests(SimpleTestCase):
    """`parse_rate_limit_wait` reads a 429's wait from standard headers, or
    returns None so the caller's own backoff takes over."""

    def _resp(self, headers: dict[str, str]) -> httpx.Response:
        return httpx.Response(429, headers=headers)

    def test_numeric_retry_after(self) -> None:
        self.assertEqual(parse_rate_limit_wait(self._resp({"Retry-After": "7"})), 7.0)

    def test_x_ratelimit_reset_is_relative_seconds(self) -> None:
        # Reddit's format (verified live): seconds until the window resets.
        self.assertEqual(parse_rate_limit_wait(self._resp({"x-ratelimit-reset": "42"})), 42.0)

    def test_retry_after_wins_over_reset(self) -> None:
        self.assertEqual(parse_rate_limit_wait(self._resp({"Retry-After": "3", "x-ratelimit-reset": "99"})), 3.0)

    def test_unusable_retry_after_falls_through_to_reset(self) -> None:
        # The whole point of layering two headers: a present-but-unusable
        # Retry-After must not short-circuit to None - it falls through to the
        # X-RateLimit-Reset Reddit actually sends.
        self.assertEqual(parse_rate_limit_wait(self._resp({"Retry-After": "nan", "x-ratelimit-reset": "8"})), 8.0)

    def test_unusable_or_absent_returns_none(self) -> None:
        for headers in (
            {},  # no rate-limit headers at all
            {"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"},  # HTTP-date -> caller's backoff
            {"Retry-After": "nan"},
            {"Retry-After": "inf"},
            {"Retry-After": "-5"},
            {"x-ratelimit-reset": "0"},
            {"x-ratelimit-reset": "-1"},
        ):
            self.assertIsNone(parse_rate_limit_wait(self._resp(headers)), headers)
