#!/bin/sh
#
# Run the OpenMagpie quickstart in a cloned repo, without make: build the stack,
# migrate, seed an example feed + watch, install git hooks. Invoked by the
# bootstrap (scripts/quickstart/bootstrap.sh) right after it clones, and runnable
# on its own from a checkout:  ./scripts/quickstart/run.sh
#
# POSIX sh, like everything on the installer path (the scripts/quickstart/
# preflight.sh and scripts/hooks.sh it calls are sh too), so the whole curl|sh
# flow is bash-free.
set -eu

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

# Helpers, sourced after cd so the relative paths resolve. _lib.sh = generic
# (manage(), is_truthy(), env_*); _engine.sh = the LLM setup (configure_engine +
# its helpers), kept separate so this file stays a readable top-to-bottom flow.
# Don't follow the source: pre-commit lints one file at a time, so the sourced
# files aren't in the input set; each is linted on its own.
# shellcheck source=/dev/null
. ./scripts/quickstart/_lib.sh
# shellcheck source=/dev/null
. ./scripts/quickstart/_engine.sh

# Invoke siblings via `sh <script>` (not `./<script>`) for the same reason the
# bootstrap does: don't depend on the +x bit surviving the clone / a tarball.
#
# Preflight up front: check every tool this run needs and, if one is missing,
# stop with the full list rather than dying mid-build. Skip it ONLY when the
# curl|sh bootstrap already ran it: bootstrap exports OPENMAGPIE_PREFLIGHTED=$$
# and exec's us, and exec preserves the PID, so the value equals our own $$. A
# value leaked into some later shell carries a DIFFERENT pid and won't match, so
# we re-run rather than trust an ambient env var. (Supersedes check-docker.sh on
# the quickstart path; that one still guards `make build`.)
if [ "${OPENMAGPIE_PREFLIGHTED:-}" != "$$" ]; then
    sh ./scripts/quickstart/preflight.sh run
fi

if [ ! -f apps/core/.env ]; then
    cp apps/core/.env.example apps/core/.env
    echo "Created apps/core/.env from .env.example"
fi

# Resolve the LLM endpoint/model/key into apps/core/.env BEFORE `up` so the
# container reads the final config (configure_engine lives in _engine.sh).
configure_engine

# The core-setup one-shot runs createcachetable before core serves (see
# docker-compose.yml), so core's /healthz is green on a fresh DB and `--wait`
# returns with the stack up.
docker compose up --build -d --wait

# The model tables + CLI OAuth app the quickstart needs. Explicit and one-time
# here (not auto-run on every `up`), so the dev loop controls when migrations
# apply via `make local-migrate`. --noinput: stdin is closed under curl|sh.
# (manage() comes from _lib.sh, sourced above.)
manage migrate --noinput
manage bootstrap_oauth_app

# Install the local `magpie` CLI so the next-steps below are copy-paste runnable.
# It's our own small, removable tool and the only way to get `magpie` today, so
# the quickstart installs it (best-effort). Needs uv; if uv is missing the script
# prints how to get it and we carry on (the stack is already up). Not make.
sh ./scripts/install-local-cli.sh || true

# Hooks are a best-effort nicety; a pre-commit failure must not abort the
# quickstart after the stack is already up.
sh ./scripts/hooks.sh || true

# Seed the example, then score it LAST among the work so any matches stream right
# before the summary. Split: seed.sh only seeds; tick.sh only ticks (gated on the
# LLM being reachable via `manage.py engine_status`). OPENMAGPIE_QUICKSTART tells
# tick.sh to defer its activity hint to the consolidated summary below (so it
# isn't printed twice). Account-only mode (SKIP_DATA_SEED) has nothing to score.
OPENMAGPIE_QUICKSTART=1 sh ./scripts/quickstart/seed.sh
if ! is_truthy "${SKIP_DATA_SEED:-}"; then
    OPENMAGPIE_QUICKSTART=1 sh ./scripts/quickstart/tick.sh
fi

