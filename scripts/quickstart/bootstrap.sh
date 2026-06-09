#!/bin/sh
#
# OpenMagpie local quickstart, runnable straight from a fresh machine:
#
#   curl -fsSL https://openmagpie.ai | sh
#
# Clones the repo and hands off to scripts/quickstart/run.sh (build the stack,
# migrate, seed an example feed + watch, install git hooks). Local development
# only; OpenMagpie is BYO-LLM, so run.sh points at an Ollama you control.
#
# POSIX sh (no bashisms), like the rest of the installer path (run.sh, seed.sh,
# and the scripts/check-docker.sh + scripts/hooks.sh that run.sh calls), so the
# whole `curl ... | sh` flow is bash-free and runs on any box. The remaining
# scripts/*.sh are dev tooling (make / pre-commit / CI only) and stay bash.
#
# Guide-only prerequisites: it checks for git + Docker and, if either is
# missing, prints how to install it and exits. It never modifies your system.
# (make is NOT needed for the quickstart, only for the dev loop afterwards.)
#
# Env overrides. When piping, prefix the `sh`, NOT the `curl` (the assignment
# binds to the command it precedes), e.g.:
#   curl -fsSL https://openmagpie.ai | OPENMAGPIE_BRANCH=feature sh
#   OPENMAGPIE_DIR     where to clone (default: ./openmagpie under the current
#                      directory; prompts when run interactively)
#   OPENMAGPIE_BRANCH  branch or tag to check out (default: main)
#   OPENMAGPIE_SSH=1   clone over SSH instead of HTTPS
# STARTER / DAYS pass through to the seed as well (they're inherited down to
# seed.sh): curl -fsSL https://openmagpie.ai | STARTER=devtools DAYS=7 sh
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

docker_guide() {
    case "$OS" in
        Darwin)
            info "macOS: install Docker Desktop -> https://docs.docker.com/desktop/install/mac-install/"
            info "       (or: brew install --cask docker), then launch it so the daemon runs." ;;
        Linux)
            info "Linux: install Docker Engine + the Compose plugin -> https://docs.docker.com/engine/install/" ;;
        *)
            info "See https://docs.docker.com/get-docker/" ;;
    esac
}

require_cmd() {
    # require_cmd <command> <human label> <macOS hint> <Linux hint>
    command -v "$1" >/dev/null 2>&1 && { ok "$2"; return; }
    case "$OS" in
        Darwin) info "$3" ;;
        Linux)  info "$4" ;;
    esac
    fail "$2 is required"
}

preflight() {
    step "Checking prerequisites"

    case "$OS" in
        Darwin | Linux)
            ok "OS: $OS" ;;
        *)
            info "OpenMagpie's quickstart supports macOS and Linux."
            info "On Windows, run it inside WSL2 (a Linux shell): https://learn.microsoft.com/windows/wsl/install"
            fail "unsupported OS: $OS" ;;
    esac

    require_cmd git "git" \
        "Install git: xcode-select --install (or via Homebrew)" \
        "Install git, e.g. sudo apt-get install -y git"
    # make is NOT required here: the quickstart itself is make-free
    # (scripts/quickstart/run.sh). make is the dev-loop interface afterwards.

    # Same checks (and order) as scripts/check-docker.sh, which run.sh uses
    # post-clone; we can't call it here since the repo isn't cloned yet. Keep
    # the checks in sync (the guidance wording is allowed to differ).
    if ! command -v docker >/dev/null 2>&1; then
        docker_guide
        fail "Docker is required"
    fi
    if ! docker compose version >/dev/null 2>&1; then
        info "Docker is installed but the Compose plugin ('docker compose') is missing."
        docker_guide
        fail "the Docker Compose plugin is required"
    fi
    if ! docker info >/dev/null 2>&1; then
        case "$OS" in
            Darwin) info "Docker is installed but its daemon isn't running. Launch Docker Desktop and wait for it to start." ;;
            Linux)  info "Docker is installed but its daemon isn't running. Start it: sudo systemctl start docker" ;;
        esac
        fail "the Docker daemon is not running"
    fi
    ok "Docker + Compose (daemon running)"
    echo ""
}

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
    url="$REPO_HTTPS"  # no `local` (undefined in POSIX sh)
    [ -n "${OPENMAGPIE_SSH:-}" ] && url="$REPO_SSH"

    if [ -d "$OPENMAGPIE_DIR/.git" ]; then
        info "Updating existing checkout at $OPENMAGPIE_DIR"
        git -C "$OPENMAGPIE_DIR" fetch --quiet origin "$OPENMAGPIE_BRANCH" || fail "git fetch failed"
        git -C "$OPENMAGPIE_DIR" checkout --quiet "$OPENMAGPIE_BRANCH" || fail "git checkout $OPENMAGPIE_BRANCH failed"
        git -C "$OPENMAGPIE_DIR" pull --quiet --ff-only origin "$OPENMAGPIE_BRANCH" || warn "could not fast-forward; using the local checkout as-is"
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
    preflight
    resolve_target_dir
    fetch_repo
    step "Running the quickstart (build -> migrate -> seed an example)"
    echo ""
    cd "$OPENMAGPIE_DIR"
    # `sh <script>` (not `./<script>`) so the handoff doesn't depend on the +x
    # bit surviving the clone or on shebang resolution. run.sh resolves its own
    # location from $0 regardless.
    exec sh ./scripts/quickstart/run.sh
}

main "$@"
