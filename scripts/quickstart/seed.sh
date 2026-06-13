#!/bin/sh
#
# Seed an example feed + watch into the local dev account. Seeding ONLY - scoring
# the backlog (the pipeline tick) is scripts/quickstart/tick.sh, gated on the LLM
# being reachable. Used by the quickstart (scripts/quickstart/run.sh) and runnable
# on its own to try another starter: STARTER=devtools DAYS=7 ./scripts/quickstart/seed.sh
#
# POSIX sh (part of the portable quickstart trio).
# Env: STARTER (default selfhosted-opensource), DAYS (default 1),
#      SKIP_DATA_SEED=1 (create the account only, no example feed/watch).
set -eu

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

# Shared helpers (manage(), is_truthy()). Sourced after cd so the path resolves.
# Don't follow the source: pre-commit lints one file at a time, so _lib.sh isn't
# in the input set; it's linted on its own.
# shellcheck source=/dev/null
. ./scripts/quickstart/_lib.sh

STARTER="${STARTER:-selfhosted-opensource}"
DAYS="${DAYS:-1}"

# Account-only mode: no example feed/watch. Stop after the account exists so the
# user can explore an empty workspace. is_truthy (from
# _lib.sh) skips on 1/true/yes/on; false/0/empty/anything-else SEEDS, so an
# ambiguous value fails safe toward HAVING data instead of silently skipping it.
if is_truthy "${SKIP_DATA_SEED:-}"; then
    manage seed_quickstart --skip-data
    echo "Account ready; skipped the example feed + watch. Explore the empty workspace, or create your own feed + watch."
    exit 0
fi

manage seed_quickstart --starter="$STARTER" --days="$DAYS"
