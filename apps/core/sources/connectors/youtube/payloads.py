"""YouTube payloads: a video observed via yt-dlp search.

Maps YouTube video metadata to the openmagpie SourcePayload contract:
the engine judges title + content, so the video's description goes to
content and the uploader's name becomes the within-kind source_slug.
Metrics / refs / media stay on the payload as source-specific fields.
"""

from __future__ import annotations

from contextlib import suppress
from datetime import UTC, datetime
from typing import Any, ClassVar

from openmagpie_schema.configs import YouTubeSearchSourceSpec
from sources.payloads import SourcePayload

# YouTube video URL base.
YOUTUBE_VIDEO_URL = "https://www.youtube.com/watch?v="


class NewVideoPayload(SourcePayload):
    """A single YouTube video observed by a watched search stream.

    `author` is the channel name; `handle` is the channel ID and the
    within-kind source slug (grouping items by producing channel).
    `content` is the video description (the engine's judgeable body).
    The rest is source-specific: `metrics`, `refs` (related video IDs),
    `media` (thumbnails), `duration`.
    """

    PAYLOAD_KIND: ClassVar[str] = "new_video"

    author: str = ""
    handle: str = ""
    duration: int = 0  # seconds
    metrics: dict[str, int | None] = {}
    refs: dict[str, str | None] = {}
    media: list[dict[str, Any]] = []

    model_config = {"frozen": True, "extra": "ignore"}

    def source_slug(self) -> str | None:
        return self.handle or None

    @classmethod
    def sample(cls, variant: int = 0) -> NewVideoPayload:
        n = variant + 1
        video_id = str(999_000_000_000_000_000 + n)
        handle = f"example_channel_{n}"
        return cls(
            external_id=video_id,
            kind=cls.PAYLOAD_KIND,
            occurred_at=datetime(2026, 5, 27, 12, 0, tzinfo=UTC),
            source=YouTubeSearchSourceSpec.SOURCE_KIND,
            title="",
            content=f"Example YouTube video {n}: the description text that matched this watch.",
            url=f"{YOUTUBE_VIDEO_URL}{video_id}",
            author=f"Example Channel {n}",
            handle=handle,
            duration=60 * n,
            metrics={"views": 1000 + n, "likes": 100 + n, "comments": 10 + n},
            refs={},
            media=[],
        )

    @classmethod
    def from_video(cls, video: dict[str, Any]) -> NewVideoPayload:
        """Map a yt-dlp video info dict to a payload.

        yt-dlp returns videos as plain dicts when extract_info is called
        on a search URI. All attributes are accessed via dict get() with
        defaults, so the connector's unit tests can hand in lightweight
        fakes without importing yt-dlp.
        """
        video_id = str(video.get("id") or "")
        uploader = str(video.get("uploader") or video.get("channel") or "")
        uploader_id = str(video.get("uploader_id") or video.get("channel_id") or "")
        description = str(video.get("description") or "")
        upload_date = str(video.get("upload_date") or "")

        # Timestamp resolution, best first: `timestamp`/`release_timestamp`
        # (epoch seconds, full resolution) -> `upload_date` (YYYYMMDD, floors
        # to midnight UTC) -> today's midnight UTC. The last fallback must
        # stay a midnight floor, not now(): a wall-clock value advances the
        # source watermark past every same-day midnight-floored video and
        # strands them (see the watermark filter in connector.poll).
        occurred_at: datetime | None = None
        epoch = video.get("timestamp") or video.get("release_timestamp")
        if epoch is not None:
            with suppress(ValueError, TypeError, OSError, OverflowError):
                occurred_at = datetime.fromtimestamp(float(epoch), tz=UTC)
        if occurred_at is None and upload_date and len(upload_date) == 8:
            with suppress(ValueError):
                occurred_at = datetime.strptime(upload_date, "%Y%m%d").replace(tzinfo=UTC)
        if occurred_at is None:
            occurred_at = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)

        # Duration in seconds.
        duration = int(video.get("duration") or 0)

        # Metrics.
        metrics = {
            "views": int_or_none(video.get("view_count")),
            "likes": int_or_none(video.get("like_count")),
            "comments": int_or_none(video.get("comment_count")),
        }

        # Media: thumbnails.
        media = []
        for thumb in video.get("thumbnails") or []:
            url = thumb.get("url")
            if url:
                media.append(
                    {
                        "type": "thumbnail",
                        "url": url,
                        "width": int_or_none(thumb.get("width")),
                        "height": int_or_none(thumb.get("height")),
                    }
                )
        # Fallback to thumbnail field if thumbnails list is empty.
        if not media:
            thumb_url = video.get("thumbnail")
            if thumb_url:
                media.append({"type": "thumbnail", "url": thumb_url})

        return cls(
            external_id=video_id,
            kind=cls.PAYLOAD_KIND,
            occurred_at=occurred_at,
            source=YouTubeSearchSourceSpec.SOURCE_KIND,
            title=str(video.get("title") or ""),
            content=description,
            url=str(video.get("webpage_url") or f"{YOUTUBE_VIDEO_URL}{video_id}"),
            author=uploader,
            handle=uploader_id,
            duration=duration,
            metrics=metrics,
            refs={},
            media=media,
        )


def int_or_none(obj: Any) -> int | None:
    """Safely convert to int or return None."""
    if obj is None:
        return None
    try:
        return int(obj)
    except (ValueError, TypeError):
        return None
