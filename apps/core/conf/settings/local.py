import os

# Seed local-only defaults BEFORE importing base.py, so its `os.environ[...]`
# reads resolve to localhost values when the env didn't set them. Keeps
# base.py honest (no localhost fallbacks baked into prod settings) while
# the dev loop still works out of the box.
os.environ.setdefault("BASE_URL", "http://localhost:8000")
os.environ.setdefault("APP_BASE_URL", "http://localhost:3001")
# The marketing site (separate Next app) ; its public waitlist POST is
# cross-origin to the API, so its origin has to be on the CORS allowlist.
os.environ.setdefault("MARKETING_BASE_URL", "http://localhost:3000")
# Email-render sidecar (web/apps/email-render, runs in the `web` container).
# core reaches it by the compose service name, like flaresolverr below. Dev
# default only ; base.py leaves it empty so prod must set it explicitly.
os.environ.setdefault("EMAIL_RENDER_URL", "http://web:3010")
# Browser ↔ Django is plain HTTP in dev; secure cookies would never get sent.
os.environ.setdefault("AUTH_COOKIE_SECURE", "false")
# Dev points at the docker-compose flaresolverr service ; prod operators
# set this themselves (or leave it empty to disable the capability).
os.environ.setdefault("SOURCE_CHALLENGE_BYPASS_URL", "http://flaresolverr:8191/v1")
# Single-tenant self-host accepts the broken-publisher / MITM-downgrade
# trade-off for convenience ; prod multi-tenant deployments leave it off.
os.environ.setdefault("SOURCE_ALLOW_INSECURE_TLS", "true")

from common.env import env_bool

from .base import *  # noqa: F403

DEBUG = env_bool("DEBUG", "true")
ALLOWED_HOSTS = ["localhost", "127.0.0.1", "core", ".ngrok-free.app"]

# CORS: explicit allowlist even in dev. Allow-all + credentials would
# let any localhost page (a random dev server on :3000, a one-off
# tool's preview page) credentialed-fetch our /v1/auth/me and read
# session-bearing responses. Safe-method GETs aren't gated by our
# Origin-check CSRF (only non-safe methods are), so the allowlist is
# the only thing keeping cross-origin reads off the browser cookie.
CORS_ALLOWED_ORIGINS = [os.environ["APP_BASE_URL"], os.environ["MARKETING_BASE_URL"]]
CORS_ALLOW_CREDENTIALS = True
CSRF_TRUSTED_ORIGINS = [os.environ["APP_BASE_URL"], os.environ["MARKETING_BASE_URL"]]
