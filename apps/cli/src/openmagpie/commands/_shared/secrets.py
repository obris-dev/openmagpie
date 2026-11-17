"""Reading a secret (a token, an API key) without it ever touching argv."""

from __future__ import annotations

import getpass
import sys

import typer

from ... import console


def _read_secret(prompt: str, *, hint: str | None = None) -> str:
    """The first line of piped stdin, else a hidden prompt (after `hint`,
    when given). Ctrl-C or Ctrl-D at the prompt exits 130, not a traceback."""
    if not sys.stdin.isatty():
        return sys.stdin.readline().strip()
    if hint:
        console.log(hint)
    try:
        return getpass.getpass(f"{prompt}: ").strip()
    except (EOFError, KeyboardInterrupt):
        console.warn("\nCancelled.")
        raise typer.Exit(code=130) from None
