"""`magpie version`: show the CLI + connected-server versions, each flagged when a
newer one exists on its own release track.

The CLI and server version INDEPENDENTLY: the CLI is `openmagpie` on PyPI, the
server is the product `v*` release track. They're not meant to match, and
compatibility is enforced on the separate `API_VERSION` axis (a mismatch there
surfaces the "server and CLI are on incompatible versions" error elsewhere). So
this just answers "is each side current on its own track". Only the CLI line
carries a fix (`magpie upgrade`); the server's is informational, since the user may
be pointed at a hosted server they don't upgrade.
"""

from __future__ import annotations

import re

import httpx

from .. import __version__, console
from ..context import app_ctx
from ..http import _VERIFY_TLS
from ..update_check import record
from ..versions import as_tuple, is_behind, latest_version

_REPO = "obris-dev/openmagpie"
_STABLE_TAG = re.compile(r"v(\d+\.\d+\.\d+)$")  # product track; excludes cli-v* + prereleases


def _latest_product_release() -> str | None:
    """Newest STABLE product release (`v<x.y.z>` -> "x.y.z") from GitHub, or None if
    the lookup fails. Filters to the product track by tag shape AND skips a
    GitHub-marked prerelease, like the installer's resolve_ref. The except set is the
    complete one for the `.json()` + `release.get(...)` access (bad JSON -> ValueError;
    a non-list / non-dict body -> TypeError/AttributeError)."""
    try:
        # No verify=_VERIFY_TLS here (unlike the server probe): GitHub/PyPI are public
        # CAs, and MAGPIE_INSECURE_SKIP_TLS_VERIFY is only for the user's own
        # (self-signed) server, never for skipping verification on package metadata.
        resp = httpx.get(
            f"https://api.github.com/repos/{_REPO}/releases",
            params={"per_page": 100},
            timeout=10.0,
            follow_redirects=True,
        )
        resp.raise_for_status()
        best: str | None = None
        for release in resp.json():
            if release.get("prerelease"):
                continue
            match = _STABLE_TAG.fullmatch(str(release.get("tag_name", "")))
            # Track the semver-MAX, not the first (GitHub orders by publish date), so
            # this agrees with scripts/upgrade.sh's `sort`-based pick even if a patch
            # to an older minor is published after a newer release.
            if match and (best is None or as_tuple(match.group(1)) > as_tuple(best)):
                best = match.group(1)
        return best
    except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError):
        return None


def _server_version() -> str | None:
    """The connected server's product version via its unauthenticated `/healthz`, or
    None if the server is unreachable / doesn't report one (an older server). Reads the
    body on ANY HTTP status (no raise_for_status): `/healthz` includes `version` even
    on a 503 by design, so a degraded server still reports it, and only a transport
    failure (httpx.HTTPError) is "unreachable". Honors MAGPIE_INSECURE_SKIP_TLS_VERIFY
    (via _VERIFY_TLS), like every other server call, so a self-signed server isn't
    falsely reported unreachable."""
    base = app_ctx().config.server_url.rstrip("/")
    try:
        resp = httpx.get(f"{base}/healthz", timeout=10.0, follow_redirects=True, verify=_VERIFY_TLS)
        return str(resp.json().get("version") or "") or None
    except (httpx.HTTPError, ValueError, AttributeError):
        return None


def version() -> None:
    """Show the magpie CLI + connected-server versions (and whether either is behind)."""
    # Do all the lookups FIRST (PyPI, the server's /healthz, GitHub), THEN render both
    # lines. Printing the CLI line before the network calls made the output stall
    # mid-list, which read as choppy.
    cli_latest = latest_version()  # None if the PyPI check fails (offline / bad shape)
    record(cli_latest)  # reset the ambient-nudge cache; we just did the lookup it needs
    server = _server_version()
    server_latest = _latest_product_release() if server is not None else None

    cli_behind = is_behind(__version__, cli_latest)
    cli_suffix = f"  ({cli_behind} available)  -> magpie upgrade" if cli_behind else ""
    console.log(f"{'CLI':<8}{__version__}{cli_suffix}")

    if server is None:
        console.log(f"{'Server':<8}(unreachable, or a server without a version)")
        return
    server_behind = is_behind(server, server_latest)
    server_suffix = f"  ({server_behind} available)" if server_behind else ""
    console.log(f"{'Server':<8}{server}{server_suffix}")
