"""`magpie watch connect-bower`: send a watch's items to OpenBower.

Adds a `bower` action delivering to a custom signal: an existing one, by its
webhook URL (`--webhook-url`, read from piped stdin or a hidden prompt), or a
new one created with a column per item field (`--api-url`, with an OpenBower
API key from `$OPENBOWER_API_KEY`, piped stdin, or a hidden prompt). Neither
secret is ever read from argv, and the key is never stored.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass

import typer
from pydantic import ValidationError

from openmagpie_schema.watch import (
    BowerConnectColumn,
    BowerConnectExisting,
    BowerConnectInput,
    BowerConnectNew,
    BowerConnectResponse,
)
from openmagpie_schema.watch_enums import DeliveryCadence

from ... import console
from ...context import app_ctx
from .._shared import _abort_unexpected, _abort_union_validation_error, _handle_api_errors, _read_secret
from ._actions import _print_action_detail
from ._apps import watch_app

API_KEY_ENV_VAR = "OPENBOWER_API_KEY"


@watch_app.command("connect-bower")
@_handle_api_errors
def connect_bower(
    watch_id: str = typer.Argument(..., help="Watch id (from `magpie watch list`)."),
    webhook_url: bool = typer.Option(
        False, "--webhook-url", help="Connect an existing signal by its webhook URL, piped or typed when asked."
    ),
    api_url: str = typer.Option(
        "", "--api-url", help="Create a new signal through your OpenBower API, e.g. https://api.example.io."
    ),
    label: str = typer.Option("", "--label", help="A new signal's name in OpenBower. Defaults to the watch's name."),
    fields: str = typer.Option(
        "", "--fields", help="Item fields to send, comma-separated, e.g. title,url. Sends every field when omitted."
    ),
    digest: int | None = typer.Option(
        None,
        "--digest",
        min=1,
        help="Send items in batches every this many seconds. Omit to send each as it matches.",
    ),
    rank: int | None = typer.Option(
        None, "--rank", "-r", min=0, help="Where the action goes in the chain (0-based). Appends when omitted."
    ),
    dry_run: bool = typer.Option(False, "--dry-run", "-n", help="Show what it would connect, then stop."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt. Required for piped input."),
) -> None:
    """Send a watch's items to an OpenBower signal, as rows.

    With --webhook-url, connects an existing signal: its columns are matched
    to the item fields by name. With --api-url, creates a signal with a
    column per item field the watch's sources produce, using an API key that
    is not kept. --fields sends only the item fields it names (for a new
    signal, in that column order); --dry-run lists every field to pick from.
    Either way the watch gets a `bower` action, and Magpie keeps only the
    signal's webhook URL."""
    if webhook_url == bool(api_url):
        console.error("Give --webhook-url to connect an existing signal, or --api-url to create one.")
        raise typer.Exit(code=1)
    if webhook_url and label:
        console.error("--label names a new signal; an existing one keeps its name.")
        raise typer.Exit(code=1)
    if not yes and not dry_run and not sys.stdin.isatty():
        console.error(
            "Input is piped, so there is no prompt to confirm. Check it with --dry-run, then re-run with --yes, "
            "or run without the pipe to paste at a hidden prompt."
        )
        raise typer.Exit(code=1)
    common = {
        "fields": _field_names(fields),
        "delivery": DeliveryCadence.DIGEST if digest is not None else DeliveryCadence.INSTANT,
        "digest_interval_seconds": digest or 0,
        "rank": rank,
    }
    try:
        if webhook_url:
            body: BowerConnectInput = BowerConnectExisting(webhook_url=_read_webhook_url(), **common)
        else:
            body = BowerConnectNew(api_url=api_url, api_key=_read_api_key(), label=label, **common)
    except ValidationError as exc:
        _abort_union_validation_error(exc, header="Connect error:")
    words = _WORDS[type(body)]
    api = app_ctx().api.watch
    preview = api.connect_bower(watch_id, body, dry_run=True)
    if not preview.dry_run or (isinstance(body, BowerConnectNew) and preview.signal_id):
        raise _abort_unexpected(
            "asked for a dry run but the server reported a created signal", preview.signal_id, noun="signal"
        )
    _print_columns(words.preview.format(label=preview.signal_label, id=preview.signal_id), preview)
    if dry_run:
        if not body.fields:
            _print_fields_hint(preview)
        console.warn("Dry run only. Nothing was changed.")
        return
    if not yes:
        if not typer.confirm(words.question):
            console.warn("Aborted.")
            raise typer.Exit(code=1)
    connected = api.connect_bower(watch_id, body)
    if connected.dry_run or connected.action is None:
        raise _abort_unexpected("the connect did not report the action it added", connected.signal_id, noun="signal")
    console.success(words.done.format(label=connected.signal_label, id=connected.signal_id))
    _print_action_detail(connected.action, title=f"Added action {connected.action.id}:")


