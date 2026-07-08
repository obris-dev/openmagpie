#!/bin/sh
#
# Install the local `magpie` CLI on your PATH from this checkout (a SNAPSHOT;
# re-run after a `git pull` to refresh it). `make install-local-cli` is the
# dev-loop alias for this, and the quickstart (scripts/quickstart/run.sh) runs
# it for you. Both need uv, not make.
#
# This is the from-SOURCE install (a snapshot of this checkout). A released
# package now exists on PyPI (`openmagpie`), so `magpie upgrade` can replace this
# local build with the published one (it detects which installer manages the CLI
# via `uv tool list`, then reinstalls via it). This script stays the from-source
# path the quickstart uses.
#
# uv (Astral's Python toolchain) builds the CLI wheel. If it's missing we print
# how to install it and exit non-zero, the same way the quickstart guides a
# missing git / Docker, rather than touching your system.
#
# POSIX sh, like the rest of scripts/ (shellcheck -s sh in pre-commit + CI).
set -eu

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

# Opt-in `--force`: overwrite an existing `magpie` executable. The dev-loop
# `make install-local-cli` passes it so re-running is idempotent even when a
# prior install left the executable behind; the quickstart omits it so a
# from-source build never silently clobbers a deliberately-installed released
# `magpie`.
force=""
case "${1:-}" in
    --force) force=1 ;;
    "") ;;
    *) printf '%s\n' "usage: $0 [--force]" >&2; exit 2 ;;
esac

require_uv() {
    command -v uv >/dev/null 2>&1 && return 0
    # uv may already live in its default ~/.local/bin without this shell's PATH
    # knowing yet (a fresh install wires up future shells, not the current one).
    if [ -x "$HOME/.local/bin/uv" ]; then
        export PATH="$HOME/.local/bin:$PATH"
        return 0
    fi
    printf '%s\n' "uv (Astral's Python toolchain) is required to install the magpie CLI." >&2
    printf '%s\n' "  Install it:  curl -LsSf https://astral.sh/uv/install.sh | sh" >&2
    printf '%s\n' "  Docs:        https://docs.astral.sh/uv/getting-started/installation/" >&2
    printf '%s\n' "Then re-run this (or \`make install-local-cli\`)." >&2
    return 1
}

require_uv

# NOT --editable: the CLI depends on the openmagpie-schema workspace package,
# and an editable tool install lets a stale schema copy shadow the live one
# (ModuleNotFoundError on newer submodules). Non-editable builds a CURRENT
# schema wheel. --reinstall rebuilds on every run; --force (opt-in, see above)
# also overwrites an existing `magpie` executable so the dev loop stays idempotent.
if [ -n "$force" ]; then
    uv tool install --reinstall --force ./apps/cli
else
    uv tool install --reinstall ./apps/cli
fi
printf '%s\n' "Installed the local CLI (snapshot of this checkout; re-run after a pull). Try: magpie auth login"
