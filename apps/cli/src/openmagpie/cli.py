"""Root Typer app + subcommand registration."""

from __future__ import annotations

import typer

from .commands.activity import activity_app
from .commands.auth import auth_app
from .commands.delivery import delivery_app
from .commands.feed import feed_app
from .commands.telemetry import telemetry_app
from .commands.watch import watch_app
from .context import AppContext, bind_app_ctx, unbind_app_ctx

app = typer.Typer(
    name="magpie",
    help="The magpie CLI. Talk to an OpenMagpie server.",
    no_args_is_help=True,
)


@app.callback()
def main(
    ctx: typer.Context,
    server: str | None = typer.Option(
        None,
        "--server",
        "-s",
        help="OpenMagpie server URL override for this invocation.",
    ),
) -> None:
    """Build the shared AppContext (config + resource API) and bind it
    into the ContextVar so every subcommand can pull it via `app_ctx()`.
    """
    obj = AppContext(server_url=server)
    token = bind_app_ctx(obj)
    ctx.call_on_close(obj.close)
    ctx.call_on_close(lambda: unbind_app_ctx(token))


app.add_typer(auth_app, name="auth", help="Sign in / out and inspect identity.")
app.add_typer(
    feed_app,
    name="feed",
    help="Curate + read feeds (the source set watches subscribe to).",
)
app.add_typer(
    watch_app,
    name="watch",
    help="Build + manage watches (a feed subscription + action chain).",
)
app.add_typer(
    activity_app,
    name="activity",
    help="Audit an action's runs: summary / list / one in full.",
)
app.add_typer(
    delivery_app,
    name="delivery",
    help="Audit an action's outbound webhook calls.",
)
app.add_typer(
    telemetry_app,
    name="telemetry",
    help="Read or set this instance's anonymous-telemetry mode.",
)
