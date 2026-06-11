#!/bin/sh
#
# OpenMagpie local quickstart, runnable straight from a fresh machine:
#
#   curl -fsSL https://openmagpie.ai | sh
#
# Clones the repo and hands off to scripts/quickstart/run.sh (build the stack,
# migrate, install the local CLI, seed an example feed + watch, install git
# hooks). Local development only; OpenMagpie is BYO-LLM, so run.sh points at an
# OpenAI-compatible LLM endpoint you control (Ollama, vLLM, LM Studio, llama.cpp,
# or a hosted API like OpenAI) - it auto-detects a local one or prompts for it.
#
# POSIX sh (no bashisms), like every scripts/*.sh (shellcheck -s sh enforces it
# in pre-commit + CI), so the whole `curl ... | sh` flow is bash-free.
#
# Pre-clone, this checks only for git (needed to fetch the repo). The full
# prerequisites checklist (supported OS, Docker + Compose, uv) runs right after
# the clone, in scripts/quickstart/preflight.sh, so there's one styled checklist
# instead of two. Guide-only: it never installs anything; missing tools get
# install instructions. (make is NOT needed for the quickstart.)
#
# Env overrides. When piping, prefix the `sh`, NOT the `curl` (the assignment
# binds to the command it precedes), e.g.:
#   curl -fsSL https://openmagpie.ai | OPENMAGPIE_BRANCH=feature sh
#   OPENMAGPIE_DIR     where to clone (default: ./openmagpie under the current
#                      directory; prompts when run interactively)
#   OPENMAGPIE_BRANCH  branch or tag to check out (default: main)
#   OPENMAGPIE_SSH=1   clone over SSH instead of HTTPS
# STARTER / DAYS / SKIP_DATA_SEED pass through to the seed as well (inherited
# down to seed.sh), e.g. STARTER=devtools DAYS=7 sh:
#   STARTER / DAYS     which example starter to seed + its backfill window
#   SKIP_DATA_SEED=1   create the account only, no example feed/watch
set -eu

readonly REPO_HTTPS="https://github.com/obris-dev/openmagpie.git"
readonly REPO_SSH="git@github.com:obris-dev/openmagpie.git"
OPENMAGPIE_DIR="${OPENMAGPIE_DIR:-}"  # resolved in resolve_target_dir
OPENMAGPIE_BRANCH="${OPENMAGPIE_BRANCH:-main}"

# Colors only when stdout is a terminal (curl | sh keeps stdout on the tty).
# $(printf ...) rather than $'...' (ANSI-C quoting is a bashism).
if [ -t 1 ]; then
    BOLD=$(printf '\033[1m'); RED=$(printf '\033[0;31m'); GREEN=$(printf '\033[0;32m'); YELLOW=$(printf '\033[1;33m'); NC=$(printf '\033[0m')
else
    BOLD=''; RED=''; GREEN=''; YELLOW=''; NC=''
fi
# printf, not echo: echo mangles a message that starts with -n/-e or has a
# backslash (and matches the printf the colors already use).
info() { printf '%s\n' "  $1"; }
step() { printf '%s\n' "${BOLD}$1${NC}"; }
ok()   { printf '%s\n' "  ${GREEN}+${NC} $1"; }
warn() { printf '%s\n' "  ${YELLOW}!${NC} $1"; }
fail() { printf '\n%s\n\n' "  ${RED}x $1${NC}" >&2; exit 1; }

OS=$(uname -s)  # assign then mark readonly separately (don't mask $()'s status)
readonly OS

