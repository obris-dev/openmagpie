"""CLI device-flow handshake.

Flow:
  1. CLI: POST /v1/auth/device-sessions
     → {session_id, authorize_url, user_code, device_secret, expires_in}
     CLI prints URL + user_code; holds device_secret in memory only.
  2. CLI: opens browser at authorize_url, then polls
     GET /device-sessions/{id} (header: X-Device-Secret) every 2s.
  3. Browser (logged-in user): GETs /info to see audit metadata, then
     POSTs /complete with the user_code typed from the terminal.
  4. Server validates user_code, mints tokens, replaces the cache bag
     with a completed state (preserving device_secret_hash).
  5. Next CLI poll returns {status:"completed", access_token, ...}; CLI
     saves tokens and exits. Bag is deleted on that first read.

Three secrets in play, with deliberately separate roles (RFC 8628):
  - `session_id`, public, in URL. Identifies the session. Leaking it
    alone gives an attacker nothing.
  - `device_secret`, CLI-only bearer for polling. Returned ONCE at
    create time, stored as SHA-256 on the server side. Required header
    on every poll → closes the read-the-completed-bag-via-leaked-id
    attack.
  - `user_code`, short human-typed verification code. Gates /complete
    → closes the "lure-victim-to-authorize-attacker's-CLI" attack.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

from django.conf import settings
from django.http import HttpRequest
from django.utils import timezone
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from common.web_urls import AUTH_DEVICE, app_url

from .constants import AuthErrorCode, DeviceSessionStatus
from .device_session_store import DeviceSessionClient, DeviceSessionState, Store
from .serializers import TokenPairSerializer
from .services.tokens import mint_token_pair_for_user

_DEVICE_SECRET_HEADER = "X-Device-Secret"
_DEVICE_SECRET_META_KEY = "HTTP_X_DEVICE_SECRET"

# Confusable-free alphabet so a user reading the code off their terminal
# doesn't second-guess I/1/l/O/0. Vowels stripped too so we don't
# accidentally spell anything reading the random codes back.
_USER_CODE_ALPHABET = "BCDFGHJKMNPQRSTVWXZ"
_USER_CODE_LENGTH = 8


# ── Helpers ────────────────────────────────────────────────────────────


def _authorize_url(session_id: str) -> str:
    return app_url(AUTH_DEVICE, session_id=session_id)


def _client_ip(request: HttpRequest) -> str | None:
    """Best-effort initiator IP. X-Forwarded-For first hop if present
    (caller deployments behind a proxy need the proxy to strip
    client-supplied values); otherwise REMOTE_ADDR. Stored for audit;
    never used for auth decisions.
    """
    fwd = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if fwd:
        first = fwd.split(",", 1)[0].strip()
        if first:
            return first
    return request.META.get("REMOTE_ADDR") or None


def _generate_user_code() -> str:
    """Random 8-char code in the form 'ABCD-EFGH'."""
    raw = "".join(secrets.choice(_USER_CODE_ALPHABET) for _ in range(_USER_CODE_LENGTH))
    return f"{raw[:4]}-{raw[4:]}"


def _normalize_user_code(value: str) -> str:
    """Lowercase + strip non-alphanumerics so 'abcd-efgh', 'ABCDEFGH',
    and 'ABCD EFGH' all compare equal."""
    return "".join(c for c in (value or "") if c.isalnum()).upper()


def _hash_device_secret(secret: str) -> str:
    """SHA-256 of the device-secret, hex-encoded. We store the hash, not
    the raw secret, so a cache dump doesn't hand the bearer credential
    to whoever read it.
    """
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def _device_secret_matches(presented: str, stored_hash: str) -> bool:
    """Constant-time compare to dodge timing oracles. Both sides are
    same-length hex digests so comparison is safe.
    """
    if not presented or not stored_hash:
        return False
    return hmac.compare_digest(_hash_device_secret(presented), stored_hash)


# ── Views ──────────────────────────────────────────────────────────────


class DeviceSessionsCreateView(APIView):
    """POST /v1/auth/device-sessions, CLI starts the handshake."""

    permission_classes: list = []

    def post(self, request):
        session_id = secrets.token_hex(16)
        user_code = _generate_user_code()
        device_secret = secrets.token_urlsafe(32)
        state = DeviceSessionState(
            status=DeviceSessionStatus.PENDING,
            created_at=timezone.now().isoformat(),
            user_code=user_code,
            device_secret_hash=_hash_device_secret(device_secret),
            initiator_ip=_client_ip(request._request),
            initiator=DeviceSessionClient.model_validate(request.data.get("client") or {}),
        )
        Store.put(session_id, state)
        return Response(
            {
                "session_id": session_id,
                "authorize_url": _authorize_url(session_id),
                "user_code": user_code,
                "device_secret": device_secret,
                "expires_in": settings.DEVICE_SESSION_PENDING_TTL_SECONDS,
            },
            status=status.HTTP_201_CREATED,
        )


class DeviceSessionPollView(APIView):
    """GET /v1/auth/device-sessions/{id}, CLI polls until completed.

    Requires the `X-Device-Secret` header, session_id alone (which
    lives in the URL) isn't sufficient to read the bag. Bag is deleted
    on the first successful read of a completed state.
    """

    permission_classes: list = []

    def get(self, request, session_id: str):
        presented_secret = request.META.get(_DEVICE_SECRET_META_KEY, "")
        if not presented_secret:
            return Response(
                {
                    "error": "missing_device_secret",
                    "detail": f"{_DEVICE_SECRET_HEADER} header is required",
                },
                status=status.HTTP_401_UNAUTHORIZED,
            )

        state = Store.get(session_id)
        if state is None:
            return Response(
                {"status": DeviceSessionStatus.EXPIRED},
                status=status.HTTP_404_NOT_FOUND,
            )
        if not _device_secret_matches(presented_secret, state.device_secret_hash):
            return Response(
                {
                    "error": "invalid_device_secret",
                    "detail": "device secret doesn't match",
                },
                status=status.HTTP_401_UNAUTHORIZED,
            )

        if state.is_completed:
            Store.delete(session_id)
        return Response(state.cli_poll_view())


class DeviceSessionInfoView(APIView):
    """GET /v1/auth/device-sessions/{id}/info, browser sees audit metadata.

    Cookie-auth required. Surfaces initiator info but NEVER the
    `user_code` (the user must source that from their terminal) or the
    `device_secret_hash`.
    """

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, session_id: str):
        state = Store.get(session_id)
        if state is None:
            return Response(
                {
                    "error": DeviceSessionStatus.EXPIRED,
                    "detail": "session expired",
                },
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(state.info_view())


class DeviceSessionDenyView(APIView):
    """POST /v1/auth/device-sessions/{id}/deny, browser declines.

    Drops the cache bag so the CLI's next poll returns 404 and exits
    cleanly via its existing 404 handler.
    """

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, session_id: str):
        Store.delete(session_id)
        return Response({"status": "denied"})


class DeviceSessionCompleteView(APIView):
    """POST /v1/auth/device-sessions/{id}/complete, browser authorizes.

    Gates on the user-typed `user_code` so a victim phished to an
    attacker's session URL can't authorize it without seeing the code
    that only the attacker's terminal knows.
    """

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, session_id: str):
        submitted_code = _normalize_user_code(request.data.get("user_code", ""))
        if not submitted_code:
            return Response(
                {
                    "error": "missing_user_code",
                    "detail": "user_code is required",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        state = Store.get(session_id)
        if state is None:
            return Response(
                {
                    "error": DeviceSessionStatus.EXPIRED,
                    "detail": "session expired",
                },
                status=status.HTTP_404_NOT_FOUND,
            )
        if state.is_completed:
            return Response(
                {"error": AuthErrorCode.SESSION_ALREADY_COMPLETED},
                status=status.HTTP_409_CONFLICT,
            )

        stored_code = _normalize_user_code(state.user_code)
        if not stored_code or submitted_code != stored_code:
            return Response(
                {
                    "error": "code_mismatch",
                    "detail": "the code doesn't match the one shown in your terminal",
                },
                status=status.HTTP_403_FORBIDDEN,
            )

        access, refresh, ttl = mint_token_pair_for_user(request.user)
        token_pair = TokenPairSerializer.build(request.user, access, refresh, ttl).data
        Store.put(
            session_id,
            state.complete_with(
                access_token=token_pair["access_token"],
                refresh_token=token_pair["refresh_token"],
                expires_in=token_pair["expires_in"],
                token_type=token_pair["token_type"],
                user=token_pair["user"],
            ),
        )
        return Response({"status": DeviceSessionStatus.COMPLETED})
