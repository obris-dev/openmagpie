"""DRF serializers for the auth API.

Input serializers validate request payloads (DRF turns ValidationError into
400). The user identity that responses carry is built through the shared
`AuthUser` contract (see `auth_user_wire`), NOT a hand-mirrored serializer, so
the web frontend and the CLI parse one shape that can't drift from the server.
The remaining output serializers here shape the other response fields.
"""

from __future__ import annotations

from django.contrib.auth.password_validation import validate_password
from rest_framework import serializers

from accounts.services import AccountService
from openmagpie_schema.auth import AuthUser

from .constants import BEARER_TOKEN_TYPE
from .services.cli_tokens import MAX_EXPIRY_DAYS, MIN_EXPIRY_DAYS

# ── Input ──────────────────────────────────────────────────────────────


class _EmailField(serializers.EmailField):
    """Lowercased, stripped email. Matches the model's normalize step."""

    def to_internal_value(self, data):
        return super().to_internal_value(data).strip().lower()


class SignupSerializer(serializers.Serializer):
    email = _EmailField()
    password = serializers.CharField(write_only=True, trim_whitespace=False)

    def validate_password(self, value: str) -> str:
        # Runs the project's AUTH_PASSWORD_VALIDATORS chain (min length, etc.).
        validate_password(value)
        return value


class LoginSerializer(serializers.Serializer):
    email = _EmailField()
    password = serializers.CharField(write_only=True, trim_whitespace=False)


class RefreshSerializer(serializers.Serializer):
    refresh_token = serializers.CharField()


class CliTokenCreateSerializer(serializers.Serializer):
    """Body for `POST /v1/auth/cli-tokens`."""

    name = serializers.CharField(max_length=255, trim_whitespace=True)
    # Optional bound; omitted / null means "never expires" (the default).
    # Bounds shared with `CliTokenService.Global.mint` so the two can't drift.
    expires_in_days = serializers.IntegerField(
        required=False, allow_null=True, min_value=MIN_EXPIRY_DAYS, max_value=MAX_EXPIRY_DAYS
    )


# ── Output ─────────────────────────────────────────────────────────────


class CliTokenSerializer(serializers.Serializer):
    """Metadata wire shape for a `CliToken`. NEVER carries the raw token
    or its hash, list/read responses are safe to log. The create response
    is this shape plus a one-time `token` field the view tacks on (the
    model doesn't store the raw token)."""

    id = serializers.CharField()
    name = serializers.CharField()
    last_four = serializers.CharField()
    created_at = serializers.DateTimeField()
    last_used_at = serializers.DateTimeField(allow_null=True)
    expires_at = serializers.DateTimeField(allow_null=True)


def auth_user_wire(user) -> AuthUser:
    """Build the shared `AuthUser` contract for a `User` (the `/v1/auth` me /
    signup / login `{user}` payload, and the device-session completed bag), so
    the server + CLI + web share ONE identity shape instead of a hand-mirrored
    DRF serializer. Response sites emit `auth_user_wire(user).model_dump(mode="json")`.

    `account_id` is REQUIRED by the contract: a user belongs to an account (signup
    creates one and binds the user), so a missing account is a data-integrity
    violation, not a valid response, surface it rather than emit a contract-breaking
    null (mirrors the account-scoped services' None->raise guard).
    """
    account_id = AccountService.Global.primary_account_id_for(user_id=str(user.id))
    if account_id is None:
        raise ValueError(f"user {user.id} has no account (invariant: users belong to an account)")
    return AuthUser(id=str(user.id), email=user.email, account_id=account_id, created_at=user.date_joined)


class TokenPairSerializer(serializers.Serializer):
    """Wire shape for `/v1/auth/tokens/refresh` responses and the
    device-session completed bag. Fed a dict assembled in the view
    (OAuth Toolkit's models don't natively carry `expires_in` etc.).

    Use `TokenPairSerializer.build(user, access, refresh, ttl).data`
    when you have the raw token rows handy.
    """

    access_token = serializers.CharField()
    refresh_token = serializers.CharField()
    expires_in = serializers.IntegerField()
    token_type = serializers.CharField()
    # Already the shared `AuthUser` contract dict (built by `auth_user_wire`), so
    # this passes it through verbatim rather than re-mirroring the identity shape.
    user = serializers.JSONField()

    @classmethod
    def build(cls, user, access, refresh, ttl: int) -> TokenPairSerializer:
        return cls(
            instance={
                "access_token": access.token,
                "refresh_token": refresh.token,
                "expires_in": ttl,
                "token_type": BEARER_TOKEN_TYPE,
                "user": auth_user_wire(user).model_dump(mode="json"),
            }
        )
