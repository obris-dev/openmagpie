"""Wire-level constants for the auth API.

Status values match the strings the CLI's Pydantic models expect; keep
this in lockstep with `cli/src/openmagpie/constants.py` (same wire
contract, two sides).
"""

from __future__ import annotations

from enum import StrEnum


class DeviceSessionStatus(StrEnum):
    """Status values written into the device-session cache bag."""

    PENDING = "pending"
    COMPLETED = "completed"
    EXPIRED = "expired"


# HTTP transport constants.
AUTHORIZATION_HEADER = "Authorization"
# Django re-cases incoming headers as HTTP_<UPPER_SNAKE>; cached here so
# views don't sprinkle the META key everywhere.
AUTHORIZATION_META_KEY = "HTTP_AUTHORIZATION"
BEARER_SCHEME = "Bearer"

# OAuth2 token_type value emitted in token responses (RFC 6750).
BEARER_TOKEN_TYPE = "Bearer"


# Stable error codes the API returns in `{"error": <code>, "detail": ...}`.
class AuthErrorCode(StrEnum):
    MISSING_FIELDS = "missing_fields"
    INVALID_EMAIL = "invalid_email"
    EMAIL_TAKEN = "email_taken"
    INVALID_CREDENTIALS = "invalid_credentials"
    NOT_AUTHENTICATED = "not_authenticated"
    NOT_FOUND = "not_found"
    PAT_CANNOT_MINT = "pat_cannot_mint"
    MISSING_REFRESH_TOKEN = "missing_refresh_token"
    INVALID_REFRESH_TOKEN = "invalid_refresh_token"
    REVOKED_REFRESH_TOKEN = "revoked_refresh_token"
    # Note: device-session "expired" is represented by DeviceSessionStatus.EXPIRED
    # , same wire string, different enum because it doubles as a status value.
    SESSION_ALREADY_COMPLETED = "already_completed"
