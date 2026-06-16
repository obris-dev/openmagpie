#!/bin/sh
#
# Enforce the branch-name convention: <type>/<kebab-slug>, where <type> is a
# Conventional-Commits prefix. Used by BOTH the pre-commit hook (validates the
# current branch) and CI (validates a PR's source branch) so the rule lives in
# one place.
#
#   feat/leaf-only-action-cli   fix/poll-lock-lease   ci/branch-naming
#
# `main` is exempt (the default branch carries no type prefix), as are
# release-please's machine-generated release-PR branches (release-please--...),
# which follow the bot's own fixed scheme, not this human convention. A detached
# HEAD (mid-rebase, or CI's detached checkout) is skipped, so this never blocks a
# rebase or a non-PR build.
#
# Usage:
#   ./scripts/check-branch-name.sh [branch-name]
# With no argument it reads the current branch from git.

set -eu

branch="${1:-$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo HEAD)}"

# Detached HEAD (rebase in progress, CI checkout): nothing to validate.
if [ "$branch" = "HEAD" ] || [ -z "$branch" ]; then exit 0; fi
# The default branch is exempt.
if [ "$branch" = "main" ]; then exit 0; fi
# release-please's own release-PR branches are machine-generated
# (release-please--branches--<target>--components--<component>) and don't follow
# the human convention; exempt them like main so the release PRs stay green.
case "$branch" in
    release-please--*) exit 0 ;;
esac

# POSIX sh has no `[[ =~ ]]`, so match the anchored regex with grep -E. grep
# anchors per line, not whole-string like `[[ =~ ]]`, but a git refname can't
# contain a newline (and the only other input is a dev's argv), so it's moot.
# An optional `!` after the type marks a breaking change (Conventional Commits),
# e.g. feat!/drop-legacy-api - it mirrors the `feat!:` commit marker that
# release-please reads to bump the version.
pattern='^(feat|fix|docs|refactor|test|chore|ci|perf|build|style|revert)!?/[a-z0-9][a-z0-9._-]*$'
if printf '%s\n' "$branch" | grep -Eq "$pattern"; then
    exit 0
fi

cat >&2 <<'EOF'
Branch name doesn't match the convention: <type>/<kebab-slug>
  e.g.  feat/leaf-only-action-cli   fix/poll-lock-lease   ci/branch-naming

  type      when to use it
  --------  ------------------------------------------------------------
  feat      a new user-facing capability
  fix       a bug fix
  perf      a performance improvement (behavior unchanged)
  refactor  restructure code; no behavior or API change
  docs      documentation only
  test      add or correct tests only
  ci        CI / workflows / pipeline config
  build     build system, dependencies, packaging
  chore     maintenance / tooling that doesn't touch src behavior
  style     formatting / whitespace only; no logic change
  revert    revert a previous change

  slug : lowercase letters / digits / - . _  (starts alphanumeric)
  break: append `!` to the type for a breaking change, e.g. feat!/drop-legacy-api

Rename the current branch with:  git branch -m <new-name>
EOF
exit 1
