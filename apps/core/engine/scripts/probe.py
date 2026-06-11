"""Probe an OpenAI-compatible `/v1` endpoint for the models it serves.

Used by the quickstart to VALIDATE the LLM ("can I reach it?") and offer a model
picker, via `python -m engine.scripts.probe <base_url> [api_key]` (prints the model
ids one per line; no output = unreachable / none). httpx + stdlib only - NO Django,
NO settings - so the quickstart can run it on the host before the stack is up
(and before ENGINE_BASE_URL is set). At runtime the engine itself lists models through
the `openai` client; this standalone probe exists only for that pre-`up` step.

The key is read from the ENGINE_PROBE_KEY env var when no positional one is given,
so a caller (the quickstart) can pass it WITHOUT it landing in process argv, where
`ps`/`/proc/<pid>/cmdline` would expose it to other users. The positional form
stays for convenient manual use.
"""

from __future__ import annotations

import os
import sys

import httpx


def probe_models(base_url: str, api_key: str = "", *, timeout: float = 5.0) -> list[str]:
    """Model ids at `base_url` (`GET /v1/models` -> `data[].id`). [] on any error
    (unreachable, auth, shape drift) - the caller treats empty as 'not validated'."""
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    try:
        resp = httpx.get(f"{base_url.rstrip('/')}/models", headers=headers, timeout=timeout)
        resp.raise_for_status()
        data = resp.json().get("data", [])
    except (httpx.HTTPError, ValueError, AttributeError):
        return []
    return [m["id"] for m in data if isinstance(m, dict) and "id" in m]


def _main(argv: list[str]) -> int:
    if not argv:
        print("usage: python -m engine.scripts.probe <base_url> [api_key]", file=sys.stderr)
        return 2
    # Positional key (manual use) wins; else ENGINE_PROBE_KEY env (the quickstart
    # passes it this way so it never appears in argv / ps output).
    api_key = argv[1] if len(argv) > 1 else os.environ.get("ENGINE_PROBE_KEY", "")
    # stdout IS the contract: the quickstart reads one model id per line (no output
    # = unreachable / none). Newline-delimited so an id can't be mis-split.
    for model in probe_models(argv[0], api_key):
        print(model)
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
