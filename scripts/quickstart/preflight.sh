#!/bin/sh
#
# The quickstart's prerequisites gate. Checks everything up front (supported OS,
# git, Docker + Compose + a running daemon, uv), prints a styled +/- checklist,
# and if anything is missing lists how to install each and stops, all at once,
# rather than dying one command deep into the run.
#
# Single source for both entry points, parameterized by who invoked it:
#   $1 = caller, `run` (clone-first, the default) or `bootstrap` (curl|sh). It
#        only tailors the "re-run" hint when something's missing.
# The curl|sh bootstrap runs this right after cloning and exports
# OPENMAGPIE_PREFLIGHTED so run.sh doesn't repeat it; clone-first, run.sh runs
# it. The downstream checks stay as a safety net (scripts/check-docker.sh guards
# `make build`; scripts/install-local-cli.sh guards uv when run alone).
#
# POSIX sh, on the curl|sh path. Guide-only: it never installs anything.
set -eu

via="${1:-run}"
os="$(uname -s)"

# Color the checklist marks when stdout is a terminal (curl|sh keeps stdout on
# the tty); plain otherwise. printf for the escapes (echo mangles them).
if [ -t 1 ]; then
    GREEN=$(printf '\033[0;32m'); RED=$(printf '\033[0;31m'); BOLD=$(printf '\033[1m'); NC=$(printf '\033[0m')
else
    GREEN=''; RED=''; BOLD=''; NC=''
fi

# Each check prints its line immediately (so the list reads top to bottom) and a
# miss stashes its install hint, printed together after the list. No arrays in
# POSIX sh, so fixes are newline-joined.
fixes=""
pass() { printf '%s\n' "  ${GREEN}+${NC} $1"; }
miss() { printf '%s\n' "  ${RED}-${NC} ${RED}$1${NC}"; fixes="${fixes}  $2
"; }

docker_install_hint() {
    case "$os" in
        Darwin) printf '%s' "Docker Desktop: https://docs.docker.com/desktop/install/mac-install/ (or brew install --cask docker), then launch it." ;;
        Linux)  printf '%s' "Docker Engine + Compose plugin: https://docs.docker.com/engine/install/" ;;
        *)      printf '%s' "Docker: https://docs.docker.com/get-docker/" ;;
    esac
}

# Compose < 2.20 fails `up --wait` on an exited one-shot even with
# service_completed_successfully (docker/compose#10596) -> it reproduces the very
# `up --wait` hang core-setup is meant to avoid, with no hint why. Sets
# compose_ver as a side effect; returns true ONLY when we can parse a version AND
# it's older than the floor (unparseable -> false, so we never block on a guess).
COMPOSE_MIN_MAJOR=2
COMPOSE_MIN_MINOR=20
compose_ver=""
compose_too_old() {
    compose_ver="$(docker compose version 2>/dev/null | grep -oE 'v?[0-9]+\.[0-9]+(\.[0-9]+)?' | head -1 | sed 's/^v//')"
    cmaj="${compose_ver%%.*}"
    crest="${compose_ver#*.}"
    cmin="${crest%%.*}"
    case "$cmaj" in '' | *[!0-9]*) return 1 ;; esac
    case "$cmin" in '' | *[!0-9]*) cmin=0 ;; esac
    [ "$cmaj" -lt "$COMPOSE_MIN_MAJOR" ] || { [ "$cmaj" -eq "$COMPOSE_MIN_MAJOR" ] && [ "$cmin" -lt "$COMPOSE_MIN_MINOR" ]; }
}

printf '%s\n' "${BOLD}Checking prerequisites${NC}"

case "$os" in
    Darwin | Linux) pass "OS: $os" ;;
    *) miss "OS: $os (need macOS or Linux)" "On Windows, run the quickstart inside WSL2: https://learn.microsoft.com/windows/wsl/install" ;;
esac

if command -v git >/dev/null 2>&1; then
    pass "git"
else
    case "$os" in
        Darwin) miss "git" "git: xcode-select --install (or via Homebrew)" ;;
        *)      miss "git" "git: sudo apt-get install -y git (or your distro's package manager)" ;;
    esac
fi

# Docker: installed -> Compose plugin -> daemon running. Name the failing stage.
if ! command -v docker >/dev/null 2>&1; then
    miss "Docker" "$(docker_install_hint)"
elif ! docker compose version >/dev/null 2>&1; then
    miss "Docker Compose plugin" "$(docker_install_hint)"
elif ! docker info >/dev/null 2>&1; then
    case "$os" in
        Darwin) miss "Docker daemon (not running)" "Launch Docker Desktop and wait for it to finish starting." ;;
        Linux)  miss "Docker daemon (not running)" "Start it: sudo systemctl start docker" ;;
        *)      miss "Docker daemon (not running)" "Start the Docker daemon, then re-run." ;;
    esac
elif compose_too_old; then
    miss "Docker Compose ${compose_ver:-(unknown)} (need >= ${COMPOSE_MIN_MAJOR}.${COMPOSE_MIN_MINOR})" \
        "Compose < ${COMPOSE_MIN_MAJOR}.${COMPOSE_MIN_MINOR} hangs 'up --wait' on the one-shot core-setup. Update Docker Desktop / the Compose plugin: $(docker_install_hint)"
else
    pass "Docker + Compose ${compose_ver:-} (daemon running)"
fi

# uv lands in ~/.local/bin by default, which may not be on PATH in this shell.
if command -v uv >/dev/null 2>&1 || [ -x "$HOME/.local/bin/uv" ]; then
    pass "uv"
else
    miss "uv" "uv (for the magpie CLI): curl -LsSf https://astral.sh/uv/install.sh | sh
     docs: https://docs.astral.sh/uv/getting-started/installation/"
fi

if [ -n "$fixes" ]; then
    case "$via" in
        bootstrap) rerun="curl -fsSL https://openmagpie.ai | sh" ;;
        *)         rerun="./scripts/quickstart/run.sh" ;;
    esac
    printf '\n%s\n%sThen re-run: %s\n' "${BOLD}Missing prerequisites. Install:${NC}" "$fixes" "$rerun" >&2
    exit 1
fi
