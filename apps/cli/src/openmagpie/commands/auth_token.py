"""`magpie auth token ...`, manage personal access tokens.

The `token_app` Typer sub-app is mounted on `auth_app` in `auth.py`. The
cold-start (mint your FIRST token on a box you can't yet log into) goes
through the server's `issue_cli_token` management command instead; these
commands are the convenience once you're already authenticated via a
browser login (a PAT can't mint another token).
"""

from __future__ import annotations

import typer

from .. import console
from ..constants import PERSONAL_ACCESS_TOKEN_PREFIX
from ..context import app_ctx
from ._shared import (
    _columns_option,
    _emit_columns_items,
    _handle_api_errors,
    _jsonl_rows_option,
    _list_output_option,
    _print_columns_option,
    _transpose_option,
    _ts,
    col,
)

token_app = typer.Typer(no_args_is_help=True, help="Create, list, and revoke personal access tokens.")

_TOKEN_COLUMNS = [
    col("ID:id"),
    col("NAME:name"),
    col("TOKEN:last_four", fmt=lambda v: f"{PERSONAL_ACCESS_TOKEN_PREFIX}…{v}"),
    col("CREATED:created_at", fmt=_ts),
    col("LAST USED:last_used_at", fmt=_ts),
    col("EXPIRES:expires_at", fmt=_ts),
]


@token_app.command("create")
@_handle_api_errors
def token_create(
    name: str = typer.Option(..., "--name", help="Label to tell tokens apart (e.g. 'home-office box')."),
    expires_in_days: int | None = typer.Option(
        None,
        "--expires-in-days",
        help="Optional expiry in days. Omit for a non-expiring token.",
    ),
) -> None:
    """Mint a personal access token (printed once).

    Requires a browser login; a PAT can't mint another token, so a
    PAT-authenticated request gets a 403 with that explanation.
    """
    created = app_ctx().api.auth.cli_tokens.create(name=name, expires_in_days=expires_in_days)
    console.success(f"Created token '{created.name}' ({created.id}).")
    console.log("")
    console.log(created.token)
    console.log("")
    console.warn("Copy it now, it will NOT be shown again.")


@token_app.command("list")
@_handle_api_errors
def token_list(
    columns: str | None = _columns_option(),
    transpose: bool = _transpose_option("token"),
    print_columns: bool = _print_columns_option("token"),
    jsonl: bool = _jsonl_rows_option("token"),
    output: str | None = _list_output_option(paginated=False),
) -> None:
    """List your active personal access tokens (the secret is never shown)."""
    tokens = app_ctx().api.auth.cli_tokens.list()
    _emit_columns_items(
        items=tokens,
        record_of=lambda t: t.model_dump(mode="json"),
        default_columns=_TOKEN_COLUMNS,
        columns=columns,
        transpose=transpose,
        print_columns=print_columns,
        jsonl=jsonl,
        output=output,
        empty_msg="No personal access tokens. Create one with `magpie auth token create --name <name>`.",
        header=f"{len(tokens)} token(s)",
    )


@token_app.command("revoke")
@_handle_api_errors
def token_revoke(
    token_id: str = typer.Argument(..., help="The id of the token to revoke (from `magpie auth token list`)."),
) -> None:
    """Revoke a personal access token by id."""
    app_ctx().api.auth.cli_tokens.revoke(token_id)
    console.success(f"Revoked token {token_id}.")
