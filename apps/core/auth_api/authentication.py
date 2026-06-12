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
from rest_framework.request import Request

from .auth_backends import _user_from_access_token
from .constants import AUTHORIZATION_META_KEY, BEARER_SCHEME
from .cookies import AUTH_COOKIE_NAME
from .services.cli_tokens import TOKEN_PREFIX as PAT_PREFIX
from .services.cli_tokens import CliTokenService

_BEARER_PREFIX = f"{BEARER_SCHEME} "

# HTTP methods that don't mutate state, exempt from CSRF check even
# on cookie-auth requests. Matches Django's CsrfViewMiddleware semantics.
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})

# `request.auth` value set when a request authenticated via a personal
# access token (vs None for session-token / cookie auth). Views gate
# PAT-forbidden actions on it, e.g. minting more tokens (`request_is_cli_token`).
CLI_TOKEN_AUTH = "cli_token"


def _extract_bearer(request: Request) -> str | None:
    """The raw token from an `Authorization: Bearer ...` header, or None.

    Shared by both auth classes so their header parsing can't drift.
    Returns None when there's no bearer header or it's empty.
    """
    auth_header = request._request.META.get(AUTHORIZATION_META_KEY, "")
    if not auth_header.startswith(_BEARER_PREFIX):
        return None
    return auth_header.removeprefix(_BEARER_PREFIX).strip() or None


def request_is_cli_token(request: Request) -> bool:
    """True iff this request authenticated via a personal access token."""
    return getattr(request, "auth", None) == CLI_TOKEN_AUTH


def _allowed_origins() -> set[str]:
    """Origins permitted to submit cookie-auth mutating requests.

    Pulled directly from settings each call so test overrides take effect.
    """
    origins = {settings.APP_BASE_URL.rstrip("/")}
    for o in getattr(settings, "CORS_ALLOWED_ORIGINS", []):
        origins.add(o.rstrip("/"))
    return origins


class PersonalAccessTokenAuthentication(BaseAuthentication):
    """Resolve `request.user` from a `Bearer mgp_...` personal access token.

    Registered AHEAD of `BearerOrCookieAuthentication` in
    `DEFAULT_AUTHENTICATION_CLASSES`. The split is by token shape: this
    class owns the `mgp_` prefix and returns None for everything else
    (non-PAT bearers, cookies, no credential) so DRF falls through to the
    session-token / cookie class. That ordering is what keeps the two
    credential types from colliding.

    No CSRF surface: like the Bearer path in the sibling class, a PAT is
    never auto-attached by a browser to an arbitrary page.
    """

    def authenticate(self, request):
        token_value = _extract_bearer(request)
        if token_value is None or not token_value.startswith(PAT_PREFIX):
            # No bearer, or a bearer that isn't ours. Let
            # BearerOrCookieAuthentication try it as an OAuth access token.
            return None
        user = CliTokenService.Global.resolve(token_value)
        if user is None:
            # It IS a PAT-shaped token but unknown / revoked / expired.
            # Fail loudly with 401 rather than falling through, so the CLI
            # knows to mint a new one instead of seeing "missing credential".
            raise exceptions.AuthenticationFailed("invalid or expired personal access token")
        # `request.auth = CLI_TOKEN_AUTH` (a marker, NOT the raw secret) so
        # views can forbid PAT-only-disallowed actions like minting tokens.
        return (user, CLI_TOKEN_AUTH)

    def authenticate_header(self, request):
        return f'{BEARER_SCHEME} realm="api"'


class BearerOrCookieAuthentication(BaseAuthentication):
    def authenticate(self, request):
        django_request = request._request

        # Bearer path: programmatic clients (the CLI, scripts). No CSRF
        # surface, the token isn't auto-attached by browsers to
        # arbitrary pages.
        token_value = _extract_bearer(request)
        if token_value is not None:
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
