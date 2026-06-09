#!/bin/sh
# Parses ## comments from Makefiles and prints grouped help output.
# Usage: ./scripts/make-help.sh Makefile make/local.mk ...

PURPLE='\033[35m'
RESET='\033[0m'

entries=$(grep -h '^[a-zA-Z0-9_-]*[: ].*##' "$@" | sed 's/[: ].*## /,/' | sort)

# Print core commands (no dashes) first
echo "$entries" | while IFS=, read -r target desc; do
    case "$target" in
        *-*) ;;
        *)   printf "  ${PURPLE}%-20s${RESET} %s\n" "$target" "$desc" ;;
    esac
done

# Print remaining commands, grouped by prefix with blank line separators
prev_group=""
echo "$entries" | while IFS=, read -r target desc; do
    case "$target" in
        *-*) ;;
        *)   continue ;;
    esac

    group="${target%%-*}"
    if [ "$group" != "$prev_group" ]; then
        echo ""
        prev_group="$group"
    fi

    printf "  ${PURPLE}%-20s${RESET} %s\n" "$target" "$desc"
done
