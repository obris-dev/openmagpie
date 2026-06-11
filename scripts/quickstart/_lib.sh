#!/bin/sh
#
# Generic helpers for the quickstart scripts, SOURCED (not executed) by
# scripts/quickstart/{run,seed,tick}.sh so the shared logic lives in one place
# rather than duplicated with a keep-in-sync comment. Engine (LLM) setup is its
# own file, _engine.sh (sourced only by run.sh). Just function definitions, no
# side effects (no set -eu / cd) so the sourcing script stays in control.
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

# Read the value of KEY=$1 from env file $2 (last uncommented assignment wins);
# empty if absent. NB: KEY is used as a grep REGEX, which is safe only because
# every caller passes a literal ENGINE_* key (no regex metacharacters).
env_get() {
    grep "^$1=" "$2" 2>/dev/null | tail -1 | cut -d= -f2-
}

# Read KEY=$1 from apps/core/.env, falling back to apps/core/.env.example - the
# SINGLE source for the shipped defaults, so the scripts don't re-hardcode (and
# drift from) the default URL/model that already live in .env.example.
env_get_default() {
    _v="$(env_get "$1" apps/core/.env)"
    [ -n "$_v" ] || _v="$(env_get "$1" apps/core/.env.example)"
    printf '%s' "$_v"
}

# Set KEY=$1 to VALUE=$2 in env file $3, via a temp file (atomic mv). Drops any
# existing uncommented assignment and appends the new one; a commented
# "# KEY=..." default line is left in place. Value is written verbatim (no shell
# expansion), so URLs/keys with special chars are safe.
env_set() {
    _es_tmp="$3.tmp.$$"
    # `|| true`: grep -v exits 1 when the file is empty or every line matches,
    # which would abort the caller's `set -e` even though dropping zero/all lines
    # is the correct result here. (KEY is a grep regex; callers pass literal keys.)
    grep -v "^$1=" "$3" > "$_es_tmp" || true
    printf '%s=%s\n' "$1" "$2" >> "$_es_tmp"
    # The mv swaps the inode, so the new file would otherwise inherit the umask
    # default (often 644). .env can hold ENGINE_API_KEY, so lock it owner-only
    # HERE (every write), not just at one caller's tail.
    chmod 600 "$_es_tmp" 2>/dev/null || true
    mv "$_es_tmp" "$3"
}
