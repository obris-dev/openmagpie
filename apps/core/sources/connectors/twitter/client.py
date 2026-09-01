"""Twikit-based X (Twitter) client, unofficial route.

Session material comes from the `TWITTER_*` settings (see
conf/settings/base.py for the priority chain: inline cookie JSON, the
auth_token/ct0 pair, one cookie-export file, or a credentials directory of
exports with optional `.proxy` pins). Cookie formats are the exact ones the
live listeningkit setup used (a plain JSON dict, or a
Get-cookies.txt-LOCALLY array export).

Resolution happens per search call, not at import: settings are cheap env
reads plus at most a small file, `@override_settings` works in tests, and a
rotated cookie export applies on the next poll without a process restart.

Proxy: `TWITTER_PROXY` (or a per-credential `.proxy` pin in the credentials
dir). twikit supports `proxy=` natively, so every request egresses through
it (transport-level).

twikit is async-only; the connector's `poll` is a sync iterator, so
`TwikitClient.search` bridges with a fresh event loop per call
(`asyncio.run`). One search per poll cycle is a bounded, short-lived
loop; no shared loop state to leak across poll cycles.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Callable
from pathlib import Path
from typing import Literal

from django.conf import settings
from twikit import Client
from twikit.errors import TwitterException

from ..base import rate_limit_delay, sleep_with_heartbeat
from .errors import RATE_LIMITED, TwitterError, map_bootstrap_failure, map_twikit_error

# The product string twikit passes to X's search endpoint. The spec's
# `latest` / `top` literals map 1:1 onto these (see connector.py).
TwikitProduct = Literal["Latest", "Top"]

log = logging.getLogger("sources.twitter")

# The two cookies that make an authenticated twikit session work.
CRITICAL_COOKIES = ("auth_token", "ct0")

# Rate-limit retry, mirroring the Reddit connector's 429 loop: on a
# `rate_limited` error the client backs off and retries in-cycle rather than
# failing the source immediately. This is brief SMOOTHING, not a full wait to
# reset: X's search window is ~15 min, but the delay is capped well under the
# poll lease (POLL_LOCK_TIMEOUT_SECONDS, 600s), so a genuine rate-limit
# exhausts these retries and defers to the next scheduled poll (whose watermark
# stays put) rather than pinning a worker for a quarter hour.
MAX_RATE_LIMIT_RETRIES = 3
RATE_LIMIT_BACKOFF_BASE_SECONDS = 5.0  # 5s, 10s, 20s when no usable reset is present
RATE_LIMIT_DELAY_CAP_SECONDS = 60.0


def _reset_delay(reset_epoch: int | None, attempt: int) -> float:
    """Delay before retrying a rate-limited search. X sends `x-rate-limit-reset`
    as an ABSOLUTE Unix epoch (unlike Reddit's relative seconds), so convert to
    a relative wait (`reset - now`) and defer to the shared `rate_limit_delay`
    for the exponential fallback + cap. Capped at RATE_LIMIT_DELAY_CAP_SECONDS,
    so for X's ~15-min window this returns the cap, not the full reset (see the
    retry-loop note above)."""
    remaining = reset_epoch - time.time() if reset_epoch is not None else None
    return rate_limit_delay(remaining, attempt, base=RATE_LIMIT_BACKOFF_BASE_SECONDS, cap=RATE_LIMIT_DELAY_CAP_SECONDS)


def _load_cookie_file(path: Path) -> dict[str, str]:
    """Parse a Get-cookies.txt-LOCALLY JSON export (array) or a plain dict."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        return {str(k): str(v) for k, v in data.items() if v}
    if isinstance(data, list):
        out: dict[str, str] = {}
        for item in data:
            name, value = item.get("name"), item.get("value")
            if name and value:
                out[str(name)] = str(value)
        return out
    raise ValueError(f"{path}: JSON must be an object or an array of cookie objects")


def load_cookies() -> tuple[dict[str, str], str | None]:
    """Resolve the cookie dict + proxy for one X session from the
    `TWITTER_*` settings, in priority order (see conf/settings/base.py).

    Returns ``(cookies, proxy)``. ``cookies`` is empty when nothing is
    configured (the caller stays guest-mode; the first search then fails
    with a mapped ``unauthorized`` error the poll loop handles). ``proxy``
    comes from `TWITTER_PROXY` or a `<name>.proxy` pin next to the chosen
    cookie export.
    """
    proxy = settings.TWITTER_PROXY.strip() or None

    if settings.TWITTER_COOKIES_JSON:
        try:
            data = json.loads(settings.TWITTER_COOKIES_JSON)
        except json.JSONDecodeError:
            log.warning("TWITTER_COOKIES_JSON is not valid JSON; ignoring")
        else:
            cookies = {str(k): str(v) for k, v in data.items() if v} if isinstance(data, dict) else {}
            if set(CRITICAL_COOKIES).issubset(cookies):
                return cookies, proxy
            log.warning("TWITTER_COOKIES_JSON missing auth_token/ct0 (critical pair); ignoring")

    individual = {name: getattr(settings, f"TWITTER_COOKIE_{name.upper()}").strip() for name in CRITICAL_COOKIES}
    if all(individual.values()):
        return individual, proxy

    cookies_file = settings.TWITTER_COOKIES_FILE
    if cookies_file and Path(cookies_file).exists():
        try:
            return _load_cookie_file(Path(cookies_file)), proxy
        except (json.JSONDecodeError, ValueError, OSError) as exc:
            log.warning("TWITTER_COOKIES_FILE %s unreadable (%s); ignoring", cookies_file, exc)

    directory = Path(settings.TWITTER_CREDENTIALS_DIR)
    if directory.exists():
        for path in sorted(directory.glob("*.json")):
            try:
                cookies = _load_cookie_file(path)
            except (json.JSONDecodeError, ValueError, OSError) as exc:
                log.warning("skipping %s: %s", path.name, exc)
                continue
            if not set(CRITICAL_COOKIES).issubset(cookies):
                log.warning("skipping %s: missing auth_token/ct0 (critical pair)", path.name)
                continue
            proxy_path = path.with_suffix(".proxy")
            pin = proxy
            if proxy_path.exists():
                try:
                    # An empty pin file falls back to the global proxy, not "".
                    pin = proxy_path.read_text(encoding="utf-8").strip() or proxy
                except OSError as exc:
                    log.warning("proxy pin %s unreadable (%s); using default proxy", proxy_path.name, exc)
            return cookies, pin
        log.warning("credentials dir %s: no usable cookie set found", directory)
    # A missing directory is a bare install (only the README ships in-repo),
    # not misconfiguration; the guest-mode warning fires at search time.

    return {}, proxy


