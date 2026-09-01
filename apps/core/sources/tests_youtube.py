"""YouTube search connector tests (offline, fake yt-dlp video dicts).

The connector's only I/O is the yt-dlp client (`YtDlpClient.search`); these
tests swap in fake video dicts and pin: spec validation (the blank-query
firehose guard), the day-granularity watermark filter (boundary yields, older
skips), error translation (YouTubeError -> ConnectorParseError), the client's
date-ordered search URI, and payload mapping (timestamp fallback chain,
metrics, thumbnails).
"""

from datetime import UTC, datetime
from typing import Any
from unittest import mock

from django.test import SimpleTestCase
from pydantic import ValidationError

from openmagpie_schema.configs import YouTubeSearchSourceSpec
from sources.connectors.base import ConnectorParseError
from sources.connectors.youtube.connector import YouTubeSearchConnector
from sources.connectors.youtube.errors import YouTubeError, map_ytdlp_error
from sources.connectors.youtube.payloads import NewVideoPayload


def _fake_video(video_id: str = "abc123", **overrides: Any) -> dict[str, Any]:
    video: dict[str, Any] = {
        "id": video_id,
        "title": "A video title",
        "description": "the description text",
        "uploader": "Some Channel",
        "uploader_id": "UCchannel",
        "upload_date": "20260601",
        "timestamp": datetime(2026, 6, 1, 15, 30, tzinfo=UTC).timestamp(),
        "duration": 120,
        "view_count": 1000,
        "like_count": 50,
        "comment_count": 5,
        "webpage_url": f"https://www.youtube.com/watch?v={video_id}",
        "thumbnails": [{"url": "https://i.ytimg.com/vi/x/hq.jpg", "width": 480, "height": 360}],
    }
    video.update(overrides)
    return video


class YouTubeSearchSourceSpecTests(SimpleTestCase):
    def test_blank_query_rejected(self):
        with self.assertRaises(ValidationError):
            YouTubeSearchSourceSpec(kind="youtube_search", query="   ")

    def test_count_bounds(self):
        with self.assertRaises(ValidationError):
            YouTubeSearchSourceSpec(kind="youtube_search", query="x", count=0)
        with self.assertRaises(ValidationError):
            YouTubeSearchSourceSpec(kind="youtube_search", query="x", count=51)

    def test_defaults(self):
        spec = YouTubeSearchSourceSpec(kind="youtube_search", query="social listening")
        self.assertEqual(spec.count, 20)


class YouTubeSearchConnectorTests(SimpleTestCase):
    def _connector(self, results):
        client = mock.Mock()
        client.search.return_value = results
        conn = YouTubeSearchConnector()
        conn._client = client
        return conn, client

    def test_yields_payloads_newer_than_since(self):
        spec = YouTubeSearchSourceSpec(kind="youtube_search", query='"social listening"')
        videos = [
            _fake_video("new", timestamp=datetime(2026, 6, 1, tzinfo=UTC).timestamp()),
            _fake_video("old", timestamp=datetime(2026, 5, 1, tzinfo=UTC).timestamp()),
        ]
        conn, client = self._connector(videos)
        payloads = list(conn.poll(spec, since=datetime(2026, 5, 15, tzinfo=UTC)))
        self.assertEqual([p.external_id for p in payloads], ["new"])
        client.search.assert_called_once_with('"social listening"', 20)

    def test_watermark_boundary_yields(self):
        """A video AT the watermark re-yields (day-granular timestamps; the
        external_id dedup absorbs it). Only strictly-older videos skip."""
        since = datetime(2026, 6, 1, tzinfo=UTC)
        spec = YouTubeSearchSourceSpec(kind="youtube_search", query="x")
        videos = [_fake_video("same-day", timestamp=None, upload_date="20260601")]
        conn, _ = self._connector(videos)
        payloads = list(conn.poll(spec, since=since))
        self.assertEqual([p.external_id for p in payloads], ["same-day"])

    def test_error_maps_to_connector_parse_error(self):
        spec = YouTubeSearchSourceSpec(kind="youtube_search", query="x")
        client = mock.Mock()
        client.search.side_effect = YouTubeError(
            code="rate_limited", message="slow down", retryable=True, action="backoff"
        )
        conn = YouTubeSearchConnector()
        conn._client = client
        with self.assertRaises(ConnectorParseError) as ctx:
            list(conn.poll(spec, since=None))
        self.assertIn("rate_limited", str(ctx.exception))


