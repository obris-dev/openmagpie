"""HTTP entry points for signup / login / logout / me / tokens.

Surface split:
  - signup / login / logout / me, BROWSER. Cookie-based auth lifecycle.
    `logout` also revokes the cookie's underlying token.
  - tokens/refresh / tokens/revoke, CLI. Stateless Bearer rotation +
    revocation; the bearer equivalent of "log out". No cookies touched.
  - device-sessions/*, the CLI ↔ browser handshake (see device_sessions.py).

Auth: every APIView uses the project-wide BearerOrCookieAuthentication
class registered in REST_FRAMEWORK settings. Permission gating is
per-view; endpoints that require an authenticated user list
`IsAuthenticated`, public ones leave `permission_classes` empty.
"""

from __future__ import annotations

from typing import cast

from django.contrib.auth import authenticate
from django.db import transaction
from oauth2_provider.models import RefreshToken
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.models.user import User
from accounts.services import UserService
from common.locks import refresh_token_lock

from .auth_backends import extract_request_token
from .authentication import request_is_cli_token
from .constants import AuthErrorCode
from .cookies import delete_auth_cookie, set_auth_cookie
from .operations import EmailAlreadyExists, SignupOperation
from .serializers import (
    CliTokenCreateSerializer,
    CliTokenSerializer,
    LoginSerializer,
    RefreshSerializer,
    SignupSerializer,
    TokenPairSerializer,
    auth_user_wire,
)
from .services.cli_tokens import CliTokenService
from .services.tokens import TokenService


def _err(code: AuthErrorCode, detail: str, status_code: int) -> Response:
    return Response({"error": str(code), "detail": detail}, status=status_code)


def _browser_auth_response(user: User, *, status_code: int = status.HTTP_200_OK) -> Response:
    """Mint a fresh token pair, return `{user}` + the auth_token cookie.

    Refresh token is intentionally NOT echoed back to the browser; the
    cookie carries the access token and the browser doesn't need raw
    rotation material.
    """
    access, _refresh, ttl = TokenService.Global.mint_pair(user)
    response = Response({"user": auth_user_wire(user).model_dump(mode="json")}, status=status_code)
    set_auth_cookie(response, access.token, max_age=ttl)
    return response


class SignupView(APIView):
    """POST /v1/auth/signup, create user + sign in via cookie."""

    permission_classes: list = []

    def post(self, request):
        serializer = SignupSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        email = serializer.validated_data["email"]
        password = serializer.validated_data["password"]

        # Optimistic pre-check for the common "email already used" case;
        # the IntegrityError translation below closes the race window
        # where two concurrent signups both pass this check before either
        # commits.
        if UserService.Global.email_exists(email):
            return _err(
                AuthErrorCode.EMAIL_TAKEN,
                "an account with this email already exists",
                status.HTTP_409_CONFLICT,
            )

        # Wrap the User+Account+UserProfile creation AND the token mint
        # in one transaction so a token-mint failure rolls back the new
        # user too, otherwise we'd be left with a registered account
        # that can't log in until manual cleanup.
        try:
            with transaction.atomic():
                user = SignupOperation(email=email, password=password).run()
                return _browser_auth_response(user, status_code=status.HTTP_201_CREATED)
        except EmailAlreadyExists:
            return _err(
                AuthErrorCode.EMAIL_TAKEN,
                "an account with this email already exists",
                status.HTTP_409_CONFLICT,
            )


class LoginView(APIView):
    """POST /v1/auth/login, verify credentials + sign in via cookie."""

    permission_classes: list = []

    def post(self, request):
        serializer = LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        email = serializer.validated_data["email"]
        password = serializer.validated_data["password"]

        base_user = authenticate(request, username=email, password=password)
        if base_user is None or not base_user.is_active:
            return _err(
                AuthErrorCode.INVALID_CREDENTIALS,
                "email or password is incorrect",
                status.HTTP_401_UNAUTHORIZED,
            )
        user = cast(User, base_user)
        return _browser_auth_response(user)


class LogoutView(APIView):
    """POST /v1/auth/logout, clear cookie + revoke its underlying token.

    Browser endpoint. CSRF defense: we force `BearerOrCookieAuthentication`
    to resolve by touching `request.user` before doing anything else.
    For the cookie path, that triggers the Origin allowlist check on
    this non-safe method, blocking cross-site form POSTs that would
    otherwise force-logout a logged-in user (the cookie ships
    automatically). For the Bearer path the auth class exempts CSRF,
    so the CLI's bearer-logout still works.

    When no credential is presented at all, `request.user` is the
    project's `UNAUTHENTICATED_USER` (None) and we no-op the revoke
    while still clearing whatever cookie might be lying around.
    """

    permission_classes: list = []

    def post(self, request):
        # Force auth resolution so the cookie-path Origin check runs.
        # AuthenticationFailed / PermissionDenied propagate as 401/403.
        _ = request.user
        token_value = extract_request_token(request._request)
        if token_value:
            TokenService.Global.revoke(token_value)
        response = Response({"detail": "logged out"})
        delete_auth_cookie(response)
        return response


