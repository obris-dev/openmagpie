"""DRF authentication class for our two-surface auth.

Resolves `request.user` from either an `Authorization: Bearer ...`
header or the `auth_token` cookie. Registered globally via
`REST_FRAMEWORK.DEFAULT_AUTHENTICATION_CLASSES`.

CSRF: enforced inside this class for the cookie path only, same
pattern DRF's own `SessionAuthentication` uses (`enforce_csrf` is
called from `authenticate`). Bearer requests are exempt: an attacker
page can't read our HttpOnly cookie to mint a Bearer header, so the
cross-site-request-forgery threat model doesn't apply. Bundling CSRF
here (rather than as a per-view permission) means every cookie-auth
endpoint is protected by default without each view having to opt in.
"""

from __future__ import annotations

from django.conf import settings
from rest_framework import exceptions
from rest_framework.authentication import BaseAuthentication

from .auth_backends import _user_from_access_token
from .constants import AUTHORIZATION_META_KEY, BEARER_SCHEME
from .cookies import AUTH_COOKIE_NAME

_BEARER_PREFIX = f"{BEARER_SCHEME} "

# HTTP methods that don't mutate state, exempt from CSRF check even
# on cookie-auth requests. Matches Django's CsrfViewMiddleware semantics.
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})


def _allowed_origins() -> set[str]:
    """Origins permitted to submit cookie-auth mutating requests.

    Pulled directly from settings each call so test overrides take effect.
    """
    origins = {settings.APP_BASE_URL.rstrip("/")}
    for o in getattr(settings, "CORS_ALLOWED_ORIGINS", []):
        origins.add(o.rstrip("/"))
    return origins


class BearerOrCookieAuthentication(BaseAuthentication):
    def authenticate(self, request):
        django_request = request._request

        # Bearer path: programmatic clients (the CLI, scripts). No CSRF
        # surface, the token isn't auto-attached by browsers to
        # arbitrary pages.
        auth_header = django_request.META.get(AUTHORIZATION_META_KEY, "")
        if auth_header.startswith(_BEARER_PREFIX):
            token_value = auth_header.removeprefix(_BEARER_PREFIX).strip()
            if not token_value:
                return None
            user = _user_from_access_token(token_value)
            if user is None:
                # Credential presented but invalid, fail loudly with
                # 401 so the client knows to refresh / re-login. Falling
                # through to anonymous would mask the bad token as
                # "missing credential" downstream.
                raise exceptions.AuthenticationFailed("invalid bearer token")
            return (user, None)

        # Cookie path: browser. Enforce same-origin on mutating methods
        # before returning the user, so a forged cross-site POST that
        # somehow carries our cookie (legacy browsers, Chrome's old
        # Lax-allowing-unsafe window) still gets rejected.
        cookie_value = django_request.COOKIES.get(AUTH_COOKIE_NAME)
        if not cookie_value:
            return None
        user = _user_from_access_token(cookie_value)
        if user is None:
            # Stale / revoked cookie. Raise AuthenticationFailed so the
            # custom exception handler in `auth_api.exception_handlers`
            # can attach a clearing Set-Cookie to the 401 response and
            # the browser stops sending the dead credential.
            raise exceptions.AuthenticationFailed("invalid auth cookie")

        if django_request.method not in _SAFE_METHODS:
            origin = (django_request.META.get("HTTP_ORIGIN") or "").rstrip("/")
            if not origin or origin not in _allowed_origins():
                raise exceptions.PermissionDenied("request Origin not in the allowed list")

        return (user, None)

    def authenticate_header(self, request):
        # Returned in the WWW-Authenticate header on 401s. RFC 7235 wants
        # a realm parameter; some clients strictly parse the challenge.
        return f'{BEARER_SCHEME} realm="api"'
