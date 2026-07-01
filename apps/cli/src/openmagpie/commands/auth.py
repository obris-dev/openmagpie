"""`magpie auth ...` commands: login (device flow), status, logout."""

from __future__ import annotations

import getpass
import os
import sys
import time
import webbrowser
from urllib.parse import urlparse

import httpx
import typer

from .. import console
from ..api.auth import DeviceSessionCompleted, DeviceSessionExpired
from ..constants import TOKEN_ENV_VAR, is_personal_access_token
from ..context import app_config, app_ctx
from ..http import ApiError, AuthError
from ._shared import _unreachable_message
from .auth_token import token_app
from .telemetry import notice_after_login

auth_app = typer.Typer(no_args_is_help=True)


def _ambient_token_set() -> bool:
    return bool(os.environ.get(TOKEN_ENV_VAR))


POLL_INTERVAL_SECONDS = 2.0
# Small grace window past the server-reported `expires_in` to give the
# server's eviction-driven 404 time to land (so the user sees "Session
# expired" instead of our own generic "timed out").
POLL_DEADLINE_GRACE_SECONDS = 5.0
# Hard ceiling on how long we're willing to sit in the polling loop,
# regardless of what the server reports. A buggy / hostile server
# returning `expires_in: 10_000_000` would otherwise leave a CLI
# process spinning for months. The legit server's pending-session TTL
# is 15 min; 30 min gives us 2x headroom for edge cases without ever
# letting the loop run indefinitely.
MAX_DEVICE_LOGIN_SECONDS = 30 * 60

# Shown when the device flow can't open a browser on THIS machine. Opening the URL
# on another device IS the normal login (you authorize in the web app there), so
# that's the lead; `--token` is the alternative for a headless/automated box that
# can't do the interactive login at all. No "you need a token" implication -- most
# people don't.
_NO_BROWSER_HINT = (
    "Open the URL above on another device to finish, or use `magpie auth login --token` for a headless setup."
)

# Shown above the INTERACTIVE --token prompt: how to obtain a token if you don't
# have one (the prompt itself does the asking). Plain, not a warning -- it's
# neutral guidance, matching the device flow's instruction lines; yellow is for
# actual problems. On-topic here (the user chose the token path).
_TOKEN_PROMPT_HINT = (
    "Don't have a token? Sign in with `magpie auth login`, then `magpie auth token create`,\n"
    "or ask your admin to generate your first one."
)


def _print_signed_in(email: str) -> None:
    console.success(f"Signed in as {email}")


def _safe_authorize_url(authorize_url: str, server_url: str) -> bool:
    """Refuse server-supplied URLs that don't match the configured server.

    A compromised or rogue server could return an `authorize_url`
    pointing anywhere (phishing surface). We require:
      - scheme is http or https (no `javascript:`, `data:`, etc.)
      - hostname matches the configured server's hostname exactly
        (port-agnostic, so localhost:8000 ↔ localhost:3001 works for
        the standard dev split).
    """
    try:
        a = urlparse(authorize_url)
        s = urlparse(server_url)
    except ValueError:
        return False
    if a.scheme not in ("http", "https"):
        return False
    if not a.hostname or not s.hostname:
        return False
    return a.hostname.lower() == s.hostname.lower()


def _read_token_secret() -> str:
    """Get the PAT without ever putting it in argv.

    Piped stdin (`echo $TOK | magpie auth login --token`) -> a hidden
    interactive prompt. Returns "" if neither is available. `MAGPIE_TOKEN`
    is deliberately NOT a source here: when it's set, `login` refuses (it's
    the ambient credential, used directly, no login needed).
    """
    if not sys.stdin.isatty():
        # Piped / redirected: take the first line (no human to hint).
        return sys.stdin.readline().strip()
    # Interactive: tell the operator where a token comes from before prompting.
    console.log(_TOKEN_PROMPT_HINT)
    try:
        return getpass.getpass("Personal access token: ").strip()
    except (EOFError, KeyboardInterrupt):
        # Ctrl-D / Ctrl-C at the prompt: exit cleanly (130 = 128 + SIGINT),
        # matching the device-flow handler, not a raw traceback.
        console.warn("\nCancelled.")
        raise typer.Exit(code=130) from None


