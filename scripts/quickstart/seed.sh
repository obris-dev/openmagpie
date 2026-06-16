#!/bin/sh
#
# Seed the quickstart's feed + watch into the local dev account. Seeding ONLY -
# scoring the backlog (the pipeline tick) is scripts/quickstart/tick.sh, gated on
# the LLM being reachable. Used by the quickstart (scripts/quickstart/run.sh) and
# runnable on its own: DAYS=7 ./scripts/quickstart/seed.sh
#
# POSIX sh (part of the portable quickstart trio).
# Env: DAYS (default 1, the first-tick lookback),
#      SKIP_DATA_SEED=1 (create the account only, no feed/watch).
set -eu

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

# Shared helpers (manage(), is_truthy()). Sourced after cd so the path resolves.
# Don't follow the source: pre-commit lints one file at a time, so _lib.sh isn't
# in the input set; it's linted on its own.
# shellcheck source=/dev/null
. ./scripts/quickstart/_lib.sh

DAYS="${DAYS:-1}"

# Account-only mode: no feed/watch. Stop after the account exists so the user can
# explore an empty workspace. is_truthy (from _lib.sh) skips on 1/true/yes/on;
# false/0/empty/anything-else SEEDS, so an ambiguous value fails safe toward
# HAVING data instead of silently skipping it.
if is_truthy "${SKIP_DATA_SEED:-}"; then
    manage seed_quickstart --skip-data
    echo "Account ready; skipped the feed + watch. Explore the empty workspace, or create your own."
    exit 0
fi

# Personalize the seed when a human is at the keyboard: which subreddits to
# listen to, what to flag (the semantic filter), and how strict (the threshold).
# The template's values are shown in brackets as guidance; pressing Enter sends
# an EMPTY value, which the command treats as "keep the template's" (its loaded
# dicts untouched). That makes the interactive-Enter and non-interactive paths
# (CI, curl|sh with no tty, the demo recording) converge on the same template. A
# typed value overrides. Prompt on /dev/tty, not stdin: under `curl | sh` stdin
# is the pipe (same reason as configure_engine).
SUBREDDITS=""
INSTRUCTIONS=""
THRESHOLD=""
if [ -t 1 ] && [ -r /dev/tty ]; then
    # The template's values, shown in the prompts as `[default]`, read as
    # `key=value` lines so the shell doesn't parse YAML (see
    # seed_quickstart --print-config-defaults). Display only; not the value sent.
    _defaults="$(manage seed_quickstart --print-config-defaults 2>/dev/null | tr -d '\r')"
    _sub_default="$(printf '%s\n' "$_defaults" | sed -n 's/^subreddits=//p')"
    _instr_default="$(printf '%s\n' "$_defaults" | sed -n 's/^instructions=//p')"
    _thr_default="$(printf '%s\n' "$_defaults" | sed -n 's/^threshold=//p')"

    printf '\n%s\n' "Set up your first listener: a feed (subreddits), a filter (what to flag), and how strict."
    printf '  Which subreddits? (comma-separated) [%s]: ' "$_sub_default" > /dev/tty
    IFS= read -r SUBREDDITS < /dev/tty || SUBREDDITS=""  # empty (Enter) keeps the template's

    printf '  What should it flag? (plain language) [%s]: ' "$_instr_default" > /dev/tty
    IFS= read -r INSTRUCTIONS < /dev/tty || INSTRUCTIONS=""

    printf '  Match threshold, 0 to 1 (0 keeps everything, 1 only exact matches; higher = stricter) [%s]: ' "$_thr_default" > /dev/tty
    IFS= read -r THRESHOLD < /dev/tty || THRESHOLD=""
fi

manage seed_quickstart --days="$DAYS" --subreddits="$SUBREDDITS" --instructions="$INSTRUCTIONS" --threshold="$THRESHOLD"
