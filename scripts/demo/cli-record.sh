#!/bin/sh
#
# Record assets/cli-tour.gif with VHS: the post-quickstart CLI tour (feed ->
# watch -> semantic filter -> matches). Resolves the featured watch's ids from
# the running stack via the magpie CLI, renders cli-tour.tape.tmpl, runs vhs.
# Deliberately NOT a make target: re-recording the README asset is a rare,
# maintainer-only act, not dev loop.
#
# Needs, beyond the quickstart's own requirements:
# - vhs on PATH (https://github.com/charmbracelet/vhs ; brew install vhs)
# - python3 (parses the CLI's --jsonl output)
# - the magpie CLI signed in (magpie auth login)
# - the featured watch seeded AND scored: at least one succeeded run, so the
#   matches table has rows. Fresh stack: ./scripts/quickstart/run.sh, then
#   make local-tick until something matches.
#
# Env: WATCH_NAME - exact name of the watch to feature (default: the
# selfhosted-opensource starter's watch, the one the quickstart seeds).
#
# POSIX sh, like the rest of scripts/ (shellcheck -s sh in pre-commit + CI).
set -eu

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

WATCH_NAME="${WATCH_NAME:-OSS alternative seekers (starter)}"
TMPL=scripts/demo/cli-tour.tape.tmpl

# The CLI installs to ~/.local/bin (scripts/install-local-cli.sh); put it first
# so both this script and the shell VHS records resolve THAT magpie, even when
# another install shadows it on the ambient PATH.
PATH="$HOME/.local/bin:$PATH"
export PATH

for tool in vhs magpie python3; do
    if ! command -v "$tool" >/dev/null 2>&1; then
        echo "error: $tool not found on PATH (see the header of this script for what the recording needs)" >&2
        exit 1
    fi
done

if ! magpie auth status >/dev/null 2>&1; then
    echo "error: the magpie CLI is not signed in. Run: magpie auth login" >&2
    exit 1
fi

# Each id is read from the CLI's --jsonl output (one JSON object per row), not
# the pretty tables, so column layout changes can't break the parse. Each CLI
# call is captured into a variable BEFORE the parse, never piped into it: POSIX
# sh has no pipefail, so in a pipe a dying magpie (server down, token expired)
# would hand python an empty stdin, python would exit 0, and the empty result
# would misread as "not found". As an assignment, set -e stops on the CLI's own
# exit status, with its own error on stderr.
watches_jsonl="$(magpie watch list --jsonl)"
watch_row="$(printf '%s\n' "$watches_jsonl" | python3 -c '
import json, sys
name = sys.argv[1]
rows = [json.loads(line) for line in sys.stdin if line.strip().startswith("{")]
for w in rows:
    if w["name"] == name:
        # "-" marks a feed-less watch (cannot be toured); an empty string
        # would collapse under sh word-splitting, leaving no second field.
        print(w["id"], w["feed_ids"][0] if w["feed_ids"] else "-")
        break
' "$WATCH_NAME")"
if [ -z "$watch_row" ]; then
    echo "error: no watch named '$WATCH_NAME'. Seed the starter (./scripts/quickstart/run.sh) or set WATCH_NAME to one of yours (magpie watch list)." >&2
    exit 1
fi
# Two space-separated ULIDs; word-splitting them apart is the POSIX idiom.
# shellcheck disable=SC2086
set -- $watch_row
WATCH_ID="$1"
FEED_ID="$2"
if [ "$FEED_ID" = "-" ]; then
    echo "error: watch '$WATCH_NAME' has no feeds, so there is no feed to tour; pick another (magpie watch list)." >&2
    exit 1
fi

actions_jsonl="$(magpie watch action list --watch "$WATCH_ID" --jsonl)"
ACTION_ID="$(printf '%s\n' "$actions_jsonl" | python3 -c '
import json, sys
rows = [json.loads(line) for line in sys.stdin if line.strip().startswith("{")]
for a in rows:
    if a["kind"] == "semantic_filter":
        print(a["id"])
        break
')"
if [ -z "$ACTION_ID" ]; then
    echo "error: watch '$WATCH_NAME' has no semantic_filter action; nothing to demo." >&2
    exit 1
fi

# One fetch covers two needs: the first row is the run featured by `activity
# get`, and a FULL page means the tape's `--limit 5` list will carry a next
# cursor, so its TTY pager prompt WILL appear and the tape must answer it (the
# __PAGER__ marker; see the template header). Full page, not "a 6th row
# exists": the server sets the cursor whenever len(rows) == limit (see
# views_audit.py), so exactly 5 succeeded runs still prompts. Same --limit 5
# as the tape's command, so the full-page test mirrors the page it predicts.
runs_jsonl="$(magpie activity list --action "$ACTION_ID" --state succeeded --limit 5 --jsonl)"
run_row="$(printf '%s\n' "$runs_jsonl" | python3 -c '
import json, sys
rows = [json.loads(line) for line in sys.stdin if line.strip().startswith("{")]
if rows:
    print(rows[0]["run"]["id"], 1 if len(rows) == 5 else 0)
')"
if [ -z "$run_row" ]; then
    echo "error: no succeeded runs for action $ACTION_ID yet, so the matches table would be empty. Score the backlog first: make local-tick" >&2
    exit 1
fi
# shellcheck disable=SC2086
set -- $run_row
RUN_ID="$1"
HAS_NEXT_PAGE="$2"

echo "Recording with watch '$WATCH_NAME' (watch=$WATCH_ID feed=$FEED_ID action=$ACTION_ID run=$RUN_ID)"

# Render to a temp tape. vhs runs from the repo root, where the tape's relative
# Output path lands in assets/. Explicit template, not `mktemp -t`: BSD treats
# -t's operand as a prefix, GNU requires X's in it, so -t can't be both.
tape="$(mktemp "${TMPDIR:-/tmp}/cli-tour-tape.XXXXXXXX")"
trap 'rm -f "$tape"' EXIT
python3 - "$TMPL" "$FEED_ID" "$WATCH_ID" "$ACTION_ID" "$RUN_ID" "$HAS_NEXT_PAGE" >"$tape" <<'PY'
import sys

_, tmpl, feed_id, watch_id, action_id, run_id, has_next = sys.argv
with open(tmpl) as f:
    text = f.read()
for placeholder, value in [
    ("__FEED_ID__", feed_id),
    ("__WATCH_ID__", watch_id),
    ("__ACTION_ID__", action_id),
    ("__RUN_ID__", run_id),
]:
    text = text.replace(placeholder, value)
pager = 'Type "n"\nEnter\nSleep 1s\n' if has_next == "1" else ""
print(text.replace("__PAGER__\n", pager), end="")
PY

vhs "$tape"
echo "Wrote assets/cli-tour.gif"