def _login_with_token() -> None:
    """Sign in with a personal access token (the headless path)."""
    ac = app_ctx()
    secret = _read_token_secret()
    if not secret:
        console.error("No token provided. Pipe the token on stdin, or run interactively to be prompted.")
        raise typer.Exit(code=1)
    try:
        me = ac.sign_in_with_token(secret)
    except AuthError:
        console.error(
            "That token was rejected. Mint a new one on the server, or "
            "`magpie auth token create` if you're already signed in elsewhere."
        )
        raise typer.Exit(code=1) from None
    except ApiError as e:
        console.error(f"Couldn't reach server at {ac.config.server_url} (HTTP {e.status}).")
        raise typer.Exit(code=1) from None
    except httpx.HTTPError as e:
        console.error(_unreachable_message(e))
        raise typer.Exit(code=1) from None
    _print_signed_in(me.email)
    notice_after_login(ac)


@auth_app.command("login")
def login(
    token: bool = typer.Option(
        False,
        "--token",
        help=(
            "Sign in with a personal access token instead of the browser "
            "device flow. The token is read from piped stdin or a hidden "
            "prompt (never the command line)."
        ),
    ),
    no_browser: bool = typer.Option(
        False,
        "--no-browser",
        help="Don't try to launch a browser; just print the URL.",
    ),
) -> None:
    """Sign in via the browser device-flow handshake (or a token with --token)."""
    if _ambient_token_set():
        # gh-style refusal: an ambient token is used on every request and
        # overrides any stored login, so logging in would be a no-op (and
        # silently ignore piped/prompted input). Make the operator choose.
        console.error(
            f"{TOKEN_ENV_VAR} is set in your environment; it's already used for every "
            f"request and overrides a stored login. Unset it (`unset {TOKEN_ENV_VAR}`) "
            "to log in, or just run magpie as-is."
        )
        raise typer.Exit(code=1)
    if token:
        _login_with_token()
        return

    ac = app_ctx()

    try:
        created = ac.api.auth.create_device_session()
    except ApiError as e:
        console.error(f"Server returned an error (HTTP {e.status}).")
        raise typer.Exit(code=1) from None
    except httpx.HTTPError as e:
        console.error(_unreachable_message(e))
        raise typer.Exit(code=1) from None

    if not _safe_authorize_url(created.authorize_url, ac.config.server_url):
        console.error(
            "The server returned an authorize URL that doesn't match "
            f"the configured server ({ac.config.server_url}). Refusing "
            "to open it. If you trust this server, reconfigure with "
            "--server, then try again."
        )
        raise typer.Exit(code=1)

    console.log(f"Open this URL to authorize: {created.authorize_url}")
    # Cyan+bold for the verification code: it's the one piece of data the
    # operator must read off the terminal and type into the browser, so it
    # gets a bolder treatment than `console.header` (which is cyan, not bold).
    typer.secho(f"Verification code: {created.user_code}", fg=typer.colors.CYAN, bold=True)
    console.log("Enter this code on the authorize page to confirm it's your CLI.")
    if not no_browser:
        opened = False
        try:
            opened = webbrowser.open(created.authorize_url)
        except webbrowser.Error as e:
            console.warn(f"Couldn't launch a browser ({e}). {_NO_BROWSER_HINT}")
        else:
            if not opened:
                console.warn(f"No browser available. {_NO_BROWSER_HINT}")

    console.log("Waiting for authorization...")

    # Server tells us how long the session is valid; we follow that
    # (plus a small grace), but clamp to MAX_DEVICE_LOGIN_SECONDS so a
    # buggy or hostile server returning `expires_in: 10_000_000` can't
    # park this CLI process in a polling loop for months.
    session_seconds = min(created.expires_in, MAX_DEVICE_LOGIN_SECONDS)
    deadline = time.monotonic() + session_seconds + POLL_DEADLINE_GRACE_SECONDS
    transport_warned = False
    try:
        while time.monotonic() < deadline:
            time.sleep(POLL_INTERVAL_SECONDS)
            try:
                poll = ac.api.auth.poll_device_session(created.session_id, device_secret=created.device_secret)
            except ApiError as e:
                if e.status == 404:
                    console.error("Session expired before authorization completed.")
                    raise typer.Exit(code=1) from None
                # Non-404 ApiError: server is reachable but unhappy. No
                # value in retrying; surface the status and exit.
                # `e.body` deliberately not printed, bodies can carry
                # tokens; if the user needs detail, server logs have it.
                console.error(f"Server returned an error while polling (HTTP {e.status}).")
                raise typer.Exit(code=1) from None
            except httpx.HTTPError as e:
                # Transport-level error (connect refused, timeout, DNS,
                # protocol). Treat as transient and keep polling. Warn
                # once so the user knows the CLI isn't hung; subsequent
                # retries stay quiet to avoid flooding the terminal.
                if not transport_warned:
                    console.warn(f"  (transport error: {type(e).__name__}; will keep retrying. Ctrl-C to abort.)")
                    transport_warned = True
                continue

            if isinstance(poll, DeviceSessionCompleted):
                ac.sign_in(poll)
                _print_signed_in(poll.user.email)
                notice_after_login(ac)
                return
            if isinstance(poll, DeviceSessionExpired):
                console.error("Session expired.")
                raise typer.Exit(code=1)
    except KeyboardInterrupt:
        # 130 = 128 + SIGINT, the conventional shell exit code for Ctrl-C.
        # Newline first so the message doesn't ride on the same line as
        # the terminal's "^C" echo.
        console.warn("\nCancelled.")
        raise typer.Exit(code=130) from None

    console.error(f"Timed out waiting for authorization ({session_seconds // 60} min).")
    raise typer.Exit(code=1)


