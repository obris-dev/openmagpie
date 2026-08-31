"""Twikit-based X (Twitter) client, unofficial route.

Ported from REPOS/listeningkit/backend/packages/listeners/twitter/{client,
config,credentials}.py so the openmagpie connector keeps the exact cookie
JSON formats the live listeningkit setup uses (they work today; nothing
about them is omitted or reshaped):

- `TWITTER_COOKIES_JSON` (a full JSON dict of x.com cookies) — the live
  `.env.local` source,
- `TWITTER_COOKIE_AUTH_TOKEN` + `TWITTER_COOKIE_CT0` (the critical pair),
- `TWITTER_COOKIES_FILE` (path to a twikit cookies.json),
- `TWITTER_CREDENTIALS_DIR` (dir of `*.json` cookie exports, each with an
  optional `*.proxy` pin), default `credentials/` relative to the core app.

Proxy: `TWITTER_PROXY` env (or a per-credential `.proxy` pin in
`TWITTER_CREDENTIALS_DIR`). twikit supports `proxy=` natively, so every
request egresses through it (transport-level).

twikit is async-only; the connector's `poll` is a sync iterator, so
`TwikitClient.search` bridges with a fresh event loop per call
(`asyncio.run`). One search per poll cycle is a bounded, short-lived
loop; no shared loop state to leak across poll cycles.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Literal

from twikit import Client
from twikit.errors import TwitterException

from .errors import ListenerError, map_bootstrap_failure, map_twikit_error

# The product string twikit passes to X's search endpoint. The spec's
# `latest` / `top` literals map 1:1 onto these (see connector.py).
TwikitProduct = Literal["Latest", "Top"]

log = logging.getLogger("sources.twitter")

# The two cookies that make an authenticated twikit session work.
CRITICAL_COOKIES = ("auth_token", "ct0")


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


def load_cookies(
    *,
    cookies_json: str | None = None,
    cookies_file: str | None = None,
    credentials_dir: str | None = None,
) -> tuple[dict[str, str], str | None]:
    """Resolve the cookie dict + proxy for one X session, in priority order.

    Returns ``(cookies, proxy)``. ``cookies`` is empty when nothing is
    configured (the caller stays guest-mode; the first search then fails
    with a mapped ``unauthorized`` error the poll loop handles). ``proxy``
    comes from `TWITTER_PROXY` or a `<name>.proxy` pin next to the chosen
    cookie export.
    """
    proxy = os.environ.get("TWITTER_PROXY", "").strip() or None

    if cookies_json:
        try:
            data = json.loads(cookies_json)
        except json.JSONDecodeError:
            log.warning("TWITTER_COOKIES_JSON is not valid JSON; ignoring")
        else:
            if isinstance(data, dict) and data:
                return {str(k): str(v) for k, v in data.items()}, proxy
            log.warning("TWITTER_COOKIES_JSON is not a non-empty JSON object; ignoring")

    individual = {name: os.environ.get(f"TWITTER_COOKIE_{name.upper()}", "").strip() for name in CRITICAL_COOKIES}
    if all(individual.values()):
        return individual, proxy

    if cookies_file and Path(cookies_file).exists():
        try:
            return _load_cookie_file(Path(cookies_file)), proxy
        except (json.JSONDecodeError, ValueError, OSError) as exc:
            log.warning("TWITTER_COOKIES_FILE %s unreadable (%s); ignoring", cookies_file, exc)

    directory = Path(credentials_dir or os.environ.get("TWITTER_CREDENTIALS_DIR", "credentials"))
    if directory.exists():
        for path in sorted(directory.glob("*.json")):
            try:
                cookies = _load_cookie_file(path)
            except (json.JSONDecodeError, ValueError, OSError) as exc:
                log.warning("skipping %s: %s", path.name, exc)
                continue
            if not {"auth_token", "ct0"}.issubset(cookies):
                log.warning("skipping %s: missing auth_token/ct0 (critical pair)", path.name)
                continue
            proxy_path = path.with_suffix(".proxy")
            pin = proxy_path.read_text().strip() if proxy_path.exists() else proxy
            return cookies, pin
        log.warning("credentials dir %s: no usable cookie set found", directory)
    elif credentials_dir:
        log.warning("credentials dir %s not found; no sessions loaded", credentials_dir)

    return {}, proxy


class ListenerErrorWrapper(Exception):
    """Carries a canonical ListenerError through the pipeline."""

    def __init__(self, err: ListenerError) -> None:
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
    multi-source feed poll). Cookies are resolved once here (cheap env /
    file reads); only the twikit client is per-call.
    """

    def __init__(
        self,
        *,
        language: str = "en-US",
        proxy: str | None = None,
        user_agent: str | None = None,
        cookies: dict[str, str] | None = None,
        cookies_json: str | None = None,
        cookies_file: str | None = None,
        credentials_dir: str | None = None,
    ) -> None:
        self._language = language
        self._user_agent = user_agent
        self.proxy = proxy
        if cookies is not None:
            self._cookies = dict(cookies)
        else:
            self._cookies, self.proxy = load_cookies(
                cookies_json=cookies_json,
                cookies_file=cookies_file,
                credentials_dir=credentials_dir,
            )

    async def _search_async(self, query: str, mode: TwikitProduct, count: int):
        # Build the twikit client here, inside the event loop search()
        # runs: twikit's Client binds its httpx.AsyncClient to this loop
        # at construction (see class docstring).
        client = Client(language=self._language, proxy=self.proxy, user_agent=self._user_agent)
        if self._cookies:
            client.set_cookies(dict(self._cookies), clear_cookies=True)
            log.info("session: loaded %d cookie(s)", len(self._cookies))
        else:
            log.warning("session: no cookies configured; guest mode only")
        try:
            return await client.search_tweet(query, mode, count=count)
        except TwitterException as exc:
            raise ListenerErrorWrapper(map_twikit_error(exc, {"query": query, "mode": mode})) from exc
        except Exception as exc:  # bootstrap failures (degraded shell) are not TwitterException
            raise ListenerErrorWrapper(map_bootstrap_failure(exc, {"query": query})) from exc

    def search(self, query: str, mode: TwikitProduct = "Latest", count: int = 20):
        """Run one live search; returns a twikit Result[Tweet] (or a test double)."""
        return asyncio.run(self._search_async(query, mode, count))
