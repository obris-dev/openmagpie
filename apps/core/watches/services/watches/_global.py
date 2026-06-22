"""Cross-tenant Watch operations.

Do NOT import directly; use `WatchService.Global.<op>(...)`. For the
scheduler (the trigger + drain cron) and debug only.
"""

from collections.abc import Iterator

from watches.models import Watch


class WatchGlobal:
    """Static methods only. Span all accounts."""

    @staticmethod
    def get(id: str) -> Watch:
        """Look up a Watch regardless of account. Raises DoesNotExist if
        missing. System-level only (scheduler, management commands)."""
        return Watch.objects.get(id=id)

    @staticmethod
    def iter_active(*, chunk_size: int = 100) -> Iterator[Watch]:
        """Stream active Watches across all accounts ; the trigger pass
        entry point ("due" = is_active, no per-watch schedule). Iterates
        (chunked) rather than materializing ; there may be many watches."""
        return Watch.objects.filter(is_active=True).iterator(chunk_size=chunk_size)

    @staticmethod
    def count() -> int:
        """Total watches across all accounts (telemetry gauge)."""
        return Watch.objects.count()
