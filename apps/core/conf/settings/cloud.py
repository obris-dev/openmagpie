"""Cloud (hosted) settings. `DJANGO_ENV=cloud` -> `conf.settings.cloud`.

The production counterpart to `local.py`. Extends `base.py` with production-safe
values + the stable non-secret SMTP constants + the canonical openmagpie.ai
URLs, so a deploy supplies only secrets and infra hosts — not a wall of config.

DEPLOY CHECKLIST — set these in the cloud env; everything else has a sane
default (here or in base.py). (S) = secret, store in the secret manager.

    DJANGO_ENV=cloud           selects this settings module
    DJANGO_SECRET_KEY     (S)  long random string
    POSTGRES_HOST              managed db host (DB/USER/PORT default to openmagpie/openmagpie/5432)
    POSTGRES_PASSWORD     (S)  db password
    ENGINE_BASE_URL            OpenAI-compatible LLM `/v1` base URL
    ENGINE_MODEL               LLM model id (e.g. qwen2.5:7b, gpt-4o-mini)
    ENGINE_API_KEY        (S)  Bearer key if the LLM is hosted (omit for local)
    EMAIL_RENDER_URL           email-render sidecar URL (unset -> sends retry to the cap, then FAILED)
    EMAIL_HOST_USER       (S)  Brevo SMTP login
    EMAIL_HOST_PASSWORD   (S)  Brevo SMTP key

Defaulted to the openmagpie.ai deployment (override via env for another):
    BASE_URL, APP_BASE_URL, MARKETING_BASE_URL, ALLOWED_HOSTS,
    CORS_ALLOWED_ORIGINS, CSRF_TRUSTED_ORIGINS.
This instance's own IP is auto-added to ALLOWED_HOSTS at runtime for health checks.

Optional overrides: DEFAULT_FROM_EMAIL, EMAIL_HOST/PORT (non-Brevo relay),
SECURE_SSL_REDIRECT (on by default), SECURE_HSTS_SECONDS (off; enable once TLS
is stable), the WEBHOOK_*/SOURCE_* SSRF gates (on by default).
"""

import contextlib
import os
import socket

# Canonical openmagpie.ai URLs, seeded BEFORE base.py reads BASE_URL /
# APP_BASE_URL as required os.environ[...] (the same pattern local.py uses for
# its localhost defaults). Override any via env for a different deployment.
os.environ.setdefault("BASE_URL", "https://api.openmagpie.ai")
os.environ.setdefault("APP_BASE_URL", "https://app.openmagpie.ai")
os.environ.setdefault("MARKETING_BASE_URL", "https://openmagpie.ai")
os.environ.setdefault("ALLOWED_HOSTS", "api.openmagpie.ai")
# Browser origins that call the API: the app + both marketing hosts (apex +
# www). www stays OUT of ALLOWED_HOSTS (that's the API host only) but IS a CORS
# origin (the marketing waitlist form posts cross-origin from it).
_ORIGINS = "https://app.openmagpie.ai,https://www.openmagpie.ai,https://openmagpie.ai"
os.environ.setdefault("CORS_ALLOWED_ORIGINS", _ORIGINS)
os.environ.setdefault("CSRF_TRUSTED_ORIGINS", _ORIGINS)

from common.env import env_bool  # noqa: E402

from .base import *  # noqa: E402, F403

# Never debug in the hosted env.
DEBUG = False

# Host(s) this serves (seeded above; override via env).
ALLOWED_HOSTS = [h.strip() for h in os.environ["ALLOWED_HOSTS"].split(",") if h.strip()]

# Allow this instance's own IP so health checks that reach it directly by IP
# (rather than by hostname) aren't rejected by ALLOWED_HOSTS under DEBUG=False.
# Resolved at runtime; suppress(gaierror) so a resolution miss never crashes boot.
with contextlib.suppress(socket.gaierror):
    ALLOWED_HOSTS.append(socket.gethostbyname(socket.gethostname()))

# ── Transport security ───────────────────────────────────────────────────
# Behind a TLS-terminating proxy / load balancer: trust its forwarded scheme so
# Django knows the original request was HTTPS (drives secure-cookie + redirect).
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# Session + CSRF cookies only over HTTPS. (The auth_token cookie already defaults
# secure via base's AUTH_COOKIE_SECURE.)
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True

# HTTP->HTTPS redirect ON by default: with SECURE_PROXY_SSL_HEADER above, a
# standard TLS-terminating proxy makes this safe, and it's fully RECOVERABLE
# (set SECURE_SSL_REDIRECT=false) if a no-TLS bring-up or a proxy that doesn't
# set X-Forwarded-Proto needs it off.
SECURE_SSL_REDIRECT = env_bool("SECURE_SSL_REDIRECT", "true")
# HSTS stays OPT-IN (default 0). Unlike the redirect, it's a PERSISTENT,
# hard-to-undo instruction browsers cache — a wrong default can lock the domain
# to HTTPS for the whole max-age. Turn it on (e.g. 31536000 = 1y) once TLS is
# confirmed stable; includeSubDomains / preload are extra-irreversible, so they
# stay off until deliberately enabled.
SECURE_HSTS_SECONDS = int(os.environ.get("SECURE_HSTS_SECONDS", "0"))
SECURE_HSTS_INCLUDE_SUBDOMAINS = env_bool("SECURE_HSTS_INCLUDE_SUBDOMAINS", "false")
SECURE_HSTS_PRELOAD = env_bool("SECURE_HSTS_PRELOAD", "false")

# ── SSRF / outbound-safety gates ─────────────────────────────────────────
# base.py leaves these OFF (permissive) for single-tenant self-host where an
# operator might legitimately target an internal webhook / feed. The hosted
# deployment is multi-tenant and takes UNTRUSTED user input (webhook URLs,
# source URLs), so default them ON: require HTTPS webhooks and block requests to
# private / loopback / link-local / metadata (169.254.169.254) addresses. Still
# env-overridable.
WEBHOOK_REQUIRE_HTTPS = env_bool("WEBHOOK_REQUIRE_HTTPS", "true")
WEBHOOK_BLOCK_PRIVATE_IPS = env_bool("WEBHOOK_BLOCK_PRIVATE_IPS", "true")
SOURCE_BLOCK_PRIVATE_IPS = env_bool("SOURCE_BLOCK_PRIVATE_IPS", "true")

# ── Transactional email ──────────────────────────────────────────────────
# Pinned SMTP (Brevo) — public + rarely changes, so it lives here, not in env.
# Env still carries the SECRETS (EMAIL_HOST_USER / EMAIL_HOST_PASSWORD) and the
# deploy-specific bits (DEFAULT_FROM_EMAIL, EMAIL_RENDER_URL) — all read in
# base.py. EMAIL_HOST / EMAIL_PORT keep an env override for a non-Brevo relay.
EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
EMAIL_HOST = os.environ.get("EMAIL_HOST", "smtp-relay.brevo.com")
EMAIL_PORT = int(os.environ.get("EMAIL_PORT", "587"))
EMAIL_USE_TLS = True
