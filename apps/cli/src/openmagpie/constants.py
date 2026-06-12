"""Wire-level constants shared with the server API contract.

The status values match the strings the server emits in device-session
poll responses. Keep this file in lockstep with
`core/auth_api/constants.py`; they're the same wire contract from two
sides.
"""

from __future__ import annotations

from enum import StrEnum


class DeviceSessionStatus(StrEnum):
    """Status values returned by GET /v1/auth/device-sessions/{id}."""

    PENDING = "pending"
    COMPLETED = "completed"
    EXPIRED = "expired"


# HTTP transport constants.
AUTHORIZATION_HEADER = "Authorization"
BEARER_SCHEME = "Bearer"

# Prefix on every personal access token (`mgp_...`). Lets the CLI tell a
# PAT credential apart from a session token locally (e.g. to skip the
# server revoke on logout). Mirrors `core/auth_api/services/cli_tokens.py`.
PERSONAL_ACCESS_TOKEN_PREFIX = "mgp_"


def is_personal_access_token(token: str | None) -> bool:
    """True iff `token` is shaped like a personal access token (`mgp_...`)."""
    return bool(token and token.startswith(PERSONAL_ACCESS_TOKEN_PREFIX))


# Ambient credential env var. When set, it's used as the bearer on EVERY
# request, takes precedence over the stored login, and is never persisted
# or refreshed, the standard "token in an environment" pattern (gh's
# GH_TOKEN, etc.). `auth login` refuses while it is set; it is never a
# `--token` source (that reads stdin or a hidden prompt).
TOKEN_ENV_VAR = "MAGPIE_TOKEN"
