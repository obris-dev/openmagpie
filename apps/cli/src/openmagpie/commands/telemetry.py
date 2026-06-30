"""`magpie telemetry status | enable | disable` -- read or change the instance's
anonymous-telemetry mode from the CLI.

`status` is readable by any signed-in user; `enable`/`disable` require being an
account owner (the server gates it). They send a consent INTENT and the server
resolves the concrete mode. Thin wrapper over the `/v1/telemetry` resource client.
See apps/core/TELEMETRY.md for exactly what anonymous telemetry collects.
"""

from __future__ import annotations

import sys

import httpx
import typer
from pydantic import ValidationError

from .. import console
from ..config import save
from ..context import AppContext, app_ctx
from ..http import ApiError
from ._shared import _handle_api_errors

telemetry_app = typer.Typer(no_args_is_help=True)


@telemetry_app.command("status")
@_handle_api_errors
def status() -> None:
    """Show the instance's anonymous-telemetry mode."""
    # No --jsonl/-o by design: this mirrors `auth status` (a human-facing operator
    # status readout), NOT the resource reads (feed/watch/activity/delivery get|list)
    # that the "every read carries machine output" rule governs. A script that needs
    # the mode programmatically reads GET /v1/telemetry (the typed TelemetryState).
    ac = app_ctx()
    state = ac.api.telemetry.get()
    console.log(f"Telemetry mode: {state.mode}")
    # Server-computed: the mode alone is ambiguous (opt-out: `unset` means ON), and the
    # client can't derive emission (DO_NOT_TRACK / key are server-side). Omit if the
    # server didn't report it (older server).
    if state.emitting is not None:
        console.log(f"Emitting now:   {'yes' if state.emitting else 'no'}")
    console.log(
        "Change it with `magpie telemetry enable` / `disable`."
        if state.can_set
        else "Only an account owner can change it."
    )


# enable/disable send a consent INTENT; the server resolves the concrete mode
# (self-hosted: enable -> anonymous, disable -> off). A 403 (not an account owner)
# is rendered from the server's detail by the shared @_handle_api_errors handler.
@telemetry_app.command("enable")
@_handle_api_errors
def enable() -> None:
    """Turn on anonymous telemetry (account owner only)."""
    state = app_ctx().api.telemetry.set_enabled(enabled=True)
    console.success(f"Telemetry enabled (mode: {state.mode}).")


@telemetry_app.command("disable")
@_handle_api_errors
def disable() -> None:
    """Turn off telemetry (account owner only)."""
    state = app_ctx().api.telemetry.set_enabled(enabled=False)
    console.success(f"Telemetry disabled (mode: {state.mode}).")


def _mark_disclosed(ac: AppContext) -> None:
    """Record (on this machine) that we've shown the telemetry disclosure, so we don't
    repeat it on every login."""
    ac.config.telemetry_disclosed = True
    save(ac.config)


def notice_after_login(ac: AppContext) -> None:
    """Show the one-time anonymous-telemetry disclosure once after a successful
    login, while the server is still on the opt-out default (UNSET). Telemetry is
    opt-OUT (on by default) and INSTANCE-wide, so EVERY user is disclosed to, not
    just owners, but the text is tailored: an owner gets the off switch, a member
    (who can't change the account setting) gets a 'managed by your owner' note
    instead, never the `telemetry disable` verb that would 403 for them. This only
    INFORMS, never changes the mode. Best-effort + interactive-only (skips piped/
    headless logins); never disrupts login. `telemetry_disclosed` in the local config
    stops the repeat on this machine.
    """
    try:
        if ac.config.telemetry_disclosed or not sys.stdin.isatty():
            return
        state = ac.api.telemetry.get()
        if not state.is_unset:
            _mark_disclosed(ac)  # a genuine server-side decision already exists; nothing to disclose
            return
        if state.emitting is not True:
            # unset but not actually emitting: suppressed server-side (emitting False:
            # DO_NOT_TRACK / no key) or a server too old to report it (emitting None,
            # pre-opt-out where unset is silent). Nothing to disclose YET, and crucially
            # do NOT mark disclosed: emission can later turn on (suppression lifted, or
            # the server upgraded to opt-out), and a future login must still fire the
            # one-time notice. Costs one GET per interactive login until then; marking
            # would burn the one-shot permanently.
            return
        # unset AND positively emitting: disclose once (to owners AND members, since
        # opt-out collects for the whole instance), marking so it doesn't repeat.
        _mark_disclosed(ac)
        console.log("")
        if state.can_set:
            console.log(
                "OpenMagpie collects ANONYMOUS usage telemetry (no content, no personal data) to help prioritize what to build."
            )
            console.log("It's on by default. Turn it off any time: magpie telemetry disable (or set DO_NOT_TRACK=1).")
        else:
            # A member can't flip the account-level setting (it 403s) and can't reach
            # the server's DO_NOT_TRACK, so point at the owner + the doc, not a verb
            # they can't run.
            console.log(
                "This OpenMagpie instance sends ANONYMOUS usage telemetry (no content, no personal data) to help prioritize what to build."
            )
            console.log(
                "It's on by default and managed by your account owner. "
                "Details: https://github.com/obris-dev/openmagpie/blob/main/apps/core/TELEMETRY.md"
            )
    except KeyboardInterrupt:
        return  # login already succeeded; a Ctrl-C during the status read must not disturb it
    except (ApiError, httpx.HTTPError, OSError, ValidationError, ValueError):
        # Swallow ONLY the expected best-effort failures: the status read (API /
        # transport error; a wrong-shaped body the wire model rejects -> ValidationError;
        # a corrupt JSON 2xx body -> json.JSONDecodeError, a ValueError) and the config
        # save (OSError). ValueError also covers a closed stdin from isatty(). None should
        # disturb a login that already succeeded, and the CLI has no logging sink. A logic
        # bug raises something else (AttributeError/TypeError/...) and is deliberately NOT
        # caught here; it should surface.
        return