resolve_target_dir() {
    # Default to a named subdir of the CURRENT directory (not $HOME, and not
    # cwd itself, which could clone into a non-empty dir). Explicit override via
    # OPENMAGPIE_DIR wins; otherwise prompt when a terminal is attached (true
    # even under curl | sh, via /dev/tty), else use the default silently.
    # (No `local` — undefined in POSIX sh; these names don't clash.)
    default="$(pwd)/openmagpie"
    if [ -z "$OPENMAGPIE_DIR" ]; then
        # Prompt only when someone is plausibly watching: stdout is a terminal
        # (`[ -t 1 ]`, still true under curl|sh since only stdin is the pipe) AND
        # the controlling tty is readable. The `[ -t 1 ]` guard matters because a
        # readable /dev/tty alone (CI with a pty, a detached tmux) would block
        # `read` forever; in those cases fall back to the default silently.
        if [ -t 1 ] && [ -r /dev/tty ]; then
            printf "  Clone into [%s]: " "$default" > /dev/tty
            IFS= read -r reply < /dev/tty || reply=""
            OPENMAGPIE_DIR="${reply:-$default}"
        else
            OPENMAGPIE_DIR="$default"
        fi
    fi
    # Expand a leading ~ (the `${x/#~/}` form is a bashism). Quote the ~ in both
    # the pattern and the strip, or a POSIX sh tilde-expands them and the strip
    # silently no-ops. SC2088 is exactly that quoted-tilde, which here is
    # intentional (we match/strip a LITERAL ~/), so it's disabled.
    # shellcheck disable=SC2088
    case "$OPENMAGPIE_DIR" in
        "~/"*) OPENMAGPIE_DIR="$HOME/${OPENMAGPIE_DIR#"~/"}" ;;
    esac
    case "$OPENMAGPIE_DIR" in
        /*) ;;
        *) OPENMAGPIE_DIR="$(pwd)/$OPENMAGPIE_DIR" ;;  # relative -> absolute, so the path we print is unambiguous
    esac
}

fetch_repo() {
    step "Fetching OpenMagpie"
    # git is the one tool we must have BEFORE the repo (and its preflight.sh)
    # exist, so it's checked here, at its point of use, rather than in the
    # post-clone preflight. The full prerequisites checklist runs after the clone.
    if ! command -v git >/dev/null 2>&1; then
        case "$OS" in
            Darwin) info "Install git: xcode-select --install (or via Homebrew)" ;;
            Linux)  info "Install git, e.g. sudo apt-get install -y git" ;;
            *)      info "Install git: https://git-scm.com/downloads" ;;
        esac
        fail "git is required to fetch OpenMagpie"
    fi
    url="$REPO_HTTPS"  # no `local` (undefined in POSIX sh)
    [ -n "${OPENMAGPIE_SSH:-}" ] && url="$REPO_SSH"

    if [ -d "$OPENMAGPIE_DIR/.git" ]; then
        info "Updating existing checkout at $OPENMAGPIE_DIR"
        git -C "$OPENMAGPIE_DIR" fetch --quiet origin "$OPENMAGPIE_BRANCH" || fail "git fetch failed"
        git -C "$OPENMAGPIE_DIR" checkout --quiet "$OPENMAGPIE_BRANCH" || fail "git checkout $OPENMAGPIE_BRANCH failed"
        git -C "$OPENMAGPIE_DIR" pull --quiet --ff-only origin "$OPENMAGPIE_BRANCH" || warn "could not fast-forward; using the local checkout as-is"
    elif [ -d "$OPENMAGPIE_DIR" ] && [ -n "$(ls -A "$OPENMAGPIE_DIR" 2>/dev/null)" ]; then
        # Non-empty but not a git checkout: the leftover of an interrupted or
        # failed prior attempt. `git clone` would only fail here with a cryptic
        # "already exists and is not empty", so bail with a path out instead. (An
        # absent or empty dir falls through to a clean clone below.) A prior run
        # may have started containers + a pgdata volume under this dir's compose
        # project, so tear those down before removing the dir, or the re-clone
        # silently reattaches the old volume.
        info "$OPENMAGPIE_DIR already exists and isn't a clean OpenMagpie checkout"
        info "(likely a previous attempt that didn't finish). Clean up, then re-run:"
        info "  cd \"$OPENMAGPIE_DIR\" && docker compose down -v   # if a prior run started the stack"
        info "  rm -rf \"$OPENMAGPIE_DIR\"                          # remove the leftover checkout"
        info "Or set OPENMAGPIE_DIR to a fresh path."
        fail "target directory is not empty"
    else
        info "Cloning into $OPENMAGPIE_DIR"
        # Fail loudly on a bad branch rather than silently cloning the default
        # one (so a typo'd OPENMAGPIE_BRANCH doesn't masquerade as success).
        git clone --quiet --branch "$OPENMAGPIE_BRANCH" "$url" "$OPENMAGPIE_DIR" \
            || fail "git clone failed (does branch/tag '$OPENMAGPIE_BRANCH' exist?)"
    fi
    ok "Source at $OPENMAGPIE_DIR"
    echo ""
}

# Wrapped in a function so `curl | sh` reads the whole script before running
# any of it (a truncated download then can't execute a half-script).
main() {
    echo ""
    step "OpenMagpie quickstart"
    info "https://openmagpie.ai"
    echo ""
    resolve_target_dir
    fetch_repo
    cd "$OPENMAGPIE_DIR"
    # Repo's here now: run the full prerequisites checklist from it (one shared
    # implementation with the clone-first path), then tell run.sh it's done so it
    # doesn't repeat the checklist. `sh <script>` (not `./<script>`) so the
    # handoff doesn't depend on the +x bit surviving the clone.
    sh ./scripts/quickstart/preflight.sh bootstrap
    # Tell run.sh the checklist already ran. PID-scoped to our $$: the exec below
    # preserves the PID, so run.sh sees OPENMAGPIE_PREFLIGHTED == its own $$. A
    # value that leaks into some later shell carries a different pid and won't
    # match, so run.sh re-checks instead of trusting a stale ambient var.
    export OPENMAGPIE_PREFLIGHTED=$$
    step "Running the quickstart (build -> migrate -> seed an example)"
    echo ""
    # run.sh resolves its own location from $0 regardless of cwd.
    exec sh ./scripts/quickstart/run.sh
}

main "$@"
