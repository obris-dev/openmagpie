#!/bin/sh
#
# Run the OpenMagpie quickstart in a cloned repo, without make: build the stack,
# migrate, seed an example feed + watch, install git hooks. Invoked by the
# bootstrap (scripts/quickstart/bootstrap.sh) right after it clones, and runnable
# on its own from a checkout:  ./scripts/quickstart/run.sh
#
# POSIX sh, like everything on the installer path (the scripts/check-docker.sh
# and scripts/hooks.sh it calls are sh too), so the whole curl|sh flow is
# bash-free.
set -eu

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

# Invoke siblings via `sh <script>` (not `./<script>`) for the same reason the
# bootstrap does: don't depend on the +x bit surviving the clone / a tarball.
sh ./scripts/check-docker.sh

if [ ! -f apps/core/.env ]; then
    cp apps/core/.env.example apps/core/.env
    echo "Created apps/core/.env from .env.example"
fi

docker compose up --build -d --wait

# Mirrors the manage commands `make local-migrate` runs. --noinput because we
# exec with stdin closed/at-EOF (the curl|sh pipe), so a prompt can't be
# answered; migrate is non-interactive in practice, but be explicit.
manage() { docker compose exec -T core uv run --package openmagpie-core python apps/core/manage.py "$@"; }
manage migrate --noinput
manage createcachetable
manage bootstrap_oauth_app

sh ./scripts/quickstart/seed.sh
# Hooks are a best-effort nicety; a pre-commit failure must not abort the
# quickstart after the stack is already up (the user still wants "Ready. Next").
sh ./scripts/hooks.sh || true

cat <<'EOF'

Ready. Next:
  App:        http://localhost:3001  (create an account; you're signed in)
  Marketing:  http://localhost:3000
  CLI:        make install-cli   then   magpie auth login
  Dev:        the dev loop runs through make (see `make help`); install make if you don't have it.

The web (App + Marketing) builds on first boot, so give those URLs a minute if
they don't load right away.

Heads up: OpenMagpie is BYO-LLM. Point OLLAMA_URL in apps/core/.env at an
Ollama you control. The default (http://host.docker.internal:11434) reaches
Ollama on your own machine; host.docker.internal is how the container talks to
your host. For a remote box, set OLLAMA_URL to its address.
EOF
