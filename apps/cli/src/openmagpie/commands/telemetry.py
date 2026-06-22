"""`magpie telemetry status | enable | disable` -- read or change the instance's
anonymous-telemetry mode from the CLI.

`status` is readable by any signed-in user; `enable`/`disable` require being an
account owner (the server gates it). They send a consent INTENT and the server
resolves the concrete mode. Thin wrapper over the `/v1/telemetry` resource client.
See apps/core/TELEMETRY.md for exactly what anonymous telemetry collects.
"""

from __future__ import annotations

import logging
import sys

import typer

from .. import console
from ..config import save
from ..context import AppContext, app_ctx
from ._shared import _handle_api_errors

logger = logging.getLogger("openmagpie")

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


def _mark_prompted(ac: AppContext) -> None:
    """Record (on this machine) that we've offered the opt-in, so we don't re-ask."""
    ac.config.telemetry_prompted = True
    save(ac.config)


def prompt_after_login(ac: AppContext) -> None:
    """Offer the anonymous-telemetry opt-in once after a successful login -- to an
    account owner, when the server is still undecided. Best-effort + interactive-
    only (skips piped/headless logins); never disrupts login. `telemetry_prompted`
    in the local config stops us re-asking on this machine.
    """
    try:
        if ac.config.telemetry_prompted or not sys.stdin.isatty():
            return
        state = ac.api.telemetry.get()
        if not state.is_unset:
            _mark_prompted(ac)  # already decided server-side
            return
        if not state.can_set:
            return  # not an account owner; leave the decision to an owner login
        # Persist `prompted` BEFORE the prompt + network call, so an EOF/abort or a
        # failed enable doesn't re-offer on every later login (change it later with
        # `magpie telemetry enable` / `disable`).
        _mark_prompted(ac)
        console.log("")
        console.log("Help improve OpenMagpie? Share ANONYMOUS usage so we can prioritize what to build.")
        console.log("It's anonymous (never your content or any personal data) and off until you opt in.")
        if typer.confirm("Enable anonymous telemetry?", default=False):
            try:
                ac.api.telemetry.set_enabled(enabled=True)
            except Exception as exc:
                # They said yes but the POST failed -- tell them, or they'd believe
                # telemetry is on (a swallowed warning is invisible).
                console.warn("Couldn't enable telemetry right now -- run `magpie telemetry enable` to retry.")
                logger.warning("telemetry opt-in POST failed: %s", exc)
                return
            console.success("Anonymous telemetry enabled. Turn it off any time: magpie telemetry disable")
    except (typer.Abort, KeyboardInterrupt):
        return  # EOF / Ctrl-C at the prompt; login already succeeded, don't fail it
    except Exception:
        # The expected paths -- abort/cancel and a failed opt-in POST -- are handled
        # above, so reaching here is genuinely unexpected; log with a traceback like
        # the other telemetry swallow paths (capture / events.guard).
        logger.exception("post-login telemetry prompt failed")
