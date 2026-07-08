"""`magpie upgrade`: update the magpie CLI itself to the latest published release.

Self-contained: no make, no repo, no running server. It checks PyPI for the
latest `openmagpie` release and, if this CLI is behind, upgrades in place via
whatever toolchain installed it (uv tool / pipx). To upgrade the self-hosted
SERVER instead, use `make upgrade` (scripts/upgrade.sh) from your checkout.
"""

from __future__ import annotations

import shutil
import subprocess
import sys

import typer

from .. import __version__, console
from ..update_check import record
from ..versions import PKG, as_tuple, latest_version

# Installer ids (single source of truth; not bare "uv"/"pipx"/"pip" literals repeated
# across detect / upgrade-argv / advise).
_UV, _PIPX, _PIP = "uv", "pipx", "pip"


def _detect_manager() -> str | None:
    """Which tool manages this CLI: "uv", "pipx", "pip", or None if we can't tell.
    Detect the ACTUAL installer rather than guess, so we upgrade via the SAME one that
    installed it (blindly running `uv tool install` over a pipx/pip install would leave
    a second `magpie` shadowing the first). uv + pipx are isolated-tool installers,
    checked by grepping their inventories (first match wins; uv is the quickstart's
    toolchain). pip is a plain env install, so it's probed on THIS interpreter: `pip
    show` succeeds only if the package is pip-installed here -- a uv-tool/pipx venv has
    no pip, so this can't false-positive on the isolated installs handled above.
    Anything else (poetry, conda, a broken PATH) stays None -> we advise, never guess."""
    for tool, argv in ((_UV, ["tool", "list"]), (_PIPX, ["list", "--short"])):
        exe = shutil.which(tool)
        if not exe:
            continue
        try:
            listed = subprocess.run([exe, *argv], capture_output=True, text=True, timeout=20, check=False)
        except (OSError, subprocess.SubprocessError):
            continue
        # Match the package as a line's FIRST token, not a bare substring: both tools
        # list one tool per line starting with its name, so this won't match a
        # prefix-sharing sibling (e.g. `openmagpie-extras`) and route to the wrong tool.
        if any(line.split()[:1] == [PKG] for line in listed.stdout.splitlines()):
            return tool
    try:
        shown = subprocess.run(
            [sys.executable, "-m", "pip", "show", PKG], capture_output=True, text=True, timeout=20, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return _PIP if shown.returncode == 0 else None


def _upgrade_argv(manager: str) -> list[str]:
    """The command that upgrades the CLI under `manager`. The uv/pipx `--force`
    re-install is deliberate: it also converts a quickstart local-source install
    (`uv tool install ./apps/cli`) to the published PyPI package. pip runs in THIS
    interpreter (the env `_detect_manager` confirmed the package lives in)."""
    if manager == _UV:
        return ["uv", "tool", "install", "--force", PKG]
    if manager == _PIPX:
        return ["pipx", "install", "--force", PKG]
    return [sys.executable, "-m", "pip", "install", "--upgrade", PKG]  # pip


def upgrade(
    check: bool = typer.Option(False, "--check", help="Only report whether a newer release exists; don't upgrade."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt (required off a TTY)."),
) -> None:
    """Update the magpie CLI to the latest release published on PyPI."""
    current = __version__
    latest = latest_version()
    if latest is None:
        console.error("Couldn't check PyPI for the latest version (offline, or a bad response).")
        raise typer.Exit(code=1)
    record(latest)  # reset the ambient-nudge cache; we just did the lookup it needs

    if as_tuple(latest) <= as_tuple(current):
        console.success(f"magpie {current} is already the latest.")
        return

    console.header(f"A newer magpie is available: {current} -> {latest}")
    manager = _detect_manager()

    if check:
        _print_manual(manager)
        return
    if manager is None:
        console.warn("Couldn't tell how magpie was installed, so it wasn't changed. Upgrade with one of:")
        _print_manual(manager)
        raise typer.Exit(code=1)

    argv = _upgrade_argv(manager)
    if not yes:
        if not sys.stdin.isatty():
            console.warn("Not a TTY: re-run with --yes to upgrade non-interactively.")
            raise typer.Exit(code=1)
        console.log(f"Will run:  {' '.join(argv)}")
        if not typer.confirm(f"Upgrade magpie {current} -> {latest}?"):
            console.warn("Aborted.")
            raise typer.Exit(code=1)

    console.log(f"Upgrading via {manager}...")
    try:
        subprocess.run(argv, check=True)
    except (OSError, subprocess.CalledProcessError) as exc:
        console.error(f"Upgrade failed ({exc}). Run it yourself:  {' '.join(argv)}")
        raise typer.Exit(code=1) from exc
    console.success(f"Upgraded to magpie {latest}. Run `magpie --help` in a fresh shell to confirm.")


def _print_manual(manager: str | None) -> None:
    """Print the upgrade command(s). Highlights the detected manager; lists all when
    unknown, so the user can pick whichever matches their install."""
    options = {
        _UV: f"uv tool install --force {PKG}",
        _PIPX: f"pipx install --force {PKG}",
        _PIP: f"pip install --upgrade {PKG}",
    }
    if manager in options:
        console.log(f"  {options[manager]}")
    else:
        for cmd in options.values():
            console.log(f"  {cmd}")
