#!/bin/sh
#
# Upgrade an existing OpenMagpie install to a newer release. Advances THIS
# checkout to the latest published product release tag (v<x.y.z>) - or to
# OPENMAGPIE_BRANCH if set (e.g. =main for the bleeding edge) - then rebuilds the
# stack, applies DB migrations, and refreshes the `magpie` CLI.
#
# DATA IS PRESERVED: the rebuild keeps the Postgres volume, migrations are
# additive, and this NEVER re-seeds. It's a lifecycle command, distinct from the
# quickstart (first-time setup) - run it from your checkout:
#   ./scripts/upgrade.sh            (or:  make upgrade)
#
# POSIX sh, like the rest of scripts/ (shellcheck -s sh in pre-commit + CI).
set -eu

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

# _lib.sh gives us manage() (run a Django command in the core container). It's a
# generic helper lib despite living under quickstart/; sourcing it here doesn't
# make upgrade part of the quickstart.
# shellcheck source=/dev/null
. ./scripts/quickstart/_lib.sh

# Small print helpers (bootstrap.sh has its own copies; it runs pre-clone and
# can't source anything, so a shared lib isn't reachable there).
if [ -t 1 ]; then RED="$(printf '\033[0;31m')"; NC="$(printf '\033[0m')"; else RED=''; NC=''; fi
info() { printf '%s\n' "  $1"; }
fail() { printf '\n%s\n\n' "  ${RED}x $1${NC}" >&2; exit 1; }

command -v git >/dev/null 2>&1 || fail "git is required to upgrade."
command -v docker >/dev/null 2>&1 || fail "Docker is required to upgrade."
[ -d .git ] || fail "Not a git checkout. Upgrade advances the repo the quickstart cloned; run this from that directory."

# Refuse to clobber local edits to TRACKED files - a checkout would fail or lose
# them. Untracked files (apps/core/.env, your data) are left untouched.
if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
    fail "You have uncommitted changes to tracked files. Commit or stash them, then re-run."
fi

current_version="$(cat version.txt 2>/dev/null || echo '?')"

info "Fetching releases..."
git fetch --quiet --tags origin || fail "git fetch failed (check your network / remote)."

# Target = OPENMAGPIE_BRANCH if pinned, else the latest STABLE product tag
# (vMAJOR.MINOR.PATCH - excludes the cli-v* track and -rc/-suffix prereleases),
# picked from the now-fetched local tags. Falls back to main. NB: portable numeric
# sort (strip the `v`, sort on the three fields), NOT `sort -V` - `-V` is a GNU/BSD
# extension a bare POSIX sort lacks, and its failure is silent (the pipe's status is
# tail's), which would leave `target` empty and wrongly fall through to `main`.
target="${OPENMAGPIE_BRANCH:-}"
if [ -z "$target" ]; then
    latest="$(git tag --list 'v*' | grep -E '^v[0-9]+\.[0-9]+\.[0-9]+$' | sed 's/^v//' \
        | sort -t. -k1,1n -k2,2n -k3,3n | tail -n1)"
    if [ -n "$latest" ]; then target="v${latest}"; else target="main"; fi
fi

# Resolve the target to a commit and no-op if HEAD is already there. Compare COMMITS,
# not refs: on a branch, `git rev-parse --abbrev-ref HEAD` == the branch name == a
# `main` target, so a name compare would exit before the fast-forward below and the
# OPENMAGPIE_BRANCH=main path would never actually advance.
if git show-ref --verify --quiet "refs/remotes/origin/${target}"; then
    target_sha="$(git rev-parse "origin/${target}")"
else
    target_sha="$(git rev-parse "refs/tags/${target}^{commit}" 2>/dev/null || echo '')"
fi
if [ -n "$target_sha" ] && [ "$(git rev-parse HEAD)" = "$target_sha" ]; then
    info "Already up to date (v${current_version}). Nothing to upgrade."
    exit 0
fi

info "Upgrading v${current_version} -> ${target} ..."
git checkout --quiet "$target" || fail "git checkout ${target} failed."
# A tag is an exact commit (nothing to fast-forward); a branch (e.g. main) needs
# advancing to the fetched remote tip.
if git show-ref --verify --quiet "refs/remotes/origin/${target}"; then
    git merge --ff-only --quiet "origin/${target}" || fail "Could not fast-forward ${target}; resolve manually."
fi

# Rebuild from the new source. The named Postgres volume persists across the
# rebuild, so your data survives. --wait blocks until /healthz is green.
info "Rebuilding the stack..."
docker compose up --build -d --wait

# New migrations + the idempotent OAuth app (mirrors run.sh; --noinput: no stdin).
info "Applying migrations..."
manage migrate --noinput
manage bootstrap_oauth_app || true  # idempotent; keep output visible (parity with run.sh)

# Refresh the `magpie` CLI from the new source (best-effort, like run.sh does).
sh ./scripts/install-local-cli.sh || info "Skipped CLI refresh; the stack is upgraded (re-run scripts/install-local-cli.sh)."

new_version="$(cat version.txt 2>/dev/null || echo '?')"
info "Upgraded to v${new_version}."
