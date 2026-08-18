"""Camofox-based Facebook client, unofficial route.

Spawns the facebook-worker.py shim (sibling checkout
REPOS/facebook-camofox-client/scripts/facebook-worker.py) as a
subprocess with a JSON stdin/stdout contract, mirroring how the
twitter connector wraps twikit. The worker drives one Camofox
(anti-detect Firefox) session per run, injects the account's cookies as
storage state, opens the target Facebook group surface, runs the auth
guard, extracts posts, and returns normalized records + refreshed
cookies.

Cookies resolution follows the twitter connector's env/file pattern:

- FACEBOOK_COOKIES_JSON (a full JSON list/dict of facebook.com cookies),
- FACEBOOK_COOKIES_FILE (path to a cookies JSON file),
- FACEBOOK_CREDENTIALS_DIR (dir of *.json cookie exports, each with an
  optional *.proxy pin), default credentials/ relative to the core app.

Proxy: FACEBOOK_PROXY env (or a per-credential .proxy pin in
FACEBOOK_CREDENTIALS_DIR). The worker forwards it to Camofox.

The connector's poll is a sync iterator, so the subprocess is a
blocking subprocess.run (one bounded run per poll cycle; the worker
launches and tears down its own browser session).
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from .errors import FacebookError, map_worker_error

log = logging.getLogger("sources.facebook")


def _load_cookie_file(path: Path) -> list[dict[str, Any]]:
    """Parse a cookies JSON export (array of cookie objects) or a plain dict."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        return [{"name": str(k), "value": str(v), "domain": ".facebook.com", "path": "/"} for k, v in data.items() if v]
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict) and item.get("name") and item.get("value")]
    raise ValueError(f"{path}: JSON must be an object or an array of cookie objects")


def load_cookies(
    *,
    cookies_json: str | None = None,
    cookies_file: str | None = None,
    credentials_dir: str | None = None,
) -> tuple[list[dict[str, Any]], str | None]:
    """Resolve the cookie list + proxy for one Facebook session, in priority order.

    Returns ``(cookies, proxy)``. ``cookies`` is empty when nothing is
    configured (the worker stays guest-mode; the first open then hits the
    login surface and the auth guard returns ``auth_required``, which the
    poll loop maps). ``proxy`` comes from `FACEBOOK_PROXY` or a
    `<name>.proxy` pin next to the chosen cookie export.
    """
    proxy = os.environ.get("FACEBOOK_PROXY", "").strip() or None

    if cookies_json:
        try:
            data = json.loads(cookies_json)
        except json.JSONDecodeError:
            log.warning("FACEBOOK_COOKIES_JSON is not valid JSON; ignoring")
        else:
            if isinstance(data, dict) and data:
                out = [{"name": str(k), "value": str(v), "domain": ".facebook.com", "path": "/"} for k, v in data.items() if v]
                return out, proxy
            if isinstance(data, list) and data:
                out = [item for item in data if isinstance(item, dict) and item.get("name") and item.get("value")]
                return out, proxy
            log.warning("FACEBOOK_COOKIES_JSON is not a non-empty object/array; ignoring")

    if cookies_file and Path(cookies_file).exists():
        try:
            return _load_cookie_file(Path(cookies_file)), proxy
        except (json.JSONDecodeError, ValueError, OSError) as exc:
            log.warning("FACEBOOK_COOKIES_FILE %s unreadable (%s); ignoring", cookies_file, exc)

    directory = Path(credentials_dir or os.environ.get("FACEBOOK_CREDENTIALS_DIR", "credentials"))
    if directory.exists():
        for path in sorted(directory.glob("*.json")):
            try:
                cookies = _load_cookie_file(path)
            except (json.JSONDecodeError, ValueError, OSError) as exc:
                log.warning("skipping %s: %s", path.name, exc)
                continue
            if not {"c_user", "xs"}.issubset({c["name"] for c in cookies}):
                log.warning("skipping %s: missing c_user/xs (critical pair)", path.name)
                continue
            proxy_path = path.with_suffix(".proxy")
            pin = proxy_path.read_text().strip() if proxy_path.exists() else proxy
            return cookies, pin
        log.warning("credentials dir %s: no usable cookie set found", directory)
    elif credentials_dir:
        log.warning("credentials dir %s not found; no sessions loaded", credentials_dir)

    return [], proxy


