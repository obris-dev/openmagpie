"""`magpie delivery ...`: the outbound webhook-call audit for one action.

Flat, top-level observability noun (not nested under `watch action`). `list` is
the per-attempt call log; `get` is one call in full, including the exact request
body sent. Both scope to an action via `--action` ; `get` takes the delivery's
own id. Only `webhook` actions make HTTP calls, so other kinds list empty.
"""

from __future__ import annotations

import json

import typer

from openmagpie_schema.watch import (
    WatchActionDeliveryListResponse,
    WatchActionDeliveryView,
    WatchActionDeliveryWire,
)
from openmagpie_schema.watch_enums import WatchActionDeliveryState, choices

from .. import console
from ..context import app_ctx
from ._shared import _check_choice, _handle_api_errors, _print_detail, _print_next_page

delivery_app = typer.Typer(no_args_is_help=True)


@delivery_app.command("list")
@_handle_api_errors
def list_(
    action_id: str = typer.Option(..., "--action", "-a", help="Action id whose deliveries to list."),
    state: str | None = typer.Option(
        None, "--state", "-s", help=f"Filter by delivery state ({choices(WatchActionDeliveryState)})."
    ),
    after: str | None = typer.Option(None, "--after", help="Cursor (delivery id) to page after."),
    limit: int | None = typer.Option(None, "--limit", "-l", help="Max rows to show."),
) -> None:
    """One action's outbound webhook calls (one row per attempt), newest first."""
    _check_choice(state, WatchActionDeliveryState)
    _print_deliveries(app_ctx().api.delivery.list(action_id, state=state, after=after, limit=limit))


@delivery_app.command("get")
@_handle_api_errors
def get(
    delivery_id: str = typer.Argument(..., help="Delivery id, from `magpie delivery list`."),
) -> None:
    """Show one delivery in full, including the exact request body that was sent."""
    _print_delivery_detail(app_ctx().api.delivery.get(delivery_id))


def _print_deliveries(resp: WatchActionDeliveryListResponse) -> None:
    if not resp.items:
        console.log("No deliveries match.")
        return
    columns: list[console.Column[WatchActionDeliveryWire]] = [
        console.Column("DELIVERY ID", lambda d: d.id),
        console.Column("STATE", lambda d: str(d.state)),
        console.Column("CADENCE", lambda d: str(d.delivery)),
        console.Column("METHOD", lambda d: str(d.method)),
        console.Column("HTTP", lambda d: str(d.http_status) if d.http_status is not None else "-"),
        console.Column("HOST", lambda d: d.target_host or "-"),
        console.Column("ITEMS", lambda d: str(d.item_count)),
        console.Column("ATTEMPT", lambda d: str(d.attempt)),
        console.Column("COMPLETED", lambda d: console.timestamp(d.completed_at)),
        console.Column("ERROR", lambda d: d.error or "-"),
    ]
    console.table(resp.items, columns)
    _print_next_page(resp.next_cursor)


def _print_delivery_detail(d: WatchActionDeliveryView) -> None:
    fields: list[tuple[str, str]] = [
        ("state", str(d.state)),
        ("cadence", str(d.delivery)),
        ("http", str(d.http_status) if d.http_status is not None else "-"),
        ("method", str(d.method)),
        ("host", d.target_host or "-"),
        ("items", str(d.item_count)),
        ("attempt", str(d.attempt)),
        ("completed", console.timestamp(d.completed_at)),
        ("error", d.error or "-"),
    ]
    _print_detail(f"delivery {d.id}", fields)
    console.log("\nrequest payload:")
    console.log(json.dumps(d.request_payload, indent=2, sort_keys=True))
