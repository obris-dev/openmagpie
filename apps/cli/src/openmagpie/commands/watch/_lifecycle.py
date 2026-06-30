"""`magpie watch pause | resume`: stop / restart a watch's triggering.

A paused watch keeps its action chain and run history; the trigger pass just stops
running it (it iterates only active watches). Imported by the package __init__ for
the verb-registration side effect.
"""

from __future__ import annotations

import typer

from ... import console
from ...context import app_ctx
from .._shared import _handle_api_errors
from ._apps import watch_app


@watch_app.command("pause")
@_handle_api_errors
def pause(watch_id: str = typer.Argument(..., help="Watch id to pause.")) -> None:
    """Pause a watch: the trigger pass stops running it until you `resume` it.
    The action chain and run history are kept; only triggering stops."""
    detail = app_ctx().api.watch.set_active(watch_id, is_active=False)
    console.success(f"Paused watch {detail.name} ({detail.id}); it won't run until resumed.")


@watch_app.command("resume")
@_handle_api_errors
def resume(watch_id: str = typer.Argument(..., help="Watch id to resume.")) -> None:
    """Resume a paused watch: the trigger pass runs it again on new feed items."""
    detail = app_ctx().api.watch.set_active(watch_id, is_active=True)
    console.success(f"Resumed watch {detail.name} ({detail.id}); it runs again on new items.")
