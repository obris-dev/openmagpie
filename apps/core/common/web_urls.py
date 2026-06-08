"""Web route templates the server builds absolute URLs to: the product app
(APP_BASE_URL) and the marketing site (MARKETING_BASE_URL).

App paths mirror `web/packages/api-utils/src/routes.ts:webRoutes`, same wire
contract, two sides. Django can't reverse() these (the pages are Next.js, not in
Django's URLconf), so we keep one named constant per path and centralize URL
assembly in `app_url(...)` / `marketing_url(...)`.

When a page path changes, update both the TypeScript registry and this file.
"""

from __future__ import annotations

from typing import Final

from django.conf import settings

# App path templates. `{session_id}` style placeholders are filled by
# `app_url`'s str.format kwargs.
AUTH_DEVICE: Final[str] = "/auth/device/{session_id}"


def app_url(path: str, /, **params: object) -> str:
    """Build an absolute URL into the product app (joins `settings.APP_BASE_URL`,
    trailing slash trimmed so we never double up).

        >>> app_url(AUTH_DEVICE, session_id="abc123")
        "http://localhost:3001/auth/device/abc123"
    """
    base = settings.APP_BASE_URL.rstrip("/")
    return f"{base}{path.format(**params)}"


def marketing_url(path: str = "", /, **params: object) -> str:
    """Build an absolute URL into the marketing site (joins
    `settings.MARKETING_BASE_URL`). `path` defaults to the site root.

        >>> marketing_url()
        "https://openmagpie.ai"
    """
    base = settings.MARKETING_BASE_URL.rstrip("/")
    return f"{base}{path.format(**params)}"
