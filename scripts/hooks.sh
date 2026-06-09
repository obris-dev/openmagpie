#!/bin/sh
#
# Install the git pre-commit hooks (.pre-commit-config.yaml). General dev setup,
# not quickstart-specific: run it standalone via `make hooks`, and the quickstart
# calls it too. POSIX sh because the quickstart (run.sh) calls it, so it's on the
# installer path. Best-effort by design: it needs uv (uvx) and a git checkout, so
# if either is missing (e.g. a curl-only trial with no dev toolchain) it skips
# with a note instead of failing the caller.
set -eu

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if ! command -v uvx >/dev/null 2>&1; then
    echo "Skipping git hooks: uv (uvx) not found. Install uv, then run: make hooks"
    exit 0
fi
if ! git rev-parse --git-dir >/dev/null 2>&1; then
    echo "Skipping git hooks: not a git checkout."
    exit 0
fi
uvx pre-commit install
