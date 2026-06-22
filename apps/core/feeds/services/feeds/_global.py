"""Cross-tenant Feed operations.

Do NOT import directly; use `FeedService.Global.<op>(...)`. For
scheduler / debug only.
"""

from collections.abc import Iterator
from datetime import datetime

from django.db.models import Q

from feeds.models import Feed


class FeedGlobal:
    """Static methods only. Span all accounts."""

    @staticmethod
    def get(id: str) -> Feed:
        """Look up a Feed regardless of account. Raises DoesNotExist if missing.
        System-level only (scheduler, management commands)."""
        return Feed.objects.get(id=id)

    @staticmethod
    def iter_due_for_poll(*, now: datetime, chunk_size: int = 100) -> Iterator[Feed]:
        """Active Feeds whose next_poll_at has elapsed (or is unset).
        Spans all accounts, scheduler entry point."""
        return (
            Feed.objects.filter(is_active=True)
            .filter(Q(next_poll_at__isnull=True) | Q(next_poll_at__lte=now))
            .iterator(chunk_size=chunk_size)
        )

    @staticmethod
    def count() -> int:
        """Total feeds across all accounts (telemetry gauge)."""
        return Feed.objects.count()
