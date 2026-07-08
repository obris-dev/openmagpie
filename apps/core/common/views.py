"""Project-wide HTTP endpoints not tied to any single app.

Currently just `/healthz`, a readiness probe that pings the dependencies
the server actually needs to serve requests (DB + cache). Returns 200
when everything is healthy, 503 when any check fails, so an orchestrator
can route traffic accordingly.
"""

from __future__ import annotations

import secrets
from typing import Any

from django.conf import settings
from django.core.cache import cache
from django.db import connection
from django.http import HttpRequest, JsonResponse
from django.views.decorators.http import require_http_methods


def _check_database() -> str:
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        return "ok"
    except Exception as e:
        return f"error: {type(e).__name__}"


def _check_cache() -> str:
    # Round-trip a unique value so a stale key from a prior request can't
    # masquerade as a healthy cache. Key itself is per-request too, so
    # two concurrent probes don't overwrite each other's value and
    # false-503 with "round-trip mismatch".
    probe_key = f"_healthz_probe:{secrets.token_hex(8)}"
    probe_value = secrets.token_hex(8)
    try:
        cache.set(probe_key, probe_value, timeout=5)
        if cache.get(probe_key) != probe_value:
            return "error: round-trip mismatch"
        cache.delete(probe_key)
        return "ok"
    except Exception as e:
        return f"error: {type(e).__name__}"


@require_http_methods(["GET"])
def healthz(request: HttpRequest) -> JsonResponse:
    """Liveness + readiness probe. 200 if DB and cache are reachable, 503
    if anything's degraded. Body always carries per-check status so an
    operator can see which dependency failed without trawling logs.
    """
    checks: dict[str, Any] = {
        "database": _check_database(),
        "cache": _check_cache(),
    }
    ok = all(v == "ok" for v in checks.values())
    # `version` is the running PRODUCT version (release-please `.` track), so a
    # client (`magpie version`) can report what the server is on. Unauthenticated +
    # always present, even when degraded.
    return JsonResponse(
        {"ok": ok, "version": settings.PRODUCT_VERSION, "checks": checks},
        status=200 if ok else 503,
    )
