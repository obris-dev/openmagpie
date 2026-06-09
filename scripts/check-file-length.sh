#!/bin/sh
#
# Fails if any tracked Python file exceeds LIMIT lines.
#
# Why: long files become hard to scan and tend to mix concerns. The
# limit is intentionally aggressive; keep concerns small, split eagerly.
#
# To fix a violation: pull distinct concerns into their own modules.
# When a single concept legitimately spans more, convert the file into
# a package: foo.py -> foo/__init__.py + foo/<sub_concept>.py. Each
# module stays focused on one thing.
#
# Usage:
#   ./scripts/check-file-length.sh
#
# Exits non-zero with a per-file report when over the limit.

set -eu

cd "$(git rev-parse --show-toplevel)"

LIMIT=350

# Files we don't author / aren't worth re-shaping: Django auto-generated
# migrations, the settings tree (declarative config, not logic — splitting
# it for a line count helps nobody), and anything under a `.venv` checkout
# that somehow got tracked. Extend this if other generated/config trees
# show up.
EXEMPT_PATTERN='(^|/)(migrations|\.venv|conf/settings)/'

# POSIX sh has no arrays, so accumulate violations into a newline-separated
# string ($nl is a literal newline). The loop reads from a heredoc-of-command-
# substitution rather than a `< <(...)` process substitution (a bashism) or a
# pipe (which would run the loop in a subshell and lose `violations`).
nl='
'
violations=""

while IFS= read -r file; do
    [ -f "$file" ] || continue
    printf '%s\n' "$file" | grep -Eq "$EXEMPT_PATTERN" && continue
    lines=$(wc -l < "$file" | tr -d '[:space:]')
    if [ "$lines" -gt "$LIMIT" ]; then
        over=$(( lines - LIMIT ))
        violations="$violations  $file: $lines lines (over by $over)$nl"
    fi
done <<EOF
$(git ls-files '*.py')
EOF

if [ -n "$violations" ]; then
    echo "File length: $LIMIT-line limit exceeded"
    echo
    printf '%s' "$violations"
    echo
    echo "To fix: split distinct concerns into their own modules."
    echo "If one concept legitimately spans more, convert the file into a"
    echo "package: foo.py -> foo/__init__.py + foo/<sub_concept>.py. Keep"
    echo "each module focused on one thing."
    exit 1
fi
