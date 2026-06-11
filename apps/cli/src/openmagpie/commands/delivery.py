"""`magpie delivery ...`: the outbound webhook-call audit for one action.

Flat, top-level observability noun (not nested under `watch action`). `list` is
the per-attempt call log; `get` is one call in full, including the exact request
body sent. Both scope to an action via `--action` ; `get` takes the delivery's
own id. Only `webhook` actions make HTTP calls, so other kinds list empty.
"""

from __future__ import annotations

import json

import typer

from openmagpie_schema.watch import WatchActionDeliveryView
from openmagpie_schema.watch_enums import WatchActionDeliveryState, choices

from .. import console
from ..context import app_ctx
from ._shared import (
    _check_choice,
    _columns_option,
    _emit_columns_paginated,
    _emit_detail,
    _handle_api_errors,
    _jsonl_rows_option,
    _list_output_option,
    _print_columns_option,
    _print_detail,
    _transpose_option,
    _ts,
    col,
)

delivery_app = typer.Typer(no_args_is_help=True)

# Default `delivery list` columns, as dot-paths into a delivery record. COMPLETED
# opts into seconds formatting with fmt=_ts.
_DELIVERY_COLUMNS = [
    col("DELIVERY ID:id"),
    col("STATE:state"),
    col("CADENCE:delivery"),
    col("METHOD:method"),
    col("HTTP:http_status"),
    col("HOST:target_host"),
    col("ITEMS:item_count"),
    col("ATTEMPT:attempt"),
    col("COMPLETED:completed_at", fmt=_ts),
    col("ERROR:error"),
]


@delivery_app.command("list")
@_handle_api_errors
def list_(
    action_id: str = typer.Option(..., "--action", "-a", help="Action id whose deliveries to list."),
    state: str | None = typer.Option(
        None, "--state", "-s", help=f"Filter by delivery state ({choices(WatchActionDeliveryState)})."
    ),
    after: str | None = typer.Option(None, "--after", help="Cursor (delivery id) to page after."),
    limit: int | None = typer.Option(None, "--limit", "-l", help="Rows per page."),
    columns: str | None = _columns_option(),
    transpose: bool = _transpose_option("delivery"),
    print_columns: bool = _print_columns_option("delivery"),
    jsonl: bool = _jsonl_rows_option("delivery"),
    output: str | None = _list_output_option(paginated=True),
) -> None:
    """One action's outbound webhook calls (one row per attempt), newest first."""
    _check_choice(state, WatchActionDeliveryState)
    _emit_columns_paginated(
        fetch=lambda cursor, lim: app_ctx().api.delivery.list(action_id, state=state, after=cursor, limit=lim),
        after=after,
        limit=limit,
        record_of=lambda d, _: d.model_dump(mode="json"),
        default_columns=_DELIVERY_COLUMNS,
        columns=columns,
        transpose=transpose,
        print_columns=print_columns,
        jsonl=jsonl,
        output=output,
        empty_msg="No deliveries match.",
    )


@delivery_app.command("get")
@_handle_api_errors
def get(
    delivery_id: str = typer.Argument(..., help="Delivery id, from `magpie delivery list`."),
    jsonl: bool = typer.Option(False, "--jsonl", help="Emit the delivery as one JSON object instead of a field table."),
    output: str | None = typer.Option(None, "--output", "-o", help="Write to a file instead of stdout."),
) -> None:
    """Show one delivery in full, including the exact request body that was sent."""
    view = app_ctx().api.delivery.get(delivery_id)
    _emit_detail(render=lambda: _print_delivery_detail(view), json_obj=view.model_dump_json, jsonl=jsonl, output=output)


def _print_delivery_detail(d: WatchActionDeliveryView) -> None:
    fields: list[tuple[str, str]] = [
        ("state", str(d.state)),
        ("cadence", str(d.delivery)),
        ("http", str(d.http_status) if d.http_status is not None else console.EMPTY),
        ("method", str(d.method)),
        ("host", d.target_host or console.EMPTY),
        ("items", str(d.item_count)),
        ("attempt", str(d.attempt)),
        ("completed", console.timestamp(d.completed_at)),
        ("error", d.error or console.EMPTY),
    ]
    _print_detail(f"delivery {d.id}", fields)
    console.log("\nrequest payload:")
    console.log(json.dumps(d.request_payload, indent=2, sort_keys=True))
