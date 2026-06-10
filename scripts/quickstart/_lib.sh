#!/bin/sh
#
# Shared helpers for the quickstart scripts, SOURCED (not executed) by
# scripts/quickstart/{run,seed}.sh so the shared logic lives in one place rather
# than duplicated with a keep-in-sync comment. Just function definitions, no side
# effects (no set -eu / cd) so the sourcing script stays in control.
#
# POSIX sh, like the rest of scripts/ (shellcheck -s sh in pre-commit + CI).

# Run a Django management command in the core container.
manage() { docker compose exec -T core uv run --package openmagpie-core python apps/core/manage.py "$@"; }

# True (exit 0) when $1 is a truthy boolean string: 1/true/yes/on,
# case-insensitive. Everything else (false/0/empty/unset/garbage) is false, so an
# ambiguous value fails safe. SKIP_DATA_SEED is parsed through this in both
# run.sh and seed.sh.
is_truthy() {
    case "$(printf '%s' "${1:-}" | tr '[:upper:]' '[:lower:]')" in
        1 | true | yes | on) return 0 ;;
        *) return 1 ;;
    esac
}
