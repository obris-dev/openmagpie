# uv workspace image for the `openmagpie-core` member.
#
# Build context is the REPO ROOT (see docker-compose.yml): the single uv.lock +
# every member's pyproject.toml live there and uv resolves the whole workspace.
#
# Multi-stage: psycopg[c] (the cloud group) compiles against libpq, so the
# builder carries the compiler + libpq headers; the runtime ships only the
# libpq5 shared lib and runs non-root, staying slim.

# ---------- builder ----------
FROM python:3.14-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# PYTHONUNBUFFERED: real-time management-command output when piped.
# UV_LINK_MODE=copy: avoid hardlink warnings across the cache mount.
# UV_PYTHON_*: use the base image's system Python (3.14, matching .python-version),
# never download a uv-managed interpreter. The managed one lands under /root
# (mode 700) and breaks the non-root runtime; pinning the base to the same
# version keeps the venv on the world-readable /usr/local interpreter.
ENV PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PYTHON_PREFERENCE=only-system

# Build deps for psycopg[c] (compiled against libpq). Builder-only.
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential git libpq-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Deps-only layer: just the manifests needed to resolve the workspace, so this
# cache layer only busts when dependencies change. (The `cloud`/`dev` groups are
# member-scoped, so they're selected in the member sync below, not here.)
COPY pyproject.toml uv.lock ./
COPY apps/core/pyproject.toml apps/core/pyproject.toml
COPY apps/cli/pyproject.toml apps/cli/pyproject.toml
COPY packages/openmagpie-schema/pyproject.toml packages/openmagpie-schema/pyproject.toml
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-workspace

# Full workspace, then install just this member (+ its workspace deps, e.g.
# openmagpie-schema). --no-dev keeps prod lean (no ruff/ty/psycopg[binary]);
# --group cloud adds gunicorn + psycopg[c] (compiled here against libpq).
COPY . /app
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --package openmagpie-core --group cloud

# ---------- runtime ----------
FROM python:3.14-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

ENV PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PYTHON_PREFERENCE=only-system

# Runtime shared lib for the compiled psycopg[c].
RUN apt-get update \
    && apt-get install -y --no-install-recommends libpq5 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
# The built venv + app code, owned by the non-root runtime user (uid 1000). The
# --chown is what lets the same non-root user re-sync the venv in the dev
# compose flow (`uv run` without --no-sync); prod uses --no-sync and only reads
# it. The venv's python symlinks to /usr/local/bin's 3.14 (the runtime base).
COPY --from=builder --chown=1000:1000 /app /app

# Drop root (defense-in-depth on a shared cluster). App code + .venv are
# world-readable, nothing writes to /app at runtime, and the CMD's --no-sync
# keeps startup from touching the environment (works under a read-only root fs).
RUN useradd --create-home --uid 1000 app
USER app

EXPOSE 8000
# Prod default: gunicorn. `--chdir apps/core` puts apps/core on the path so
# `conf.wsgi` (and `conf.settings`, app packages) import the same way manage.py
# arranges them; wsgi.py picks the settings module from DJANGO_ENV (defaults to
# the locked-down "cloud"). Dev overrides this with runserver via the
# docker-compose `command:`.
CMD ["uv", "run", "--no-sync", "--no-dev", "--package", "openmagpie-core", "--group", "cloud", \
     "gunicorn", "--chdir", "apps/core", "conf.wsgi:application", \
     "--bind", "0.0.0.0:8000", "--workers", "3", "--timeout", "120"]
