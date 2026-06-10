"""Centralized API route paths used by the CLI.

Only paths the CLI itself calls live here, not the full server surface.
Browser-only endpoints (signup, login, logout) are intentionally absent:
the CLI's path to credentials is device-flow, and its "logout" goes
through `tokens.revoke`.

Grouped into class-namespaces per domain so call sites read naturally:
`routes.auth.me`, `routes.auth.tokens.refresh`,
`routes.auth.device_session(sid)`.
"""

from __future__ import annotations

API_VERSION = "v1"
_AUTH = f"/{API_VERSION}/auth"
_FEEDS = f"/{API_VERSION}/feeds"
_WATCHES = f"/{API_VERSION}/watches"
_FEED_SOURCES = f"/{API_VERSION}/feed-sources"
_ACTIONS = f"/{API_VERSION}/actions"
_ACTION_DELIVERIES = f"/{API_VERSION}/action-deliveries"
_ENGINES = f"/{API_VERSION}/engines"


class auth:
    """`/v1/auth/*` routes the CLI consumes."""

    base = _AUTH
    me = f"{_AUTH}/me"
    device_sessions = f"{_AUTH}/device-sessions"

    @staticmethod
    def device_session(session_id: str) -> str:
        return f"{_AUTH}/device-sessions/{session_id}"

    class tokens:
        """`/v1/auth/tokens/*`, bearer token lifecycle (refresh + revoke)."""

        refresh = f"{_AUTH}/tokens/refresh"
        revoke = f"{_AUTH}/tokens/revoke"


class feeds:
    """`/v1/feeds/*` routes the CLI consumes."""

    collection = _FEEDS

    @staticmethod
    def detail(feed_id: str) -> str:
        return f"{_FEEDS}/{feed_id}"

    @staticmethod
    def sources(feed_id: str) -> str:
        return f"{_FEEDS}/{feed_id}/sources"


class feed_sources:
    """`/v1/feed-sources/<id>` — one source by its own ULID (a dependent record
    of its feed; the feed is resolved server-side, not passed in the path)."""

    @staticmethod
    def detail(source_id: str) -> str:
        return f"{_FEED_SOURCES}/{source_id}"


class watches:
    """`/v1/watches/*` routes the CLI consumes."""

    collection = _WATCHES

    @staticmethod
    def detail(watch_id: str) -> str:
        return f"{_WATCHES}/{watch_id}"

    @staticmethod
    def actions(watch_id: str) -> str:
        # Chain-level list/add stay watch-scoped (no action id yet). Per-action
        # ops (edit/remove/runs) live under `routes.actions`, keyed on the
        # action's own id.
        return f"{_WATCHES}/{watch_id}/actions"


class actions:
    """`/v1/actions/*` — per-action ops keyed on the action's own ULID. The
    audit LISTS are nested here (`activity` = the run log, `deliveries`); the
    by-own-id detail of one run / delivery is parent-qualified (see `activity`
    / `deliveries` below)."""

    @staticmethod
    def detail(action_id: str) -> str:
        return f"{_ACTIONS}/{action_id}"

    @staticmethod
    def runs(action_id: str) -> str:
        # Method name follows the model (WatchActionRun); path follows the public
        # noun (activity). Phase 2 reshapes this client, so leave the name as-is.
        return f"{_ACTIONS}/{action_id}/activity"

    @staticmethod
    def deliveries(action_id: str) -> str:
        return f"{_ACTIONS}/{action_id}/deliveries"


class deliveries:
    """`/v1/action-deliveries/*` — one delivery's detail by its own ULID
    (a dependent record of its action, so the route is parent-qualified)."""

    @staticmethod
    def detail(delivery_id: str) -> str:
        return f"{_ACTION_DELIVERIES}/{delivery_id}"


class engines:
    """`/v1/engines/*` routes the CLI consumes."""

    collection = _ENGINES