class MeView(APIView):
    """GET /v1/auth/me, current user record. Cookie OR bearer auth."""

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        return Response(auth_user_wire(request.user).model_dump(mode="json"))


class TokensRefreshView(APIView):
    """POST /v1/auth/tokens/refresh, CLI bearer rotation.

    Stateless: accepts the refresh token in the body, returns a fresh
    `{access_token, refresh_token, expires_in, token_type, user}` pair.
    The old refresh token is revoked atomically (OAuth Toolkit's
    ROTATE_REFRESH_TOKEN semantics).
    """

    permission_classes: list = []

    def post(self, request):
        serializer = RefreshSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        refresh_token = serializer.validated_data["refresh_token"]

        # Serialize concurrent rotations of the same refresh token. Without
        # this lock, two parallel requests could both pass the rt.revoked
        # check and both mint new pairs (a replay attack window). The
        # cache-backed try-lock fails fast on contention because a second
        # in-flight rotation for the same token is either a bug or replay.
        with refresh_token_lock(refresh_token) as acquired:
            if not acquired:
                return _err(
                    AuthErrorCode.REVOKED_REFRESH_TOKEN,
                    "another refresh is in flight for this token",
                    status.HTTP_409_CONFLICT,
                )
            try:
                rt = RefreshToken.objects.select_related("user").get(token=refresh_token)
            except RefreshToken.DoesNotExist:
                return _err(
                    AuthErrorCode.INVALID_REFRESH_TOKEN,
                    "refresh token is unknown",
                    status.HTTP_401_UNAUTHORIZED,
                )
            if rt.revoked is not None:
                return _err(
                    AuthErrorCode.REVOKED_REFRESH_TOKEN,
                    "refresh token has been revoked",
                    status.HTTP_401_UNAUTHORIZED,
                )

            # Atomic: revoke + mint together so a failure midway can't
            # leave the user "revoked but not re-issued" (silently
            # logged out with no recovery path). Either both happen or
            # neither does.
            with transaction.atomic():
                rt.revoke()
                access, new_refresh, ttl = TokenService.Global.mint_pair(rt.user)
            return Response(TokenPairSerializer.build(rt.user, access, new_refresh, ttl).data)


class TokensRevokeView(APIView):
    """POST /v1/auth/tokens/revoke, bearer "logout".

    Revokes the access token carried in the Authorization header. Doesn't
    touch cookies (callers using cookies should use /v1/auth/logout).
    """

    permission_classes: list = []

    def post(self, request):
        token_value = extract_request_token(request._request)
        if token_value:
            TokenService.Global.revoke(token_value)
        return Response({"detail": "revoked"})


class CliTokensView(APIView):
    """`/v1/auth/cli-tokens`, personal access tokens for the current user.

    POST mints one (returning the raw token ONCE); GET lists the active
    ones (metadata only). Both require an authenticated user; the headless
    cold-start path uses the `issue_cli_token` management command instead.

    POST additionally rejects PAT-authenticated requests: a personal
    access token can't mint other tokens, or a leaked PAT could bootstrap
    fresh credentials that outlive its own revocation (GitHub blocks
    PAT->PAT creation for the same reason).
    """

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        if request_is_cli_token(request):
            return _err(
                AuthErrorCode.PAT_CANNOT_MINT,
                "personal access tokens can't mint other tokens; sign in with the browser "
                "login, or use the issue_cli_token command on the server",
                status.HTTP_403_FORBIDDEN,
            )
        serializer = CliTokenCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        token, raw_token = CliTokenService.Global.mint(
            request.user,
            name=serializer.validated_data["name"],
            expires_in_days=serializer.validated_data.get("expires_in_days"),
        )
        # Serialize the model once (keeps the field list in CliTokenSerializer)
        # and tack on the one-time raw token, which the model doesn't carry.
        body = CliTokenSerializer(token).data
        body["token"] = raw_token
        return Response(body, status=status.HTTP_201_CREATED)

    def get(self, request):
        tokens = CliTokenService.Global.list_for_user(request.user)
        return Response(CliTokenSerializer(tokens, many=True).data)


class CliTokenDetailView(APIView):
    """DELETE /v1/auth/cli-tokens/{id}, revoke one of the user's tokens."""

    permission_classes = [permissions.IsAuthenticated]

    def delete(self, request, token_id: str):
        if not CliTokenService.Global.revoke(request.user, token_id):
            return _err(
                AuthErrorCode.NOT_FOUND,
                "no such token for this user",
                status.HTTP_404_NOT_FOUND,
            )
        return Response(status=status.HTTP_204_NO_CONTENT)


class WhoamiView(APIView):
    """GET /v1/auth/whoami, diagnostic; see what the server sees."""

    permission_classes: list = []

    def get(self, request):
        from .constants import AUTHORIZATION_META_KEY  # local to avoid header churn

        return Response(
            {
                "cookies_seen": sorted(request._request.COOKIES.keys()),
                "has_auth_header": AUTHORIZATION_META_KEY in request._request.META,
                "user": auth_user_wire(request.user).model_dump(mode="json") if request.user is not None else None,
            }
        )
