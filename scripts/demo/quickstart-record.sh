#!/bin/sh
#
# Record assets/quickstart.gif with VHS: the real `curl | sh` quickstart, run
# in a throwaway ../demo directory (a sibling of this checkout) and montaged by
# scripts/demo/quickstart.tape. Deliberately NOT a make target: re-recording
# the README asset is a rare, maintainer-only act, not dev loop.
#
# The recording IS a full quickstart (clone, build, seed, score), so a take
# costs its full wall-clock and needs a clean slate. Preconditions enforced
# here, each with its remedy:
# - this checkout's stack and job tickers must be DOWN (the demo stack binds
#   the same host ports): make down-jobs && make down
# - ../demo must not exist (the bootstrap clones into it; a stale take would
#   re-enter an existing checkout and record an update, not an install)
# - the first reachable local /v1 endpoint must list qwen2.5:7b: the tape
#   accepts the quickstart's detected default URL and types that model id, and
#   a typed id missing from the list re-prompts and derails the take
# - vhs on PATH (https://github.com/charmbracelet/vhs ; brew install vhs),
#   plus the quickstart's own tools (git, curl, docker, uv), checked up front
#   so a missing one fails here and not minutes into the take
#
# The demo stack runs as compose project openmagpie-demo (exported below, and
# inherited by the shell VHS records), so its containers and volumes never
# touch this checkout's. After the take it is torn down WITH volumes (a retake
# must hit a fresh DB); the ../demo checkout is left behind for inspection and
# must be removed before the next take.
#
# POSIX sh, like the rest of scripts/ (shellcheck -s sh in pre-commit + CI).
set -eu

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

DEMO_DIR="$ROOT/../demo"
TAPE="$ROOT/scripts/demo/quickstart.tape"
MODEL="qwen2.5:7b"

for tool in vhs git curl docker uv; do
    if ! command -v "$tool" >/dev/null 2>&1; then
        echo "error: $tool not found on PATH (see the header of this script for what the recording needs)" >&2
        exit 1
    fi
done

# Job tickers fire `docker compose exec` against this checkout; mid-take they
# would spray failures into .jobs logs and could touch the daemon. Down first.
for pidf in .jobs/*.pid; do
    [ -f "$pidf" ] || continue
    if kill -0 "$(cat "$pidf")" 2>/dev/null; then
        echo "error: a job ticker is running ($pidf). Stop the dev loop first: make down-jobs && make down" >&2
        exit 1
    fi
done

if [ -n "$(docker compose ps -q)" ]; then
    echo "error: this checkout's stack is up; the demo binds the same host ports. Stop it first: make down-jobs && make down" >&2
    exit 1
fi

# Mirror engine_find_local (scripts/quickstart/_engine.sh): the quickstart will
# pre-fill its URL prompt with the FIRST of these that answers, and the tape
# accepts that default, so THAT endpoint must list the model the tape types.
found=""
for base in http://localhost:11434/v1 http://localhost:8000/v1 http://localhost:1234/v1 http://localhost:8080/v1; do
    if curl -fsS -m 1 "$base/models" >/dev/null 2>&1; then
        found="$base"
        break
    fi
done
if [ -z "$found" ]; then
    echo "error: no local OpenAI-compatible /v1 endpoint answering; the take needs one to score (e.g. ollama serve)." >&2
    exit 1
fi
if ! curl -fsS -m 5 "$found/models" | grep -qF "$MODEL"; then
    echo "error: $found does not list $MODEL, which the tape types at the model prompt (e.g. ollama pull $MODEL)." >&2
    exit 1
fi

if [ -e "$DEMO_DIR" ]; then
    echo "error: $DEMO_DIR already exists (a previous take). Inspect what you need, then remove it: rm -rf $DEMO_DIR" >&2
    exit 1
fi
mkdir -p "$DEMO_DIR"

# Hard project-name isolation for everything compose does inside the take: the
# demo's containers/volumes are openmagpie-demo_*, never this checkout's.
COMPOSE_PROJECT_NAME=openmagpie-demo
export COMPOSE_PROJECT_NAME

# BuildKit's default tty progress renderer floods the recorded pty with
# high-frequency ANSI redraws during `up --build`, which has crashed vhs's
# connection to its terminal mid-take. The build is hidden behind a jump cut
# anyway, so render it as plain linear output.
BUILDKIT_PROGRESS=plain
export BUILDKIT_PROGRESS

# Teardown runs on EVERY exit, success or derailed take: a left-behind demo
# stack would squat the host ports and fail the next take mid-build (volumes
# included; a retake must hit a fresh DB). The checkout itself stays for
# inspection.
cleanup() {
    if [ -d "$DEMO_DIR/openmagpie" ]; then
        ( cd "$DEMO_DIR/openmagpie" && docker compose down -v >/dev/null 2>&1 ) || true
    fi
}
trap cleanup EXIT

echo "Recording the full quickstart in $DEMO_DIR (clone + build + seed + score; this takes the quickstart's real wall-clock, only the GIF is short)..."
cd "$DEMO_DIR"
# The take runs unattended for 10+ minutes; a Mac idle-sleeping mid-take resets
# vhs's connection to its terminal and kills the recording. caffeinate holds
# the machine awake for exactly the vhs run (macOS-only; elsewhere, keep the
# machine awake yourself).
if command -v caffeinate >/dev/null 2>&1; then
    caffeinate -dimsu vhs "$TAPE"
else
    vhs "$TAPE"
fi
mv quickstart.gif "$ROOT/assets/quickstart.gif"

echo "Wrote assets/quickstart.gif"
echo "Demo checkout left at $DEMO_DIR (stack torn down); rm -rf it before re-recording."
echo "Bring your dev stack back when ready: make up (and make up-jobs if you run tickers)."
