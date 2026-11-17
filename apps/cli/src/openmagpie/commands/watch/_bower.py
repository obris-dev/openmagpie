"""`magpie watch connect-bower`: send a watch's items to OpenBower.

Creates a custom signal in OpenBower whose columns are the watch's item
fields, and adds a `bower` action delivering to it. The OpenBower API key is
used for that one create, server-side, and never stored: read from
`$OPENBOWER_API_KEY`, piped stdin, or a hidden prompt, never argv.
"""

from __future__ import annotations

import getpass
import os
import sys

import typer
from pydantic import ValidationError

from openmagpie_schema.watch import BowerConnectInput, BowerConnectResponse
from openmagpie_schema.watch_enums import DeliveryCadence

from ... import console
from ...context import app_ctx
from .._shared import _abort_unexpected, _handle_api_errors
from ._actions import _print_action_detail
from ._apps import watch_app

API_KEY_ENV_VAR = "OPENBOWER_API_KEY"


@watch_app.command("connect-bower")
@_handle_api_errors
def connect_bower(
    watch_id: str = typer.Argument(..., help="Watch id (from `magpie watch list`)."),
    api_url: str = typer.Option(..., "--api-url", help="Your OpenBower API's address, e.g. https://api.example.io."),
    label: str = typer.Option("", "--label", help="The signal's name in OpenBower. Defaults to the watch's name."),
    digest: int | None = typer.Option(
        None, "--digest", help="Send items in batches every this many seconds. Omit to send each as it matches."
    ),
    dry_run: bool = typer.Option(False, "--dry-run", "-n", help="Show the signal it would create, then stop."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt. Required for piped input."),
) -> None:
    """Create an OpenBower signal for a watch and send its items there.

    The signal gets one column per item field the watch's sources produce, and
    the watch gets a `bower` action that delivers each matching item as a row.
    The API key creates the signal and is not kept; Magpie keeps only the
    signal's webhook URL."""
    key = _read_api_key()
    if not key:
        console.error(f"No API key. Set {API_KEY_ENV_VAR}, pipe the key on stdin, or run interactively.")
        raise typer.Exit(code=1)
    try:
        body = BowerConnectInput(
            api_url=api_url,
            api_key=key,
            label=label,
            delivery=DeliveryCadence.DIGEST if digest else DeliveryCadence.INSTANT,
            digest_interval_seconds=digest or 0,
        )
    except ValidationError as exc:
        for error in exc.errors():
            console.error(f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}")
        raise typer.Exit(code=1) from None
    api = app_ctx().api.watch
    preview = api.connect_bower(watch_id, body, dry_run=True)
    if not preview.dry_run or preview.signal_id:
        raise _abort_unexpected(
            "asked for a dry run but the server reported a created signal", preview.signal_id, noun="signal"
        )
    _print_columns(f"Would create the OpenBower signal '{preview.signal_label}' with these columns:", preview)
    if dry_run:
        console.warn("Dry run only. Nothing was changed.")
        return
    if not yes:
        if not sys.stdin.isatty():
            console.warn("Piped input: can't prompt for confirmation. Re-run with --yes to connect.")
            raise typer.Exit(code=1)
        if not typer.confirm("Create the signal and add the action?"):
            console.warn("Aborted.")
            raise typer.Exit(code=1)
    created = api.connect_bower(watch_id, body)
    if created.dry_run or created.action is None:
        raise _abort_unexpected("the connect did not report the action it added", created.signal_id, noun="signal")
    console.success(f"Created the OpenBower signal '{created.signal_label}' ({created.signal_id}).")
    _print_action_detail(created.action, title=f"Added action {created.action.id}:")


def _print_columns(title: str, response: BowerConnectResponse) -> None:
    console.log(title)
    for column in response.columns:
        console.log(f"  {column.label} ({column.type}) <- {column.field}")


def _read_api_key() -> str:
    """The OpenBower API key, never from argv: `$OPENBOWER_API_KEY`, else the
    first line of piped stdin, else a hidden prompt."""
    from_env = os.environ.get(API_KEY_ENV_VAR, "").strip()
    if from_env:
        return from_env
    if not sys.stdin.isatty():
        return sys.stdin.readline().strip()
    try:
        return getpass.getpass("OpenBower API key: ").strip()
    except (EOFError, KeyboardInterrupt):
        console.warn("\nCancelled.")
        raise typer.Exit(code=130) from None
