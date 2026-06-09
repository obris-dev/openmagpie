#!/bin/sh
#
# Checks tracked text files for trailing whitespace and missing final newlines.
# Uses git to find tracked text files — no hardcoded extensions, binaries skipped.
#
# Usage:
#   ./scripts/check-whitespace.sh          # check only
#   ./scripts/check-whitespace.sh --fix    # fix all issues in-place

set -eu

cd "$(git rev-parse --show-toplevel)"

fix=false
[ "${1:-}" = "--fix" ] && fix=true

files=$(git grep -Il '' || true)
[ -z "$files" ] && exit 0

errors=0

# Clean up the in-place-edit temp file even if sed/cat fails under set -e
# (tmp="" so the trap is a harmless no-op when --fix never runs).
tmp=""
trap 'rm -f "$tmp"' EXIT

# --- Trailing whitespace ---

trailing=$(echo "$files" | xargs grep -ln '[[:blank:]]$' /dev/null 2>/dev/null || true)

if [ -n "$trailing" ]; then
    echo "Trailing whitespace:"
    for f in $trailing; do
        echo "  $f"
        if $fix; then
            # Portable in-place edit: `sed -i` differs between BSD and GNU, so
            # strip into a temp file and cat it back into $f. cat (not mv) keeps
            # $f's mode/owner, e.g. the +x on tracked .sh files. The mktemp
            # template (vs bare `mktemp`) works on both BSD and GNU.
            tmp=$(mktemp "${TMPDIR:-/tmp}/openmagpie-ws.XXXXXX")
            sed 's/[[:blank:]]*$//' "$f" > "$tmp"
            cat "$tmp" > "$f"
            rm -f "$tmp"
        fi
    done
    if $fix; then
        echo "  Fixed."
    else
        errors=1
    fi
else
    echo "Trailing whitespace: OK"
fi

# --- Missing final newline ---

missing=""
for f in $files; do
    if [ -s "$f" ] && [ "$(tail -c1 "$f")" != "" ]; then
        missing="$missing $f"
    fi
done

if [ -n "$missing" ]; then
    echo "Missing final newline:"
    for f in $missing; do
        echo "  $f"
        if $fix; then
            echo "" >> "$f"
        fi
    done
    if $fix; then
        echo "  Fixed."
    else
        errors=1
    fi
else
    echo "Missing final newline: OK"
fi

exit $errors
