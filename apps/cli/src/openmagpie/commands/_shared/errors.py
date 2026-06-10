"""Transport / server error rendering at the command boundary."""

from __future__ import annotations

import functools
from collections.abc import Callable
from typing import Any

import httpx
import typer

from ... import console
from ...http import ApiError, AuthError


def _handle_api_errors[T](fn: Callable[..., T]) -> Callable[..., T]:
    """Translate the transport failure modes into one clean CLI exit, at
    the command boundary. Command bodies just call `ac.api.*` directly —
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
        except httpx.HTTPError as e:
            console.error(f"Couldn't reach the server ({type(e).__name__}).")
            raise typer.Exit(code=1) from None

    return wrapper


def _print_api_error(e: ApiError) -> None:
    """Pretty-print a server-side error.

    On 400 the body is the serializer's flat `{path: [messages]}` shape
    (`{"data": {"streams[0].spec.kind": ["..."]}}`); walk it and print
    one line per leaf. Non-400 (404/409/5xx) carry a structured
    `{"error","detail"}`; surface the `detail`/`error` string only —
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