@dataclass(frozen=True)
class _Words:
    """A route's copy, each with `{label}` / `{id}` for the signal."""

    preview: str
    question: str
    done: str


_WORDS: dict[type, _Words] = {
    BowerConnectNew: _Words(
        preview="Would create the OpenBower signal '{label}' with these columns:",
        question="Create the signal and add the action?",
        done="Created the OpenBower signal '{label}' ({id}).",
    ),
    BowerConnectExisting: _Words(
        preview="Would send to the OpenBower signal {id}, filling these columns:",
        question="Add the action?",
        done="Connected the OpenBower signal {id}.",
    ),
}


def _print_columns(title: str, response: BowerConnectResponse) -> None:
    console.log(title)
    for column in response.columns:
        if column.field:
            console.log(f"  {_named(column)} ({column.type}) <- {column.field}")
    blank = [_named(column) for column in response.columns if not column.field]
    if blank:
        why = "no chosen field matches" if response.left_out_fields else "no item field matches"
        console.log(f"Left blank ({why}): {', '.join(blank)}")
    if response.unmatched_fields:
        console.log(f"Not sent (no column takes them): {', '.join(response.unmatched_fields)}")
    if response.left_out_fields:
        console.log(f"Not sent (left out by --fields): {', '.join(response.left_out_fields)}")


def _print_fields_hint(response: BowerConnectResponse) -> None:
    """How to send fewer fields, with the first few that would be sent."""
    sent = [column.field for column in response.columns if column.field]
    if len(sent) > 1:
        console.log(f"Send fewer with --fields, e.g. --fields {','.join(sent[:3])}")


def _field_names(fields: str) -> list[str]:
    """`title, url` -> ["title", "url"]; blanks between commas dropped."""
    return [name.strip() for name in fields.split(",") if name.strip()]


def _named(column: BowerConnectColumn) -> str:
    """A column's label, with its key when it has one (an existing signal's,
    or a created one's): the key is what a hand-written `fields` map uses."""
    return f"{column.label} [{column.key}]" if column.key else column.label


def _read_api_key() -> str:
    """The OpenBower API key, never from argv: `$OPENBOWER_API_KEY`, else the
    first line of piped stdin, else a hidden prompt."""
    from_env = os.environ.get(API_KEY_ENV_VAR, "").strip()
    if from_env:
        return from_env
    key = _read_secret("OpenBower API key")
    if not key:
        console.error(f"No API key. Set {API_KEY_ENV_VAR}, pipe the key on stdin, or run interactively.")
        raise typer.Exit(code=1)
    return key


def _read_webhook_url() -> str:
    """The signal's webhook URL, never from argv (its token is the
    credential): the first line of piped stdin, else a hidden prompt."""
    url = _read_secret("Webhook URL")
    if not url:
        console.error("No webhook URL. Pipe it on stdin or run interactively.")
        raise typer.Exit(code=1)
    return url