# Offer anonymous, opt-in telemetry (stays off unless accepted). Only prompt with
# a real terminal attached: under curl|sh stdin is the pipe, so read from /dev/tty
# (same guard as configure_engine). Piped / CI / headless -> skip, stays off.
if [ -t 1 ] && [ -r /dev/tty ]; then
    printf '\n%s\n' "Help improve OpenMagpie? Share ANONYMOUS usage so we can prioritize what to build."
    printf '%s\n' "  It's anonymous: never your content or any personal data, and off until you opt in."
    printf '%s\n' "  What it collects + how to disable: apps/core/TELEMETRY.md"
    printf '  Enable anonymous telemetry? [y/N]: ' > /dev/tty
    IFS= read -r _tel < /dev/tty || _tel=""
    case "$_tel" in
        [Yy]*)
            if manage telemetry enable >/dev/null 2>&1; then
                printf '%s\n' "  Thanks! Anonymous telemetry is on (turn it off any time; see apps/core/TELEMETRY.md)."
            else
                printf '%s\n' "  (couldn't enable telemetry; continuing anyway)"
            fi
            ;;
    esac
fi

# Quickstart funnel completion. Emitted unconditionally; a no-op unless the
# operator opted into telemetry just above (capture gates on mode).
manage emit_quickstart_completed >/dev/null 2>&1 || true

# Final summary, printed LAST so the CLI commands + login don't scroll off behind
# the build/seed output. creds use the same env + defaults as the seed (source of
# truth: apps/core/feeds/management/commands/seed_quickstart.py).
# Normalize the email like the seed does (it stores .strip().lower()), so a
# custom SEED_EMAIL prints the value you'd actually log in with. Password is
# kept verbatim (case-sensitive).
seed_email="$(printf '%s' "${SEED_EMAIL:-local@openmagpie.local}" | tr '[:upper:]' '[:lower:]' | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
seed_password="${SEED_PASSWORD:-openmagpie-local}"
# Color the copy-paste bits (CLI commands + login creds) when stdout is a
# terminal; headers/labels stay plain. printf for the escapes (echo mangles them).
if [ -t 1 ]; then
    KEY=$(printf '\033[1;36m'); GREEN=$(printf '\033[1;32m'); OFF=$(printf '\033[0m')
else
    KEY=''; GREEN=''; OFF=''
fi

# Next step depends on whether example data was seeded. Account-only mode
# (SKIP_DATA_SEED truthy, via is_truthy from _lib.sh) has no watch to inspect, so
# point at creating one instead.
if is_truthy "${SKIP_DATA_SEED:-}"; then
    next_block="Create your first feed and watch with the magpie CLI:
  ${KEY}magpie auth login${OFF}
  ${KEY}magpie feed create${OFF}
  ${KEY}magpie watch create${OFF}"
else
    # The seeded watch's filter-action id, for the inspect commands below.
    # Read-only, no side effects.
    aid="$(manage seed_quickstart --print-activity 2>/dev/null | tr -d '\r' | tail -1)" || aid=""
    [ -n "$aid" ] || aid="<action_id>"
    next_block="See the filter, then what it matched, with the magpie CLI:
  ${KEY}magpie auth login${OFF}
  ${KEY}magpie watch action get ${aid}${OFF}
  ${KEY}magpie activity list --action ${aid}${OFF}

Your feed and watch are saved as editable config (${KEY}config/README.md${OFF} explains them):
  ${KEY}config/quickstart/feed.yaml${OFF}, ${KEY}config/quickstart/watch.yaml${OFF}
  edit them and re-apply with ${KEY}magpie feed/watch edit${OFF}, or copy them into a new listener with ${KEY}create -f${OFF}."
fi

cat <<EOF

${GREEN}Your local OpenMagpie is ready.${OFF}

${next_block}

Login using:
  Email:     ${KEY}${seed_email}${OFF}
  Password:  ${KEY}${seed_password}${OFF}

On a headless box (no browser or installing on a remote instance)? Mint a token and sign in with it:
  ${KEY}make local-manage CMD="issue_cli_token --email ${seed_email} --name my-box"${OFF}
  ${KEY}magpie auth login --token${OFF}   # paste the printed token at the prompt

Run the pipeline: ${KEY}make local-tick${OFF} (once) or ${KEY}make up-jobs${OFF} (background).

Learn more:
  ${KEY}examples/README.md${OFF}   more starters to try, and pushing matches to a webhook
  ${KEY}make help${OFF}            every local command
EOF

# The magpie lines above assume the best-effort CLI install landed. A missing uv
# would've stopped at preflight, so the only way here without it is a CLI build
# failure (uv present); point back at the installer then. uv -> ~/.local/bin.
if ! command -v magpie >/dev/null 2>&1 && [ ! -x "$HOME/.local/bin/magpie" ]; then
    printf '%s\n' "(\`magpie\` not found? re-run ./scripts/install-local-cli.sh)"
fi