class TwitterErrorWrapper(Exception):
    """Carries a canonical TwitterError through the pipeline."""

    def __init__(self, err: TwitterError) -> None:
        super().__init__(err.message)
        self.error = err


class TwikitClient:
    """Thin, proxy-bound wrapper around the twikit async client.

    One twikit ``Client`` per search call. twikit's ``Client.__init__``
    creates an ``httpx.AsyncClient`` bound to the *currently running*
    event loop, and ``search()`` drives twikit with a fresh
    ``asyncio.run`` loop per call — so the twikit client must be
    constructed inside that loop. A shared instance created at import
    time dies with the first loop and later searches fail with
    "Event loop is closed" (observed on the second/third source in a
    multi-source feed poll). Cookies + proxy are ALSO resolved per call
    (from the `TWITTER_*` settings via `load_cookies`), so a rotated
    cookie export or env change applies on the next poll without a
    restart; an explicit `cookies`/`proxy` constructor arg pins them
    instead (tests).
    """

    def __init__(
        self,
        *,
        language: str = "en-US",
        proxy: str | None = None,
        user_agent: str | None = None,
        cookies: dict[str, str] | None = None,
    ) -> None:
        self._language = language
        self._user_agent = user_agent
        self._pinned_proxy = proxy
        self._pinned_cookies = dict(cookies) if cookies is not None else None

    async def _search_async(self, query: str, mode: TwikitProduct, count: int):
        if self._pinned_cookies is not None:
            cookies, proxy = self._pinned_cookies, self._pinned_proxy
        else:
            cookies, proxy = load_cookies()
            proxy = self._pinned_proxy or proxy
        # Build the twikit client here, inside the event loop search()
        # runs: twikit's Client binds its httpx.AsyncClient to this loop
        # at construction (see class docstring).
        client = Client(language=self._language, proxy=proxy, user_agent=self._user_agent)
        if cookies:
            client.set_cookies(dict(cookies), clear_cookies=True)
            log.info("session: loaded %d cookie(s)", len(cookies))
        else:
            log.warning("session: no cookies configured; guest mode only")
        try:
            return await client.search_tweet(query, mode, count=count)
        except TwitterException as exc:
            raise TwitterErrorWrapper(map_twikit_error(exc, {"query": query, "mode": mode})) from exc
        except Exception as exc:  # bootstrap failures (degraded shell) are not TwitterException
            raise TwitterErrorWrapper(map_bootstrap_failure(exc, {"query": query})) from exc

    def _run_search(self, query: str, mode: TwikitProduct, count: int):
        """One search attempt (a fresh event loop per call, see class
        docstring). The rate-limit seam `search` retries around."""
        return asyncio.run(self._search_async(query, mode, count))

    def search(
        self,
        query: str,
        mode: TwikitProduct = "Latest",
        count: int = 20,
        heartbeat: Callable[[], bool] | None = None,
    ):
        """Run one live search; returns a twikit Result[Tweet] (or a test double).

        On a `rate_limited` error, back off toward `x-rate-limit-reset`
        (capped) and retry, up to MAX_RATE_LIMIT_RETRIES, ticking `heartbeat`
        through the wait so the poll lease survives. This is in-cycle
        smoothing; a genuine rate-limit exhausts the retries and propagates to
        the connector's poll seam, which defers to the next scheduled poll.
        Any other error propagates immediately.
        """
        attempt = 0
        while True:
            try:
                return self._run_search(query, mode, count)
            except TwitterErrorWrapper as exc:
                if exc.error.code != RATE_LIMITED or attempt >= MAX_RATE_LIMIT_RETRIES:
                    raise
                delay = _reset_delay(exc.error.rate_limit_reset, attempt)
                log.warning(
                    "rate limited; retrying in %.0fs (attempt %d/%d)", delay, attempt + 1, MAX_RATE_LIMIT_RETRIES
                )
                sleep_with_heartbeat(delay, heartbeat)
                attempt += 1
