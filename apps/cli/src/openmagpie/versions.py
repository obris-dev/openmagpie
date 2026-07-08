"""Version helpers shared across the CLI: parse a version string to a comparable key,
decide whether one version is behind another, and look up the latest published
`openmagpie` release on PyPI.

Lives here (a neutral leaf, like config/console) rather than in a command module so the
three surfaces that reason about versions — `magpie version`, `magpie upgrade`, and the
ambient update-check nudge — share one definition of "latest" and "behind" and can't
drift apart."""

from __future__ import annotations

import httpx

PKG = "openmagpie"  # the PyPI distribution; the executable is `magpie`
_PYPI_JSON = f"https://pypi.org/pypi/{PKG}/json"


def latest_version(timeout: float = 10.0) -> str | None:
    """The newest `openmagpie` version on PyPI, or None if the lookup fails: offline /
    a bad status (httpx.HTTPError), non-JSON body (ValueError), or an unexpected shape
    (KeyError/TypeError if `info.version` is missing or the body isn't a dict). That is
    the complete raise-set for the `resp.json()["info"]["version"]` access, so a
    malformed response degrades gracefully instead of crashing the caller. `timeout` is
    short on the ambient once-a-day check (update_check) so it never stalls a command."""
    try:
        resp = httpx.get(_PYPI_JSON, timeout=timeout, follow_redirects=True)
        resp.raise_for_status()
        return str(resp.json()["info"]["version"])
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        return None


def as_tuple(version: str) -> tuple[int, ...]:
    """Best-effort numeric key for comparing X.Y.Z (leading digits per segment; a
    prerelease suffix like `rc1` is dropped, so 0.8.0rc1 sorts as 0.8.0). Good enough
    to answer "is `latest` newer than what I'm running"."""
    out: list[int] = []
    for seg in version.split(".")[:3]:
        digits = ""
        for ch in seg:
            if not ch.isdigit():
                break
            digits += ch
        out.append(int(digits) if digits else 0)
    return tuple(out)


def is_behind(current: str, latest: str | None) -> str | None:
    """`latest` if it's newer than `current` (so a caller shows "(x available)"), else
    None. An unknown/unparseable `current` never reads as behind."""
    if latest is None or current in ("", "unknown"):
        return None
    return latest if as_tuple(latest) > as_tuple(current) else None