def _resolve_worker_path() -> Path:
    """Locate facebook-worker.py: env override, then sibling-checkout, then repo-relative."""
    env_path = os.environ.get("FACEBOOK_WORKER_PATH", "").strip()
    if env_path and Path(env_path).exists():
        return Path(env_path)

    # The worker lives in the facebook-camofox-client repo checkout (sibling to
    # this repo under REPOS/). Fall back to a few plausible placements.
    candidates = [
        Path("facebook-camofox-client/scripts/facebook-worker.py"),
        Path(__file__).resolve().parent / ".." / ".." / ".." / ".." / ".." / "facebook-camofox-client" / "scripts" / "facebook-worker.py",
        Path(sys.prefix) / "facebook_camofox_client" / "scripts" / "facebook-worker.py",
    ]
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved.exists():
            return resolved
    raise FacebookError(
        code="worker_not_found",
        message="facebook-worker.py not found; set FACEBOOK_WORKER_PATH or clone REPOS/facebook-camofox-client",
        retryable=False,
        action="install the facebook-camofox-client checkout with its worker script",
    )


class FacebookClient:
    """Spawns the facebook-worker.py subprocess for one action per call."""

    def __init__(
        self,
        *,
        cookies: list[dict[str, Any]] | None = None,
        cookies_json: str | None = None,
        cookies_file: str | None = None,
        credentials_dir: str | None = None,
    ) -> None:
        self._cookies: list[dict[str, Any]]
        if cookies is not None:
            self._cookies = list(cookies)
            self.proxy = os.environ.get("FACEBOOK_PROXY", "").strip() or None
        else:
            self._cookies, self.proxy = load_cookies(
                cookies_json=cookies_json,
                cookies_file=cookies_file,
                credentials_dir=credentials_dir,
            )
        self._worker_path = _resolve_worker_path()

    def search_group(
        self,
        group_ids: list[str],
        terms: list[str] | None = None,
        count: int = 20,
    ) -> dict[str, Any]:
        """Run one Facebook group search via the worker subprocess.

        Returns the worker's parsed output dict (``{ok, result, new_cookies}``).
        Raises ``FacebookError`` on a worker-reported failure.
        """
        payload = {
            "account_id": "openmagpie",
            "action": "groups.search",
            "cookies": self._cookies,
            "params": {
                "group_ids": group_ids,
                "terms": terms or [],
                "limit": count,
            },
            "proxy": self.proxy,
        }

        try:
            proc = subprocess.run(
                [sys.executable, str(self._worker_path)],
                input=json.dumps(payload),
                capture_output=True,
                text=True,
                timeout=300,  # Camofox browser launch + surface open can take minutes
            )
        except subprocess.TimeoutExpired as exc:
            log.error("facebook worker timed out after 300s (group_ids=%r)", group_ids)
            raise FacebookError(
                code="worker_timeout",
                message="facebook worker timed out after 300s",
                retryable=True,
                action="retry with backoff; the browser session may hang on login walls",
            ) from exc
        except OSError as exc:
            raise FacebookError(
                code="worker_spawn_failed",
                message=f"could not spawn facebook worker: {exc}",
                retryable=True,
                action="check the python runtime and worker path",
            ) from exc

        if proc.returncode != 0:
            log.error(
                "facebook worker exited %d (group_ids=%r): %s",
                proc.returncode,
                group_ids,
                (proc.stderr or proc.stdout)[-2000:],
            )

        try:
            data = json.loads(proc.stdout or "{}")
        except json.JSONDecodeError as exc:
            log.error("facebook worker returned non-JSON stdout: %s", proc.stdout[-2000:])
            raise FacebookError(
                code="worker_bad_output",
                message="facebook worker returned non-JSON output",
                retryable=True,
                action="retry; log raw worker output",
            ) from exc

        if not data.get("ok"):
            raise map_worker_error(data, {"group_ids": group_ids})
        return data
