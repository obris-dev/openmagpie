#!/bin/sh
#
# Preflight for the Docker-based make targets (build) and the quickstart:
# verify Docker + the Compose plugin are installed and the daemon is running,
# with friendly OS-specific guidance instead of make's raw
# "docker: No such file or directory" when Docker is absent.
#
# POSIX sh: it's on the quickstart installer path (run.sh calls it), so it must
# run wherever `curl ... | sh` does. Guide-only: it never installs or changes
# anything, just points at the official docs and exits non-zero so the caller
# stops cleanly.
set -eu

os="$(uname -s)"

docker_guide() {
    echo ""
    echo "OpenMagpie needs Docker + the Compose plugin, with the Docker daemon running."
    case "$os" in
        Darwin)
            echo "  macOS: install Docker Desktop -> https://docs.docker.com/desktop/install/mac-install/"
            echo "         (or: brew install --cask docker), then launch it so the daemon is running."
            ;;
        Linux)
            echo "  Linux: install Docker Engine + the Compose plugin -> https://docs.docker.com/engine/install/"
            ;;
        *)
            echo "  See https://docs.docker.com/get-docker/"
            ;;
    esac
    echo ""
}

if ! command -v docker >/dev/null 2>&1; then
    echo "Error: Docker is not installed."
    docker_guide
    exit 1
fi

if ! docker compose version >/dev/null 2>&1; then
    echo "Error: the Docker Compose plugin is not available ('docker compose' did not run)."
    docker_guide
    exit 1
fi

if ! docker info >/dev/null 2>&1; then
    echo "Error: the Docker daemon is not running."
    case "$os" in
        Darwin) echo "  Launch Docker Desktop and wait for it to finish starting, then re-run." ;;
        Linux)  echo "  Start it: sudo systemctl start docker" ;;
        *)      echo "  Start the Docker daemon, then re-run." ;;
    esac
    exit 1
fi
