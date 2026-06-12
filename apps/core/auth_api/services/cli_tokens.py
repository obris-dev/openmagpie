"""Mint, resolve, list, and revoke `CliToken` personal access tokens.

`CliTokenService` is the single seam for the PAT machinery, paralleling
the OAuth session-token helpers. A PAT is user-scoped, not account-scoped,
so every operation is system-level and lives under `CliTokenService.Global`
(the AGENTS.md home for cross-tenant operations). All DB access to
`CliToken` lives here, the owning service is the only place that touches
`CliToken.objects`.

The raw token is only ever in memory here at creation; storage is the
SHA-256 hash. Token shape: ``mgp_<base64url>``, the ``mgp_`` prefix lets the
auth layer recognise a PAT bearer and route it to the hashed lookup
without probing the plaintext `AccessToken` table first.
"""

from __future__ import annotations

import contextlib
import hashlib
import secrets
from datetime import timedelta

from django.db import DatabaseError
from django.utils import timezone

from accounts.models.user import User
from accounts.services import UserService

from ..models import CliToken

# Recognisable, greppable prefix on every raw token. Mirrors the
# ``ghp_``/``glpat-`` convention so a leaked token is identifiable (and
# secret-scanners can match it).
TOKEN_PREFIX = "mgp_"

# 32 random bytes -> ~43 url-safe chars. 256 bits of entropy, so the
# stored SHA-256 has nothing to brute-force (no salt/bcrypt needed, same
# reasoning GitHub uses for PAT hashing).
_TOKEN_NBYTES = 32

# Expiry bounds enforced in `mint` (so the operator-only management command
# is guarded too, not just the serializer-validated HTTP path). MAX is a
# typo guard (~10 years), not a policy.
MIN_EXPIRY_DAYS = 1
MAX_EXPIRY_DAYS = 3650

# Don't write `last_used_at` on every request, a bump this fresh tells us
# nothing new and would turn every authed read into a write. Only persist
# when the stored value is this stale (or unset).
_LAST_USED_THROTTLE = timedelta(seconds=60)


def _hash(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


class CliTokenService:
    """Personal-access-token operations. All system-level (PATs are
    user-scoped, not account-scoped), so they live under `Global`."""

    class Global:
        @staticmethod
        def mint(user: User, *, name: str, expires_in_days: int | None = None) -> tuple[CliToken, str]:
            """Create a PAT for `user`. Returns the row plus the raw token.

            The raw token is the ONLY time the secret exists outside the
            hash; callers surface it once and never store it.
            `expires_in_days=None` means no expiry (the default). Raises
            `ValueError` on a blank name or an out-of-bounds expiry, so the
            management command can't mint a blank-named or instantly-expired
            token (the HTTP path is also serializer-guarded).
            """
            name = name.strip()
            if not name:
                raise ValueError("token name must not be blank")
            if expires_in_days is not None and not (MIN_EXPIRY_DAYS <= expires_in_days <= MAX_EXPIRY_DAYS):
                raise ValueError(f"expires_in_days must be between {MIN_EXPIRY_DAYS} and {MAX_EXPIRY_DAYS}")
            raw_token = TOKEN_PREFIX + secrets.token_urlsafe(_TOKEN_NBYTES)
            expires_at = None
            if expires_in_days is not None:
                expires_at = timezone.now() + timedelta(days=expires_in_days)
            token = CliToken.objects.create(
                user_id=str(user.id),
                name=name,
                token_hash=_hash(raw_token),
                last_four=raw_token[-4:],
                expires_at=expires_at,
            )
            return token, raw_token

        @staticmethod
        def resolve(raw_token: str) -> User | None:
            """Return the user behind a raw PAT, or None.

            None for: not a `mgp_` token, unknown hash, revoked, expired,
            or a dangling user_id (user since deleted). On a hit, bumps
            `last_used_at` (throttled) so `auth token list` can show
            activity without a write per request.
            """
            if not raw_token.startswith(TOKEN_PREFIX):
                return None
            try:
                token = CliToken.objects.get(token_hash=_hash(raw_token))
            except CliToken.DoesNotExist:
                return None
            if not token.is_valid():
                return None
            try:
                user = UserService.Global.get(token.user_id)
            except User.DoesNotExist:
                return None

            now = timezone.now()
            if token.last_used_at is None or (now - token.last_used_at) >= _LAST_USED_THROTTLE:
                # Bypass save()/auto_now via a queryset update: one UPDATE,
                # no `updated_at` churn, no signals on the hot auth path.
                # Best-effort: this runs inside authenticate() on every PAT
                # request, so a transient DB error on the activity stat must
                # NOT fail an otherwise-valid auth (just skip the bump).
                with contextlib.suppress(DatabaseError):
                    CliToken.objects.filter(pk=token.pk).update(last_used_at=now)
            return user

        @staticmethod
        def list_for_user(user: User) -> list[CliToken]:
            """Active (non-revoked) tokens for `user`, newest first.

            Ordered by the ULID PK (`-id`), not `created_at`, per AGENTS.md.
            """
            return list(CliToken.objects.filter(user_id=str(user.id), revoked_at__isnull=True).order_by("-id"))

        @staticmethod
        def revoke(user: User, token_id: str) -> bool:
            """Revoke one of `user`'s tokens. Returns True if one was revoked.

            Scoped to the owner so one user can't revoke another's token;
            a no-op (returns False) if the id is unknown, not theirs, or
            already revoked.
            """
            updated = CliToken.objects.filter(id=token_id, user_id=str(user.id), revoked_at__isnull=True).update(
                revoked_at=timezone.now()
            )
            return updated > 0
