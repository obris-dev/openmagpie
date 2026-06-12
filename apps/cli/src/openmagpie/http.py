"""HTTP client with auto token-refresh.

Pure transport. Callers pass full path strings (typically from the
`routes` module). The client adds the server base URL, the Bearer
header, and handles the refresh-near-expiry dance.

Refresh failures bubble up as `AuthError`; commands decide whether to
ask the user to re-run `magpie auth login`.
"""

from __future__ import annotations

import json
import os
import platform
import socket
from typing import Any

import httpx

from . import __version__, routes
from .config import Config, UserInfo, save
from .constants import AUTHORIZATION_HEADER, BEARER_SCHEME, TOKEN_ENV_VAR

REFRESH_LEEWAY_SECONDS = 5 * 60

# Explicit verify on the httpx.Client. httpx defaults to True; we name
# it in code so it's never silently downgraded by a future library
# default change. Set MAGPIE_INSECURE_SKIP_TLS_VERIFY=1 to opt out
# (corporate-MITM proxy scenarios only).
_VERIFY_TLS = os.environ.get("MAGPIE_INSECURE_SKIP_TLS_VERIFY") != "1"


def _hostname() -> str:
    """Best-effort hostname; falls back to 'unknown' rather than raising.

    `socket.gethostname()` raises `OSError` (gaierror is a subclass) on
    DNS / name-resolution failure ; nothing else is a normal outcome."""
    try:
        return socket.gethostname() or "unknown"
    except OSError:
        return "unknown"


def client_info() -> dict[str, str]:
    """Structured identity payload sent to the device-flow authorize
    endpoint. Just enough for a human to answer "is this my CLI on my
    machine?":

      - `name` + `version`: which product is asking
      - `hostname`: which machine it's running on

    OS / Python runtime details are intentionally left out, they're
    debug noise on a security UI, and we don't audit them anyway.
    """
    return {
        "name": "magpie-cli",
        "version": __version__,
        "hostname": _hostname(),
    }


def _user_agent() -> str:
    """HTTP User-Agent for log / debugging visibility only.

    NOT the source of truth for the authorize page, that uses the
    structured `client_info` in the request body. The UA exists so
    server logs say something more useful than `python-httpx/0.28.x`.
    """
    return f"magpie-cli/{__version__} ({platform.system()}; Python/{platform.python_version()})"


class ApiError(Exception):
    """HTTP error from the API.

    `status` is the response code; `body` is the parsed response payload
    kept as an attribute for callers that want to introspect.

    `body` is intentionally OMITTED from str(). Response bodies can
    contain tokens (the refresh-rotation path echoes them on success,
    and a misbehaving server might echo them on failure too); bare
    tracebacks / log lines would leak them. Callers that want body info
    access `e.body` explicitly and own the decision to print or redact.
    """

    def __init__(self, status: int, body: Any):
        self.status = status
        self.body = body
        super().__init__(f"HTTP {status}")


class AuthError(ApiError):
    """401 or refresh failure. The user needs to log in again."""


