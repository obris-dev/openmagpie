"""yt-dlp-based YouTube client for search extraction.

Wraps yt-dlp's YoutubeDL to perform YouTube searches without downloading
video content. Uses extract_flat mode for efficiency and handles errors
via the error taxonomy in errors.py.

Key patterns (ported from listeningkit Twitter client):
- One YtDlpClient instance per search call; yt-dlp is thread-safe for
  read-only extraction operations.
- Search queries use the `ytsearch<N>:<query>` URI scheme.
- Results are returned as dicts (not downloaded), containing metadata.
- No authentication required for public search; cookies optional for
  age-restricted content.
"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlencode

import yt_dlp
from django.conf import settings

from .errors import map_ytdlp_error

log = logging.getLogger("sources.youtube")

# Maximum results per search query. yt-dlp accepts up to 100 but we cap
# lower to match the Twitter connector's default count.
MAX_SEARCH_RESULTS = 50


class YtDlpClient:
    """Thin wrapper around yt-dlp for search-only extraction.

    No auth state: YouTube search is public. Optional cookie file can be
    passed for age-restricted content (not commonly needed for search).
    """

    def __init__(
        self,
        *,
        quiet: bool = True,
        no_warnings: bool = True,
        cookie_file: str | None = None,
    ) -> None:
        self._quiet = quiet
        self._no_warnings = no_warnings
        self._cookie_file = cookie_file

    def _build_opts(self, count: int) -> dict[str, Any]:
        opts: dict[str, Any] = {
            "quiet": self._quiet,
            "no_warnings": self._no_warnings,
            "extract_flat": False,  # need full metadata for payloads
            "skip_download": True,
            "playlistend": count,  # the count cap for the URL-based search
            # One unextractable entry (age-gated, region-locked, deleted) must
            # not abort the whole result page; it comes back as a None entry,
            # which search() filters out.
            "ignoreerrors": True,
        }
        # Constructor arg wins (tests); else the env-backed setting. Read
        # per-call, not at import, so @override_settings works and a rotated
        # cookie file applies without a process restart.
        cookie_file = self._cookie_file or settings.YOUTUBE_COOKIES_FILE
        if cookie_file:
            opts["cookies"] = cookie_file
        return opts

    def search(
        self,
        query: str,
        count: int = 20,
    ) -> list[dict[str, Any]]:
        """Run one YouTube search; returns list of video info dicts.

        Args:
            query: Search expression (keywords, phrases).
            count: Max results to fetch (capped at MAX_SEARCH_RESULTS).

        Returns:
            List of video metadata dicts, newest first.

        Raises:
            YouTubeError: On extraction failures (mapped from yt-dlp exceptions).
        """
        capped_count = min(count, MAX_SEARCH_RESULTS)
        # sp=EgIIAw= is YouTube's "Upload date: This week" FILTER. YouTube
        # removed sort-by-upload-date from search entirely (yt-dlp dropped
        # ytsearchdate for the same reason, yt-dlp/yt-dlp#15898), so recency
        # comes from restricting the window instead: results are
        # relevance-ranked but only from the last 7 days, and the watermark +
        # external_id dedup handle ordering. Old popular videos can't occupy
        # the N slots; a very busy query should raise `count` since relevance
        # picks which of the week's matches fill them.
        search_uri = f"https://www.youtube.com/results?{urlencode({'search_query': query, 'sp': 'EgIIAw=='})}"

        try:
            with yt_dlp.YoutubeDL(self._build_opts(capped_count)) as ydl:
                info = ydl.extract_info(search_uri, download=False)
                entries = (info or {}).get("entries", []) or []
                # Search results mix in playlists and channels; keep videos
                # only (full extraction marks them _type "video", or omits
                # _type on older yt-dlp versions).
                return [e for e in entries if e is not None and e.get("_type") in (None, "video")]
        except Exception as exc:
            err = map_ytdlp_error(exc, {"query": query, "count": capped_count})
            log.warning("youtube search failed query=%r code=%s: %s", query, err.code, err.message)
            raise err from exc
