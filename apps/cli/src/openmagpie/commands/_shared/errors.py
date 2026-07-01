"""Transport / server error rendering at the command boundary."""

from __future__ import annotations

import functools
from collections.abc import Callable
from typing import Any, NoReturn
from urllib.parse import urlparse

import httpx
import typer
from pydantic import ValidationError

from ... import console
from ...http import ApiError, AuthError

CONTRACT_MISMATCH_MESSAGE = (
    "The server's response didn't match what this CLI expects, so the server and CLI "
    "are on incompatible versions. Update magpie (or point at a matching server)."
)


def _abort_contract_mismatch() -> NoReturn:
    """Print the contract-mismatch message and exit(1). One place for the
    auth identity-parse sites (status / login / device flow) that each catch a
    ValidationError from a response the CLI can't parse against its schema."""
    console.error(CONTRACT_MISMATCH_MESSAGE)
    raise typer.Exit(code=1) from None


def _handle_api_errors[T](fn: Callable[..., T]) -> Callable[..., T]:
    """Translate the transport failure modes into one clean CLI exit, at
    the command boundary. Command bodies just call `ac.api.*` directly -
    no thunks. `typer.Exit` (confirm-aborts, the persistence sanity
    guards) is NOT caught, so it propagates normally. `ApiError` goes
    through `_print_api_error` so 400 field errors and structured
    4xx/5xx details both read legibly."""

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> T:
        try:
            return fn(*args, **kwargs)
        except AuthError:
            console.error("Not authenticated. Run `magpie auth login` first.")
            raise typer.Exit(code=1) from None
        except ApiError as e:
            _print_api_error(e)
            raise typer.Exit(code=1) from None
        except ValidationError:
            # A transport-OK response the CLI can't parse against its own schema:
            # the server + CLI are on incompatible contract versions.
            _abort_contract_mismatch()
        except httpx.HTTPError as e:
            console.error(_unreachable_message(e))
            raise typer.Exit(code=1) from None

    return wrapper


def _unreachable_message(exc: httpx.HTTPError) -> str:
    """One clean 'can't reach the server' line, tailored to where the CLI points:
    a self-hoster gets 'is the server running?', the hosted service gets 'try
    again shortly'. Shared by this command-boundary handler and the login flow."""
    server = _server_url()
    where = f" at {server}" if server else ""
    head = f"Couldn't reach the OpenMagpie server{where} ({type(exc).__name__})."
    if server and _is_hosted(server):
        return f"{head} The hosted service may be temporarily unavailable -- try again shortly."
    return f"{head} If you're self-hosting, check the server is running and the server URL is correct."


# OpenMagpie's hosted service lives under openmagpie.ai; anything else is a
# self-hosted box.
_HOSTED_DOMAIN = "openmagpie.ai"


def _is_hosted(server: str) -> bool:
    """True if the URL points at OpenMagpie's hosted service (vs a self-hosted box)."""
    host = (urlparse(server).hostname or "").lower()
    return host == _HOSTED_DOMAIN or host.endswith(f".{_HOSTED_DOMAIN}")


def _server_url() -> str | None:
    """The CLI's effective server URL, or None if no app context is bound."""
    from ...context import app_ctx

    try:
        return app_ctx().config.server_url
    except RuntimeError:
        return None


def _print_api_error(e: ApiError) -> None:
    """Pretty-print a server-side error.

    On 400 the body is the serializer's flat `{path: [messages]}` shape
    (`{"data": {"streams[0].spec.kind": ["..."]}}`); walk it and print
    one line per leaf. Non-400 (404/409/5xx) carry a structured
    `{"error","detail"}`; surface the `detail`/`error` string only -
    never the whole body, which can carry tokens on other endpoints.
    """
    if e.status == 400 and isinstance(e.body, dict):
        console.error("Validation error:")
        for line in _flatten_errors(e.body):
            console.error(f"  {line}")
        return
    detail = ""
    if isinstance(e.body, dict):
        for key in ("detail", "error"):
            val = e.body.get(key)
            if isinstance(val, str) and val:
                detail = f" {val}"
                break
    console.error(f"Server returned an error (HTTP {e.status}).{detail}")


def _flatten_errors(body: Any, prefix: str = "") -> list[str]:
    """DFS over the error dict; yield `path: message` strings.

    The value at each leaf is typically a list of strings, but the
    server also emits a top-level "detail" key for non-field errors, so
    handle scalars too.
    """
    out: list[str] = []
    if isinstance(body, dict):
        for key, val in body.items():
            child_prefix = f"{prefix}.{key}" if prefix else key
            out.extend(_flatten_errors(val, child_prefix))
    elif isinstance(body, list):
        for i, item in enumerate(body):
            if isinstance(item, (dict, list)):
                out.extend(_flatten_errors(item, f"{prefix}[{i}]"))
            else:
                out.append(f"{prefix or '_'}: {item}")
    else:
        out.append(f"{prefix or '_'}: {body}")
    return out
