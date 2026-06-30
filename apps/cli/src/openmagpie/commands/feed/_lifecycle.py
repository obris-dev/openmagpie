"""`magpie feed pause | resume`: stop / restart a feed's syncing.

A paused feed keeps its config, sources, and stored items; the server just stops
polling it (the poll pass skips inactive feeds). Imported by the package __init__
for the verb-registration side effect.
"""

from __future__ import annotations

import typer

from ... import console
from ...context import app_ctx
from .._shared import _handle_api_errors
from ._apps import feed_app


@feed_app.command("pause")
@_handle_api_errors
def pause(feed_id: str = typer.Argument(..., help="Feed id to pause.")) -> None:
    """Pause a feed: the server stops polling its sources until you `resume` it.
    Config, sources, and stored items are kept; only the sync stops."""
    detail = app_ctx().api.feed.set_active(feed_id, is_active=False)
    console.success(f"Paused feed {detail.name} ({detail.id}); the server will stop polling its sources.")


@feed_app.command("resume")
@_handle_api_errors
def resume(feed_id: str = typer.Argument(..., help="Feed id to resume.")) -> None:
    """Resume a paused feed: the server polls its sources again on its cadence."""
    detail = app_ctx().api.feed.set_active(feed_id, is_active=True)
    console.success(f"Resumed feed {detail.name} ({detail.id}); polling continues on its cadence.")
