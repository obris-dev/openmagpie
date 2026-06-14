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


def _classify(base_url: str, api_key: str, timeout: float) -> tuple[list[str], str | None]:
    """(model ids, reason). reason is None on success, else a ONE-LINE human
    explanation of why no models came back - so the quickstart can tell the user
    WHY ('no models pulled' vs 'unreachable' vs 'wrong path' vs 'auth') instead
    of a blank miss. Every branch returns a reason; the probe must never crash
    the setup, so even an unexpected error is reported, not raised."""
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    try:
        resp = httpx.get(f"{base_url.rstrip('/')}/models", headers=headers, timeout=timeout)
    except (httpx.UnsupportedProtocol, httpx.InvalidURL):
        return [], f"{base_url} is not a valid URL - it must start with http:// or https://"
    except httpx.ConnectError:
        return [], f"could not connect to {base_url} - is the server running and listening on that address?"
    except httpx.TimeoutException:
        return [], f"{base_url} did not respond within {timeout:.0f}s"
    except httpx.HTTPError as exc:
        return [], f"request to {base_url} failed ({type(exc).__name__})"
    except Exception as exc:  # a probe crash must surface as a reason, never abort setup
        return [], f"probing {base_url} hit an unexpected error ({type(exc).__name__})"
    if resp.status_code in (401, 403):
        return (
            [],
            f"{base_url} rejected the request (HTTP {resp.status_code}) - set the API key, or check the one you gave",
        )
    if resp.status_code == 404:
        return [], f"{base_url} has no models endpoint there (HTTP 404) - an OpenAI-compatible base URL ends in /v1"
    if resp.status_code >= 400:
        return [], f"{base_url} returned HTTP {resp.status_code}"
    try:
        data = resp.json().get("data", [])
    except (ValueError, AttributeError):
        return [], f"{base_url} did not return the expected /v1/models JSON"
    models = [m["id"] for m in data if isinstance(m, dict) and "id" in m]
    if not models:
        return [], f"reached {base_url}, but it lists no models - pull one, e.g. `ollama pull qwen2.5:7b`"
    return models, None


def probe_models(base_url: str, api_key: str = "", *, timeout: float = 5.0) -> list[str]:
    """Model ids at `base_url` (`GET /v1/models` -> `data[].id`). [] on any error
    (unreachable, auth, shape drift) - the caller treats empty as 'not validated'.
    Use `_classify` directly when the failure reason is also wanted."""
    return _classify(base_url, api_key, timeout)[0]


def _main(argv: list[str]) -> int:
    if not argv:
        print("usage: python -m engine.scripts.probe <base_url> [api_key]", file=sys.stderr)
        return 2
    # Positional key (manual use) wins; else ENGINE_PROBE_KEY env (the quickstart
    # passes it this way so it never appears in argv / ps output).
    api_key = argv[1] if len(argv) > 1 else os.environ.get("ENGINE_PROBE_KEY", "")
    models, reason = _classify(argv[0], api_key, 5.0)
    # stdout IS the contract: the quickstart reads one model id per line (no output
    # = unreachable / none). Newline-delimited so an id can't be mis-split.
    for model in models:
        print(model)
    # The reason goes to stderr with a `probe:` prefix so the shell can pick it
    # out of any surrounding tooling noise (uv warnings) and show it.
    if reason is not None:
        print(f"probe: {reason}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
