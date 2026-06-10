#!/bin/sh
#
# Seed an example feed + watch into the local dev account, then run one pipeline
# pass if Ollama is reachable so the first matches show up. Used by the
# quickstart (scripts/quickstart/run.sh) and runnable on its own to try another
# starter: STARTER=devtools DAYS=7 ./scripts/quickstart/seed.sh
#
# POSIX sh (part of the portable quickstart trio).
# Env: STARTER (default selfhosted-opensource), DAYS (default 3),
#      SKIP_DATA_SEED=1 (create the account only, no example feed/watch).
set -eu

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

# Shared helpers (manage(), is_truthy()). Sourced after cd so the path resolves.
# shellcheck source=scripts/quickstart/_lib.sh
. ./scripts/quickstart/_lib.sh

STARTER="${STARTER:-selfhosted-opensource}"
DAYS="${DAYS:-3}"

# Account-only mode: no example feed/watch, so nothing to tick. Stop after the
# account exists so the user can explore an empty workspace. is_truthy (from
# _lib.sh) skips on 1/true/yes/on; false/0/empty/anything-else SEEDS, so an
# ambiguous value fails safe toward HAVING data instead of silently skipping it.
if is_truthy "${SKIP_DATA_SEED:-}"; then
    manage seed_quickstart --skip-data
    echo "Account ready; skipped the example feed + watch. Explore the empty workspace, or create your own feed + watch."
    exit 0
fi

manage seed_quickstart --starter="$STARTER" --days="$DAYS"

# Tick only when Ollama answers; otherwise the semantic_filter has nothing to
# call. The probe is stdlib-only, so it needs no package env.
if docker compose exec -T core python -c "import os,urllib.request; urllib.request.urlopen(os.environ.get('OLLAMA_URL','')+'/api/tags', timeout=3)" >/dev/null 2>&1; then
    echo "Ollama reachable. Scoring your backlog now: the semantic filter calls your LLM once per post, so this can take a minute. Progress, an ETA, and any matches stream below as they happen."
    # poll -> trigger -> drain -> digest. The bundled starters deliver via `log`
    # (instant), so the digest flush is a no-op for them, but running it means a
    # custom starter that adds a webhook + digest delivery still gets flushed.
    # send_outbound_emails is skipped on purpose: email is a hosted-only concern,
    # not part of a local trial.
    if manage poll_due_feeds && manage process_due_watches && manage process_due_runs && manage process_due_digests; then
        tick_msg="Tick done. Posts that cleared the threshold printed above, tagged with the starter's prefix (e.g. [oss starter]); a backlog can also score zero on the first pass."
        # The quickstart (run.sh) prints its own consolidated next-steps with the
        # breakdown command + login, so it sets OPENMAGPIE_QUICKSTART to suppress
        # this hint and avoid saying it twice; a standalone re-seed still shows it.
        if [ -z "${OPENMAGPIE_QUICKSTART:-}" ]; then
            # No pipefail under POSIX sh, so a print-activity hiccup just yields an
            # empty aid (tail still succeeds) and we fall back to the generic hint;
            # the `|| aid=""` is a harmless guard for any other capture failure.
            aid="$(manage seed_quickstart --print-activity --starter="$STARTER" 2>/dev/null | tr -d '\r' | tail -1)" || aid=""
            if [ -n "$aid" ]; then
                tick_msg="$tick_msg See the matched vs gated breakdown: magpie watch action activity $aid (after magpie auth login)."
            else
                tick_msg="$tick_msg Re-check anytime: magpie watch action activity <action_id> (ids are in the seed summary above)."
            fi
        fi
        echo "$tick_msg"
    else
        echo "Seeded, but a pipeline stage exited with an error (see the output above). Fix what it reports, then re-run: ./scripts/quickstart/seed.sh"
    fi
else
    echo "Seeded, but no Ollama reached. Start it (and check OLLAMA_URL in apps/core/.env points at it), then re-run: ./scripts/quickstart/seed.sh"
fi
