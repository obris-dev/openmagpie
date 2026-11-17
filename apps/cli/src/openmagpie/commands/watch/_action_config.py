"""`magpie watch action config`: show an action's config, or change one value
in it, the rest kept.

`--secret` says the value is a secret: it is never taken from argv, only
piped or typed at a hidden prompt, and it is kept as text. The caller says
so, since only they know a value they are adding is secret. A key whose
current value comes back masked is refused without `--secret`, a guard that
stops a mistake but never decides on its own. Any other value takes the type
of the one it replaces, so `300` stays a number where the config holds a
number and text where it holds text.
"""

from __future__ import annotations

import json
import sys
from typing import Any, NoReturn

import typer
import yaml
from pydantic import ValidationError

from openmagpie_schema.watch import build_watch_action_input
from openmagpie_schema.watch_actions._secrets import looks_redacted_url

from ... import console
from ...context import app_ctx
from .._shared import _abort_union_validation_error, _handle_api_errors, _read_secret
from ._actions import _print_action_detail, _run_action_mutation
from ._apps import action_app
from ._config import _edit_seed_config


@action_app.command("config")
@_handle_api_errors
def action_config(
    action_id: str = typer.Argument(..., help="Action id (from `magpie watch action list`)."),
    key: str | None = typer.Argument(
        None, help="A dotted path into the config: `url`, `digest_interval_seconds`, `fields.c_title`."
    ),
    value: str | None = typer.Argument(None, help="The new value. Omit to pipe it or be asked for it."),
    secret: bool = typer.Option(
        False,
        "--secret",
        help="The value is a secret (a URL with a token, a header): piped or typed hidden, never an argument.",
    ),
    dry_run: bool = typer.Option(False, "--dry-run", "-n", help="Validate server-side and show the result, then stop."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt. Required for piped input."),
) -> None:
    """Show an action's config, or change one value in it.

    With no key, prints the config (secrets masked). With a key and a value,
    sets it, keeping the rest; omit the value to pipe it or be asked for it.
    Give a secret, such as a webhook or bower URL, with --secret: it is then
    piped or typed hidden, never taken as an argument."""
    api = app_ctx().api.watch
    current = api.get_action(action_id)
    config, corrupt = _edit_seed_config(current)
    if corrupt:
        console.error(f"Action {action_id}'s stored config is unreadable; replace it with `magpie watch action edit`.")
        raise typer.Exit(code=1)
    if key is None:
        console.log(yaml.safe_dump(config, sort_keys=False).rstrip())
        return
    parent, leaf = _config_parent(config, key)
    existing = parent.get(leaf)
    masked = isinstance(existing, str) and looks_redacted_url(existing)
    if masked and not secret:
        console.error(f"{key} is a secret. Add --secret, then pipe it or type it hidden.")
        raise typer.Exit(code=1)
    if secret and value is not None:
        console.error(f"With --secret, {key} is piped or typed hidden, never given as an argument.")
        raise typer.Exit(code=1)
    if not yes and not dry_run and not sys.stdin.isatty():
        console.error(
            "Input is piped, so there is no prompt to confirm. Check it with --dry-run, then re-run with --yes."
        )
        raise typer.Exit(code=1)
    text = value if value is not None else _ask(key, secret=secret)
    if not text:
        console.error(f"No value for {key}.")
        raise typer.Exit(code=1)
    parent[leaf] = text if secret else _typed(key, text, existing, parent)
    kind = str(current.kind)
    try:
        build_watch_action_input(kind=kind, config=config)
    except ValidationError as e:
        _abort_union_validation_error(e, header="Action config error:")
    updated = _run_action_mutation(
        lambda dr: api.edit_action(action_id, kind, config, dry_run=dr), is_edit=True, dry_run=dry_run, yes=yes
    )
    if updated is not None:
        _print_action_detail(updated, title=f"Set {key} on action {updated.id}:")


def _config_parent(config: dict[str, Any], key: str) -> tuple[dict[str, Any], str]:
    """The dict holding `key`'s last part, walking its dotted path. Every part
    before the last must already name an object, and none may be empty."""
    parts = key.split(".")
    if any(not part for part in parts):
        console.error(f"{key}: a path part is empty.")
        raise typer.Exit(code=1)
    *path, leaf = parts
    parent = config
    for part in path:
        child = parent.get(part)
        if not isinstance(child, dict):
            console.error(f"{key}: the config has no object at {part!r}.")
            raise typer.Exit(code=1)
        parent = child
    return parent, leaf


def _ask(key: str, *, secret: bool) -> str:
    """The value from piped stdin, else a prompt: hidden for a secret."""
    if secret or not sys.stdin.isatty():
        return _read_secret(key)
    answer = typer.prompt(key, default="", show_default=False)
    return answer.strip()


def _typed(key: str, text: str, existing: Any, parent: dict[str, Any]) -> Any:
    """`text` as the type of the value it replaces. A new key in a map of
    text stays text; any other new key is JSON when it parses, else text."""
    if isinstance(existing, bool):
        if text.lower() not in ("true", "false"):
            _refuse(key, "true or false")
        return text.lower() == "true"
    if isinstance(existing, int):
        try:
            return int(text)
        except ValueError:
            _refuse(key, "a whole number")
    if isinstance(existing, float):
        try:
            return float(text)
        except ValueError:
            _refuse(key, "a number")
    if isinstance(existing, str):
        return text
    if isinstance(existing, (dict, list)):
        parsed = _json(text)
        if not isinstance(parsed, type(existing)):
            _refuse(key, "a JSON object" if isinstance(existing, dict) else "a JSON list")
        return parsed
    siblings = list(parent.values())
    if siblings and all(isinstance(sibling, str) for sibling in siblings):
        return text
    parsed = _json(text)
    return text if parsed is None else parsed


def _json(text: str) -> Any:
    try:
        return json.loads(text)
    except ValueError:
        return None


def _refuse(key: str, wanted: str) -> NoReturn:
    console.error(f"{key} takes {wanted}.")
    raise typer.Exit(code=1)