@auth_app.command("status")
def status() -> None:
    """Show the currently signed-in identity."""
    ac = app_ctx()
    ambient = _ambient_token_set()
    # An ambient MAGPIE_TOKEN authenticates even with no stored login, so
    # `is_authenticated` (config-only) isn't the whole picture.
    if not ambient and not ac.config.is_authenticated:
        console.error("Not authenticated.")
        raise typer.Exit(code=1)

    try:
        me = ac.api.auth.me()
    except AuthError:
        if ambient:
            console.error(f"The {TOKEN_ENV_VAR} in your environment was rejected. Check the token, or unset it.")
        else:
            console.error("Stored credentials are no longer valid. Run `magpie auth login` again.")
        raise typer.Exit(code=1) from None
    except ApiError as e:
        console.error(f"Couldn't reach the server cleanly (HTTP {e.status}). Try again or check the server status.")
        raise typer.Exit(code=1) from None
    except httpx.HTTPError as e:
        console.error(_unreachable_message(e))
        raise typer.Exit(code=1) from None

    console.log(f"Signed in as {me.email}")
    console.log(f"Account:     {me.account_id}")  # always present: a user belongs to an account
    console.log(f"Server:      {ac.config.server_url}")
    if ambient:
        console.log(f"Auth:        {TOKEN_ENV_VAR} (environment)")
        if ac.config.is_authenticated:
            # There's also a stored login, but the env token wins every request.
            console.log(f"             (a stored login is shadowed; `unset {TOKEN_ENV_VAR}` to use it)")
    elif is_personal_access_token(ac.config.access_token):
        console.log("Auth:        personal access token")


@auth_app.command("logout")
def logout() -> None:
    """Clear locally stored credentials (revokes a session token server-side)."""
    cfg = app_config()
    # An ambient MAGPIE_TOKEN is managed in the environment, not by us:
    # logout can't clear it, and it keeps authenticating until unset.
    if _ambient_token_set():
        console.warn(
            f"{TOKEN_ENV_VAR} is set in your environment and will keep authenticating you. "
            f"Unset it (`unset {TOKEN_ENV_VAR}`) to log out."
        )
    if not cfg.is_authenticated:
        console.log("No stored login to clear.")
        return
    was_pat = is_personal_access_token(cfg.access_token)
    revoked_server_side = app_ctx().sign_out()
    if not revoked_server_side:
        console.warn(
            "Couldn't reach server to revoke the token. Local credentials "
            "cleared, but the token may stay valid until it expires naturally."
        )
    if was_pat:
        # A PAT isn't revoked on logout (it's a durable, reusable credential).
        console.success(
            "Local credentials cleared. The personal access token is still "
            "valid; revoke it with `magpie auth token revoke <id>`."
        )
    elif revoked_server_side:
        console.success("Logged out.")
    else:
        # Server revoke failed (warned above); don't contradict it with an
        # unqualified "Logged out."
        console.success("Local credentials cleared.")


auth_app.add_typer(token_app, name="token")
