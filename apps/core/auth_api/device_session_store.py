"""Cache-bag shape + storage for the CLI device-flow handshake.

Split from `device_sessions.py` to keep state/I/O concerns out of the
view layer. Views own the HTTP surface and the auth checks; this module
owns "what's in the bag" and "how it lives in the cache."

The full handshake protocol is documented in `device_sessions.py`.
"""

from __future__ import annotations

from typing import Any

from django.conf import settings
from django.core.cache import cache
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .constants import DeviceSessionStatus


class DeviceSessionClient(BaseModel):
    """Structured CLI identity surfaced on the authorize page.

    Truncating each field defensively so a hostile client can't blow up
    the cache bag. Empty strings allowed, older / stripped-down CLIs
    just won't populate everything.
    """

    model_config = ConfigDict(extra="ignore")

    name: str = ""
    version: str = ""
    hostname: str = ""

    @field_validator("name", "version", "hostname", mode="before")
    @classmethod
    def _truncate(cls, v: Any, info: Any) -> str:
        caps = {"name": 64, "version": 32, "hostname": 128}
        return str(v or "")[: caps[info.field_name]]


class StoredUser(BaseModel):
    """The shared `AuthUser` contract (built by `auth_user_wire`, in JSON form so
    `created_at` is an ISO string) that the completed bag carries forward for the
    CLI. Strict like the contract: `account_id` / `created_at` are non-null (a
    user always belongs to an account), so they aren't Optional here either."""

    model_config = ConfigDict(extra="ignore")

    id: str
    email: str
    account_id: str
    created_at: str


class DeviceSessionState(BaseModel):
    """Cache-bag shape for a device-flow session.

    One model, two lifecycle phases:
      - PENDING:   token fields are None
      - COMPLETED: token fields are populated by `complete_with()`
    `device_secret_hash` is carried across phases so the CLI's
    post-complete poll authenticates correctly.
    """

    model_config = ConfigDict(extra="ignore")

    status: DeviceSessionStatus
    created_at: str
    user_code: str
    device_secret_hash: str
    initiator_ip: str | None = None
    initiator: DeviceSessionClient = Field(default_factory=DeviceSessionClient)

    # Token fields. None on pending; populated on completed.
    access_token: str | None = None
    refresh_token: str | None = None
    expires_in: int | None = None
    token_type: str | None = None
    user: StoredUser | None = None

    @property
    def is_completed(self) -> bool:
        return self.status == DeviceSessionStatus.COMPLETED

    def complete_with(
        self,
        *,
        access_token: str,
        refresh_token: str,
        expires_in: int,
        token_type: str,
        user: dict[str, Any],
    ) -> DeviceSessionState:
        """Return a new state in the COMPLETED phase. Preserves the
        pending bag's user_code / device_secret_hash / initiator info
        so the CLI's post-complete poll still authenticates and we
        keep an audit trail.
        """
        return self.model_copy(
            update={
                "status": DeviceSessionStatus.COMPLETED,
                "access_token": access_token,
                "refresh_token": refresh_token,
                "expires_in": expires_in,
                "token_type": token_type,
                "user": StoredUser.model_validate(user),
            }
        )

    def cli_poll_view(self) -> dict[str, Any]:
        """Shape returned by GET /device-sessions/{id} (CLI polling).
        Strips internal fields (user_code, device_secret_hash,
        initiator metadata) and drops Nones so pending and completed
        responses are tight.
        """
        return self.model_dump(
            mode="json",
            exclude={
                "user_code",
                "device_secret_hash",
                "initiator_ip",
                "initiator",
            },
            exclude_none=True,
        )

    def info_view(self) -> dict[str, Any]:
        """Shape returned by GET /device-sessions/{id}/info (browser,
        cookie-auth). Surfaces the audit metadata; never the
        verification code or the device-secret hash.
        """
        return {
            "status": self.status,
            "created_at": self.created_at,
            "initiator_ip": self.initiator_ip,
            "initiator": self.initiator.model_dump(),
        }


class Store:
    """Cache-backed I/O for `DeviceSessionState`. Encapsulates key
    construction, TTL selection, and serialization so views never reach
    into `django.core.cache` directly with raw dicts.
    """

    @staticmethod
    def _key(session_id: str) -> str:
        return f"device_session:{session_id}"

    @staticmethod
    def get(session_id: str) -> DeviceSessionState | None:
        raw = cache.get(Store._key(session_id))
        if raw is None:
            return None
        return DeviceSessionState.model_validate(raw)

    @staticmethod
    def put(session_id: str, state: DeviceSessionState) -> None:
        # TTL picks itself off the state's phase so callers don't need
        # to remember which TTL to use.
        ttl = (
            settings.DEVICE_SESSION_COMPLETED_TTL_SECONDS
            if state.is_completed
            else settings.DEVICE_SESSION_PENDING_TTL_SECONDS
        )
        cache.set(Store._key(session_id), state.model_dump(mode="json"), timeout=ttl)

    @staticmethod
    def delete(session_id: str) -> None:
        cache.delete(Store._key(session_id))