class MagpieClient:
    def __init__(self, config: Config) -> None:
        self.config = config
        self._client = httpx.Client(
            base_url=config.server_url,
            timeout=30.0,
            headers={"User-Agent": _user_agent()},
            verify=_VERIFY_TLS,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> MagpieClient:
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()

    def _ambient_token(self) -> str | None:
        """A `MAGPIE_TOKEN` from the environment, or None.

        Ambient credential: read per request, takes precedence over the
        stored login, never persisted. The standard "token in an
        environment" pattern, so a box can `export MAGPIE_TOKEN=...` and
        skip `auth login` entirely.

        Whatever value it holds is sent as-is and NEVER refreshed, so it
        should be a long-lived PAT (`mgp_...`). A short-lived session
        access token would work here only until it expires, then 401 with
        no recovery (unlike a login-stored session token, which rotates).
        """
        return os.environ.get(TOKEN_ENV_VAR) or None

    def _bearer_token(self) -> str | None:
        return self._ambient_token() or self.config.access_token

    def _auth_headers(self) -> dict[str, str]:
        token = self._bearer_token()
        if token:
            return {AUTHORIZATION_HEADER: f"{BEARER_SCHEME} {token}"}
        return {}

    def _ensure_fresh_token(self) -> None:
        # An ambient MAGPIE_TOKEN is used as-is, never rotated (it isn't
        # ours to refresh, and refreshing would touch the stored login we
        # are deliberately overriding).
        if self._ambient_token():
            return
        if not self.config.refresh_token:
            return
        if not self.config.access_token_expired(leeway_seconds=REFRESH_LEEWAY_SECONDS):
            return
        self._refresh()

    def _refresh(self) -> None:
        """Internal: rotate tokens via /v1/auth/tokens/refresh. Lives here
        (not in api/auth.py) because it's transport-level. Public-API
        refreshes go through `api.auth` for consistency.

        Failure modes:
          - Transport error (httpx raises): propagates as-is. Credentials
            are NOT cleared, the network might be back next call.
          - Non-2xx from the server: token is dead; clear local creds +
            save before raising AuthError so the next command sees "not
            authenticated" instead of looping on the same dead token.
        """
        assert self.config.refresh_token
        resp = self._client.post(
            routes.auth.tokens.refresh,
            json={"refresh_token": self.config.refresh_token},
        )
        if resp.status_code == 401:
            # Server explicitly rejected the refresh token (unknown or
            # revoked). The local pair is dead; wipe so the next command
            # sees "not authenticated" rather than looping.
            self.config.clear_credentials()
            save(self.config)
            raise AuthError(resp.status_code, _safe_json(resp))
        if resp.status_code != 200:
            # 4xx (other than 401), 5xx, or unfollowed 3xx, none of
            # these mean "your token is bad." Surface the failure but
            # leave local creds intact so the next attempt can succeed.
            raise ApiError(resp.status_code, _safe_json(resp))
        body = resp.json()
        user = UserInfo.model_validate(body["user"]) if body.get("user") else None
        self.config.apply_credentials(
            access_token=body["access_token"],
            refresh_token=body["refresh_token"],
            expires_in=int(body["expires_in"]),
            user=user,
        )
        save(self.config)

    def _build_headers(self, extra: dict[str, str] | None) -> dict[str, str]:
        h = self._auth_headers()
        if extra:
            h.update(extra)
        return h

    def get(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        with_auth: bool = True,
        headers: dict[str, str] | None = None,
    ) -> Any:
        if not with_auth:
            # Skip the stored bearer entirely. Used by the device-flow poll,
            # which is a (re-)login operation authenticated by X-Device-Secret,
            # not the bearer: attaching a stale/revoked credential here would
            # get the poll itself rejected with 401 before the device secret
            # is ever checked.
            merged = dict(headers) if headers else {}
            resp = self._client.get(path, headers=merged, params=params)
            return _handle(resp)
        return self._authed_request("GET", path, params=params, headers=headers)

    def post(
        self,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        with_auth: bool = True,
        headers: dict[str, str] | None = None,
    ) -> Any:
        body = json_body or {}
        if not with_auth:
            # No token to refresh = no retry. Used by /tokens/refresh
            # itself (would loop) and pre-login endpoints.
            merged = dict(headers) if headers else {}
            resp = self._client.post(path, headers=merged, json=body, params=params)
            return _handle(resp)
        return self._authed_request("POST", path, json_body=body, params=params, headers=headers)

    def put(
        self,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        return self._authed_request("PUT", path, json_body=json_body or {}, params=params, headers=headers)

    def delete(
        self,
        path: str,
        *,
        headers: dict[str, str] | None = None,
    ) -> Any:
        return self._authed_request("DELETE", path, headers=headers)

    def _authed_request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        """Authenticated request with one-shot refresh-on-401 retry.

        Two refresh triggers:
          - `_ensure_fresh_token` covers expected expiries (local clock).
          - The 401 branch catches server-side early revocation (admin
            force-logout, revoke-from-another-device, key rotation)
            while our clock still thinks the token is fresh.

        Single retry only. The absence of a third `issue()` is the
        guard: if the replayed request also returns 401, `_handle`
        raises AuthError for the command layer to surface.
        """

        def issue() -> httpx.Response:
            return self._client.request(
                method,
                path,
                headers=self._build_headers(headers),
                params=params,
                json=json_body,
            )

        self._ensure_fresh_token()
        resp = issue()
        # Don't refresh on behalf of an ambient MAGPIE_TOKEN: it has no
        # refresh token and we'd be rotating the stored login we're
        # overriding. A 401 on an ambient token just surfaces as AuthError.
        if resp.status_code == 401 and not self._ambient_token() and self.config.refresh_token:
            self._refresh()
            resp = issue()
        return _handle(resp)


def _safe_json(resp: httpx.Response) -> Any:
    """Used to format error payloads ; some servers return text/plain
    on errors. Narrow to the json-decode paths so unrelated bugs (e.g.
    a programming error in `resp`) propagate instead of being papered
    over with the raw response text."""
    try:
        return resp.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        return resp.text


def _handle(resp: httpx.Response) -> Any:
    if 200 <= resp.status_code < 300:
        if resp.headers.get("content-type", "").startswith("application/json"):
            return resp.json()
        return resp.text
    if resp.status_code == 401:
        raise AuthError(resp.status_code, _safe_json(resp))
    raise ApiError(resp.status_code, _safe_json(resp))