class MapYtdlpErrorTests(SimpleTestCase):
    def test_rate_limited_retryable(self):
        err = map_ytdlp_error(Exception("HTTP Error 429: Too Many Requests"))
        self.assertEqual(err.code, "rate_limited")
        self.assertTrue(err.retryable)

    def test_unavailable_not_retryable(self):
        err = map_ytdlp_error(Exception("Video unavailable"))
        self.assertEqual(err.code, "video_unavailable")
        self.assertFalse(err.retryable)

    def test_is_raisable_exception(self):
        err = map_ytdlp_error(Exception("boom"))
        with self.assertRaises(YouTubeError):
            raise err


class YtDlpClientSearchUriTests(SimpleTestCase):
    def test_cookie_file_setting_reaches_ytdlp_opts(self):
        from django.test import override_settings

        from sources.connectors.youtube.client import YtDlpClient

        with override_settings(YOUTUBE_COOKIES_FILE="/tmp/yt-cookies.txt"):
            self.assertEqual(YtDlpClient()._build_opts(5)["cookies"], "/tmp/yt-cookies.txt")
        with override_settings(YOUTUBE_COOKIES_FILE=""):
            self.assertNotIn("cookies", YtDlpClient()._build_opts(5))
        # An explicit constructor arg wins over the setting.
        with override_settings(YOUTUBE_COOKIES_FILE="/tmp/from-setting.txt"):
            self.assertEqual(
                YtDlpClient(cookie_file="/tmp/explicit.txt")._build_opts(5)["cookies"], "/tmp/explicit.txt"
            )

    def test_date_ordered_uri_and_count_cap(self):
        from sources.connectors.youtube.client import YtDlpClient

        with mock.patch("sources.connectors.youtube.client.yt_dlp.YoutubeDL") as ydl_cls:
            ydl = ydl_cls.return_value.__enter__.return_value
            ydl.extract_info.return_value = {
                "entries": [
                    _fake_video(),
                    None,
                    {"_type": "playlist", "id": "PLxyz", "title": "a playlist, not a video"},
                ]
            }
            results = YtDlpClient().search("brand mention", count=99)
        ydl.extract_info.assert_called_once_with(
            "https://www.youtube.com/results?search_query=brand+mention&sp=EgIIAw%3D%3D", download=False
        )
        self.assertEqual(ydl_cls.call_args.args[0]["playlistend"], 50)
        self.assertEqual([e["id"] for e in results], ["abc123"])


class NewVideoPayloadTests(SimpleTestCase):
    def test_from_video(self):
        p = NewVideoPayload.from_video(_fake_video("abc123"))
        self.assertEqual(p.external_id, "abc123")
        self.assertEqual(p.handle, "UCchannel")
        self.assertEqual(p.author, "Some Channel")
        self.assertEqual(p.content, "the description text")
        self.assertEqual(p.source, "youtube_search")
        self.assertEqual(p.url, "https://www.youtube.com/watch?v=abc123")
        self.assertEqual(p.metrics["views"], 1000)
        self.assertEqual(p.media[0]["type"], "thumbnail")

    def test_timestamp_beats_upload_date(self):
        p = NewVideoPayload.from_video(_fake_video())
        self.assertEqual(p.occurred_at, datetime(2026, 6, 1, 15, 30, tzinfo=UTC))

    def test_upload_date_fallback_floors_to_midnight(self):
        p = NewVideoPayload.from_video(_fake_video(timestamp=None, upload_date="20260601"))
        self.assertEqual(p.occurred_at, datetime(2026, 6, 1, tzinfo=UTC))

    def test_missing_dates_floor_to_today_midnight(self):
        p = NewVideoPayload.from_video(_fake_video(timestamp=None, upload_date=""))
        self.assertEqual(p.occurred_at, p.occurred_at.replace(hour=0, minute=0, second=0, microsecond=0))

    def test_sample_distinct(self):
        a = NewVideoPayload.sample(0)
        b = NewVideoPayload.sample(1)
        self.assertNotEqual(a.external_id, b.external_id)
        self.assertEqual(a.PAYLOAD_KIND, "new_video")
