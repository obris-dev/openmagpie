#!/bin/sh
#
# Score the seeded backlog: run one pipeline pass (poll -> trigger -> drain ->
# digest), but ONLY if the LLM is reachable - else the semantic_filter has
# nothing to call. Reachability is `manage.py engine_status` (the REAL engine,
# in-container, so it hits the LLM the same way a judge will); on failure we skip
# the tick and print how to point ENGINE_BASE_URL at an LLM and re-run. Run by
# the quickstart (scripts/quickstart/run.sh) after seeding, and runnable on its
# own (like `make local-tick`, but without its email send, which a local trial
# doesn't need).
#
# POSIX sh (part of the portable quickstart trio).
set -eu

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

# Shared helpers (manage()). Sourced after cd so the path resolves.
# shellcheck source=/dev/null
. ./scripts/quickstart/_lib.sh

# The one reachability check (the real engine). A standalone run lets its stderr
# (the specific unreachable_reason + how_to_fix) through; the quickstart keeps its
# output clean and relies on the recovery message below. Either way, a failure
# skips the tick: the example stays seeded, just unscored until an LLM is set.
if [ -n "${OPENMAGPIE_QUICKSTART:-}" ]; then
    manage engine_status >/dev/null 2>&1 && _reachable=1 || _reachable=0
else
    manage engine_status >/dev/null && _reachable=1 || _reachable=0
fi
if [ "$_reachable" = 0 ]; then
    echo "No OpenAI-compatible LLM found at the configured ENGINE_BASE_URL, so the example wasn't scored (everything else is seeded and ready). To score it: point ENGINE_BASE_URL (the /v1 base URL) and ENGINE_API_KEY (if your LLM needs one) at your LLM in apps/core/.env. For a host LLM use host.docker.internal, not localhost (ENGINE_BASE_URL is read inside the container). Then reload + tick: docker compose down && docker compose up -d --wait, then make local-tick."
    exit 0
fi

echo "LLM reachable. Scoring your backlog now: the semantic filter calls your LLM once per post, so this can take a minute. Progress, an ETA, and any matches stream below as they happen."
# poll -> trigger -> drain -> digest (the scoring stages of `make local-tick`,
# without its send_outbound_emails). The seeded watch delivers via `log`
# (instant), so the digest flush is a no-op for it, but running it means a watch
# that adds a webhook + digest delivery still gets flushed. send_outbound_emails
# is skipped on purpose: email is a hosted-only concern, not part of a local trial.
if manage poll_due_feeds && manage process_due_watches && manage process_due_runs && manage process_due_digests; then
    tick_msg="Tick done. Posts that cleared the threshold printed above, tagged with the watch's prefix (e.g. [quickstart]); a backlog can also score zero on the first pass."
    # The quickstart (run.sh) prints its own consolidated next-steps with the
    # activity command + login, so it sets OPENMAGPIE_QUICKSTART to suppress this
    # hint and avoid saying it twice; a standalone tick still shows it.
    if [ -z "${OPENMAGPIE_QUICKSTART:-}" ]; then
        # No pipefail under POSIX sh, so a print-activity hiccup just yields an
        # empty aid (tail still succeeds) and we fall back to the generic hint;
        # the `|| aid=""` is a harmless guard for any other capture failure.
        aid="$(manage seed_quickstart --print-activity 2>/dev/null | tr -d '\r' | tail -1)" || aid=""
        if [ -n "$aid" ]; then
            tick_msg="$tick_msg See the filter: magpie watch action get $aid ; what it matched: magpie activity list --action $aid (after magpie auth login)."
        else
            tick_msg="$tick_msg Re-check anytime: magpie activity list --action <action_id> (ids are in the seed summary above)."
        fi
    fi
    echo "$tick_msg"
else
    echo "A pipeline stage exited with an error (see the output above). Fix what it reports, then re-run: make local-tick"
fi

# Heartbeat after the tick, mirroring `make local-tick` (this script is local-tick
# minus the hosted-only email send). Self-throttled + best-effort: it no-ops unless
# telemetry is opted in AND ~a day has elapsed, so it's safe to run every tick; the
# `|| true` keeps a telemetry hiccup from failing the tick under `set -e`, and the
# emit is silent so it doesn't clutter the quickstart output.
manage emit_telemetry_heartbeat >/dev/null 2>&1 || true
