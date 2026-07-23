import logging
import os
import time
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

from common.env import env_bool, env_list
from plugins.db.config import load_db_config
from plugins.guards import resolve_entrypoint_allow, resolve_extra_apps, resolve_plugin_api_urls

# Log timestamps in UTC regardless of the host clock. Python's logging
# `asctime` uses `time.localtime` by default ; the app is TIME_ZONE="UTC",
# so force the formatter converter to match (and the datefmt carries a
# trailing `Z` to say so explicitly).
logging.Formatter.converter = time.gmtime

BASE_DIR = Path(__file__).resolve().parents[2]  # core/
REPO_ROOT = BASE_DIR.parents[1]  # repo root (apps/core -> apps -> root); the dev bind mount maps it to /app

SECRET_KEY = os.environ["DJANGO_SECRET_KEY"]

# Two environments: "cloud" (hosted -> conf.settings.cloud) and "local" (dev ->
# conf.settings.local). Unset defaults to "cloud" so a deploy that forgets
# DJANGO_ENV fails SAFE into locked-down settings; dev opts into "local" via
# .env (see apps/core/.env.example). The wsgi/asgi/manage entrypoints pick the
# settings module with the same "cloud" default.
DJANGO_ENV = os.environ.get("DJANGO_ENV", "cloud")
IS_CLOUD = DJANGO_ENV == "cloud"

DJANGO_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # Enables ArrayField (+ its lookups), used by waitlist.source_interests.
    "django.contrib.postgres",
]

THIRD_PARTY_APPS = [
    "corsheaders",
    "oauth2_provider",
    "rest_framework",
]

# Single source of truth for our apps: drives INSTALLED_APPS *and*
# `_APP_LOGGERS` below. Add an app here once and it gets a logger config
# automatically, no second list to keep in sync.
LOCAL_APPS = [
    "common",
    "accounts",
    "auth_api",
    "sources",
    "feeds",
    "engine",
    "watches",
    "waitlist",
    "mailer",
    "links",
    "telemetry",
    "plugins",
]

# Forkable extensibility: a fork can add its own Django apps without editing this
# list, via a comma-separated OPENMAGPIE_EXTRA_APPS env var. They join LOCAL_APPS
# so each also gets a per-app logger (below). A name that collides with any
# already-installed app (Django, third-party, or local), or a duplicate within
# the env var itself, is a misconfig and fails loudly (resolve_extra_apps) rather
# than silently shadowing an app or registering one twice.
LOCAL_APPS += resolve_extra_apps(
    env_list("OPENMAGPIE_EXTRA_APPS"), installed=DJANGO_APPS + THIRD_PARTY_APPS + LOCAL_APPS
)

INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + LOCAL_APPS

AUTH_USER_MODEL = "accounts.User"

# ── Plugins ──────────────────────────────────────────────────────────────
# Self-registering plugin hooks loaded once at startup by the `plugins` app.
# PLUGIN_HOOKS holds `module:function` import paths (the no-packaging path,
# where a fork points OPENMAGPIE_PLUGIN_HOOKS at an in-repo hook).
#
# PLUGIN_ENTRYPOINT_ALLOW gates `openmagpie.plugins` entry-point discovery.
# Loading an entry point runs arbitrary code at boot, so the default follows the
# repo's fail-safe posture (see IS_CLOUD above): the `cloud` env defaults to an
# empty allowlist (no installed plugin loads until it is named), while `local`
# defaults to None (load every installed plugin). Because any production deploy
# runs DJANGO_ENV=cloud (there is no separate self-host-prod module), a
# self-hosted production instance is `cloud` too and must name its plugins; only
# `local` (dev) loads all by default. Setting OPENMAGPIE_PLUGIN_ALLOW overrides
# either default. Both env vars are comma-separated and OPENMAGPIE_-namespaced.
PLUGIN_HOOKS = env_list("OPENMAGPIE_PLUGIN_HOOKS")
PLUGIN_ENTRYPOINT_ALLOW: list[str] | None = resolve_entrypoint_allow(
    os.environ.get("OPENMAGPIE_PLUGIN_ALLOW"), default_when_unset=[] if IS_CLOUD else None
)
# Dotted urlconf module paths a fork mounts UNDER the API version prefix (conf.urls);
# the module writes version-relative routes. Lets a fork add REST endpoints with zero
# core edits; unset -> none.
PLUGIN_API_URLS: list[str] = resolve_plugin_api_urls(os.environ.get("OPENMAGPIE_PLUGIN_API_URLS"))

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    # On the SHORTLINK_HOST, swap request.urlconf to links.urls so a bare `<code>`
    # resolves at the root. Runs before CommonMiddleware (which resolves the URL)
    # and no-ops on every other host, so it never touches the main API routes.
    "links.middleware.ShortLinkHostMiddleware",
    # CORS must come before CommonMiddleware so preflight responses get the
    # right headers regardless of other middleware short-circuiting.
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    # Tags request.surface (cli / web / api) for telemetry; reads a header only.
    "telemetry.middleware.SurfaceMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "conf.urls"

# The dedicated short-link host (a short domain). When a request arrives on this
# host, ShortLinkHostMiddleware swaps request.urlconf to links.urls so a bare
# `<code>` resolves to the redirect at the host root. Empty = the shortener is off.
SHORTLINK_HOST = os.environ.get("SHORTLINK_HOST", "").strip()

# Salt for the click-dedup IP hash. Defaults to SECRET_KEY but is a separate knob
# so rotating SECRET_KEY (a routine op) doesn't silently make historical ip_hash
# values incomparable, which would double-count uniques across the rotation.
SHORTLINK_IP_HASH_SALT = os.environ.get("SHORTLINK_IP_HASH_SALT") or SECRET_KEY

# Trust Cloudflare's CF-Connecting-IP / CF-IPCountry headers for click analytics.
# OFF by default: those headers are client-forgeable unless the origin is reachable
# ONLY through the CF tunnel (where the edge overwrites any supplied value). Turn on
# in the CF-fronted deployment (cloud.py); off-tunnel the click IP falls back to
# REMOTE_ADDR and country is left blank. Note: off-CF, correct per-visitor analytics
# require the origin to see the REAL client IP (direct exposure). Behind a plain
# reverse proxy REMOTE_ADDR is the proxy's IP for everyone, so dedup collapses to one
# bucket. There is no X-Forwarded-For knob here by design (that header is spoofable).
SHORTLINK_TRUST_CF_HEADERS = env_bool("SHORTLINK_TRUST_CF_HEADERS", "false")

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "conf.wsgi.application"

# Postgres, always. The app is a multi-writer pipeline (the long multi-source
# poll runs alongside the trigger / drain / flush crons); SQLite's
# single-writer whole-file lock serializes those into "database is locked"
# under real volume, whereas Postgres' MVCC + row-level locking lets the CAS
# `claim_due`, the `select_for_update` digest windows, and the poll lease work
# as designed. Params are env-driven (the docker stack points HOST at `db`).
# Persistent connections for the web workers (the short-lived cron commands
# reconnect each run anyway); shared by plugin databases so they don't diverge.
DB_CONN_MAX_AGE = int(os.environ.get("DB_CONN_MAX_AGE", "60"))
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ.get("POSTGRES_DB", "openmagpie"),
        "USER": os.environ.get("POSTGRES_USER", "openmagpie"),
        # Required, no default: a missing password must fail loudly, not
        # silently fall back to a known credential (same rule as SECRET_KEY).
        "PASSWORD": os.environ["POSTGRES_PASSWORD"],
        "HOST": os.environ.get("POSTGRES_HOST", "localhost"),
        "PORT": os.environ.get("POSTGRES_PORT", "5432"),
        "CONN_MAX_AGE": DB_CONN_MAX_AGE,
    }
}

# Forkable extensibility: a fork can add its own database connections (and route
# its apps to them) via a JSON file pointed at by OPENMAGPIE_DB_CONFIG, so its
# tables live in whatever database it chooses and never enter core's schema or
# migrations. See plugins.db.config for the file shape. A conflicting alias, or a
# route to an unknown alias, fails loudly there.
PLUGIN_DB_ROUTING: dict[str, str] = {}
_db_config_path = os.environ.get("OPENMAGPIE_DB_CONFIG")
if _db_config_path:
    PLUGIN_DB_ROUTING = load_db_config(_db_config_path, DATABASES, conn_max_age=DB_CONN_MAX_AGE)

# One router, a no-op until an app is routed: it sends a routed app's models to
# its declared database and keeps every other app on `default`.
DATABASE_ROUTERS = ["plugins.db.routers.PluginAppRouter"]

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

BASE_URL = os.environ["BASE_URL"]

# Where the PRODUCT APP lives (login, the browser-facing authorize URL the CLI
# prints during device flow, the cookie-auth Origin allowlist). Required;
# local.py / cloud.py supply env-specific defaults.
APP_BASE_URL = os.environ["APP_BASE_URL"]

# Where the MARKETING site lives (the public landing + waitlist). Used for
# links in outbound email; also an allowed CORS origin. Required (like
# APP_BASE_URL): an empty value would silently ship broken links in delivered
# mail, so fail at boot instead. local.py / cloud.py supply env-specific defaults.
MARKETING_BASE_URL = os.environ["MARKETING_BASE_URL"]

# All HTTP API routes are mounted under /{API_VERSION_PREFIX}/. The web
# frontend reads the same value from NEXT_PUBLIC_API_VERSION, keep them in
# lockstep when bumping versions.
API_VERSION_PREFIX = os.environ.get("API_VERSION_PREFIX", "v1")

# Product version: the release-please `.` track's version.txt at the repo root (NOT
# apps/core's own pyproject version, and NOT API_VERSION_PREFIX, the route/compat
# axis). It's always present (bind-mounted to /app in dev, COPY'd into the image), so
# we just read it; "unknown" only if it's somehow unreadable (best-effort: never crash
# settings import over a cosmetic string). Exposed on /healthz so a client can see what
# the server is running.
try:
    PRODUCT_VERSION = (REPO_ROOT / "version.txt").read_text(encoding="utf-8").strip() or "unknown"
except (OSError, UnicodeDecodeError):
    PRODUCT_VERSION = "unknown"

# Transactional email. Enqueued (mailer.OutboundEmail) by request handlers, then
# rendered out-of-process by the email-render service (EMAIL_RENDER_URL/render)
# and sent via Django's EMAIL_BACKEND by the send_outbound_emails drain. The
# backend defaults to the console backend so dev needs no SMTP creds (mail
# prints to the drain's output); prod points it at Brevo SMTP via env. See
# common.email.EmailService + apps/core/mailer.
#
# EMAIL_RENDER_URL has NO localhost default here (dev seeds it in local.py), so
# an unset value fails loudly (EmailRenderError) rather than POSTing to
# localhost. This does NOT silently skip: the drain retries the row to the
# attempts cap, then marks it FAILED (visible), so a missing/broken render URL
# surfaces instead of vanishing. Enqueue itself is a cheap insert, so a signup
# never blocks or fails on email.
EMAIL_RENDER_URL = os.environ.get("EMAIL_RENDER_URL", "").rstrip("/")
DEFAULT_FROM_EMAIL = os.environ.get("DEFAULT_FROM_EMAIL", "OpenMagpie <noreply@openmagpie.ai>")
EMAIL_BACKEND = os.environ.get("EMAIL_BACKEND", "django.core.mail.backends.console.EmailBackend")
EMAIL_HOST = os.environ.get("EMAIL_HOST", "")
EMAIL_PORT = int(os.environ.get("EMAIL_PORT", "587"))
EMAIL_HOST_USER = os.environ.get("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.environ.get("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = env_bool("EMAIL_USE_TLS", "true")
# Bounds BOTH the render request (httpx) and the SMTP send (Django reads
# EMAIL_TIMEOUT) so a hung render service or SMTP server can't pin a worker
# (the send_outbound_emails drain renders + sends out-of-request). Seconds.
EMAIL_TIMEOUT = int(os.environ.get("EMAIL_TIMEOUT", "10"))

# Outbound-email queue drain (mailer.send_outbound_emails). Mirror the
# WATCH_RUN_* tunables: a row is claimed (attempts++) only while attempts <
# MAX; past it it stays terminally FAILED. STALE_SECONDS reaps a SENDING row
# orphaned by a crashed worker back to PENDING; RETRY_SECONDS is the backoff a
# transient failure waits before the next attempt.
EMAIL_SEND_MAX_ATTEMPTS = int(os.environ.get("EMAIL_SEND_MAX_ATTEMPTS", "5"))
EMAIL_SEND_STALE_SECONDS = int(os.environ.get("EMAIL_SEND_STALE_SECONDS", "300"))
EMAIL_SEND_RETRY_SECONDS = int(os.environ.get("EMAIL_SEND_RETRY_SECONDS", "300"))

# CORS / CSRF for the web client. Empty by default so prod has to opt-in
# explicitly via env; `local.py` overrides with permissive dev defaults.
CORS_ALLOWED_ORIGINS = env_list("CORS_ALLOWED_ORIGINS")
CORS_ALLOW_CREDENTIALS = True
CSRF_TRUSTED_ORIGINS = env_list("CSRF_TRUSTED_ORIGINS")

# `auth_token` browser cookie (set by auth_api.cookies). Secure-by-default
# in base; local.py loosens it for plain-HTTP dev. Domain is unset (host-only)
# unless an explicit subdomain hand-off is needed.
AUTH_COOKIE_SECURE = env_bool("AUTH_COOKIE_SECURE", "true")
AUTH_COOKIE_DOMAIN = os.environ.get("AUTH_COOKIE_DOMAIN") or None

# OAuth Toolkit: minimal config, Bearer tokens via the password-equivalent
# path is handled by our own /v1/auth/login endpoint (which mints tokens via
# AccessToken/RefreshToken directly). The OAuth endpoints themselves are only
# used for refresh_token rotation, proxied through /v1/auth/refresh.
REST_FRAMEWORK = {
    # Our custom auth (Bearer OR auth_token cookie) is the only auth scheme
    # in v0; permission gating is per-view via `IsAuthenticated`. No DRF
    # session auth, so CSRF is not enforced on our APIViews.
    # Order matters: the PAT class owns the `mgp_` bearer prefix and
    # returns None for everything else, so non-PAT bearers + cookies fall
    # through to BearerOrCookieAuthentication.
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "auth_api.authentication.PersonalAccessTokenAuthentication",
        "auth_api.authentication.BearerOrCookieAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [],
    "UNAUTHENTICATED_USER": None,
    # Clears the stale auth_token cookie on AuthenticationFailed so
    # browsers stop sending invalid credentials on every subsequent
    # request after a server-side revoke / expiry.
    "EXCEPTION_HANDLER": "auth_api.exception_handlers.auth_aware_exception_handler",
    # No DEFAULT_THROTTLE_CLASSES (the API isn't globally rate-limited).
    # Only the public waitlist endpoint opts in, via ScopedRateThrottle +
    # `throttle_scope = "waitlist"`; this maps that scope to a per-IP rate.
    "DEFAULT_THROTTLE_RATES": {
        "waitlist": os.environ.get("WAITLIST_THROTTLE_RATE", "30/hour"),
    },
}

OAUTH2_PROVIDER = {
    "ACCESS_TOKEN_EXPIRES_SECONDS": int(os.environ.get("ACCESS_TOKEN_EXPIRES_SECONDS", "3600")),
    "REFRESH_TOKEN_EXPIRE_SECONDS": int(os.environ.get("REFRESH_TOKEN_EXPIRE_SECONDS", str(14 * 86400))),
    "ROTATE_REFRESH_TOKEN": True,
    "SCOPES": {"read": "Read", "write": "Read and write"},
    "DEFAULT_SCOPES": ["read", "write"],
    "PKCE_REQUIRED": False,
}

# Device session (CLI login handshake) TTLs. Short, because the user is
# expected to authorize within minutes; the completed bag is even shorter
# since the CLI polls every 2s and needs only a brief window to pick it up.
DEVICE_SESSION_PENDING_TTL_SECONDS = int(os.environ.get("DEVICE_SESSION_PENDING_TTL_SECONDS", str(15 * 60)))
DEVICE_SESSION_COMPLETED_TTL_SECONDS = int(os.environ.get("DEVICE_SESSION_COMPLETED_TTL_SECONDS", str(5 * 60)))

# Cache (also used as the lock backend for scheduler concurrency control).
# Defaults to Django's db cache, zero-deps, survives process restart, fine
# for single-host. Swap to django-redis / memcached via env when there's a
# real reason (multi-host scheduler, ephemeral locks). Run
# `manage.py createcachetable` once after switching backends or on cold db.
CACHE_BACKEND = os.environ.get("CACHE_BACKEND", "django.core.cache.backends.db.DatabaseCache")
CACHE_LOCATION = os.environ.get("CACHE_LOCATION", "openmagpie_cache")
# The shortener's per-visitor click dedup is a high-volume PUBLIC writer, so it gets
# its OWN cache table. On the same DatabaseCache backend the default cache caps at
# MAX_ENTRIES=300 and culls unexpired rows: sharing it would let click writes evict
# live scheduler job locks (common.locks) / device sessions / throttle counters. A
# separate LOCATION isolates that (and lets the dedup table cull independently).
# `manage.py createcachetable` (no args) creates a table for EVERY DatabaseCache here.
CLICK_DEDUP_CACHE_LOCATION = os.environ.get("CLICK_DEDUP_CACHE_LOCATION", "openmagpie_clickdedup")
CACHES = {
    "default": {"BACKEND": CACHE_BACKEND, "LOCATION": CACHE_LOCATION},
    # Key must equal links.constants.CLICK_DEDUP_CACHE_ALIAS (settings can't import an
    # app module at load). A missing/renamed key does NOT raise: _seen_recently fails
    # open, silently disabling dedup, so a links test pins the alias into settings.CACHES.
    "clickdedup": {"BACKEND": CACHE_BACKEND, "LOCATION": CLICK_DEDUP_CACHE_LOCATION},
}

# Poll-lock LIVENESS window, NOT a cap on total poll time. The poll renews
# the lease after each source (common.locks.LockLease.renew), so a feed of
# any size polls under one continuously-held lock. This value only bounds how
# long a single source may take before a stalled/crashed worker is presumed
# dead and another may take over, so set it comfortably above the slowest
# single-source fetch (not above the whole cycle).
POLL_LOCK_TIMEOUT_SECONDS = int(os.environ.get("POLL_LOCK_TIMEOUT_SECONDS", "600"))

# Feed set-sources serialization lock. The critical section is the
# diff + bulk insert/delete + per-row meta updates ; bounded by the
# count of sources but not by network. 60s is well above a 1k-row
# reconcile and tight enough that a stuck CLI run unblocks.
FEED_SET_LOCK_TIMEOUT_SECONDS = int(os.environ.get("FEED_SET_LOCK_TIMEOUT_SECONDS", "60"))

# Watch-path chain-mutation lock. The critical section is a snapshot +
# dense-rank renumber + bulk_update over a path's actions (~2-10 rows),
# no network. 30s is far above a real renumber and unblocks a stuck run.
PATH_CHAIN_LOCK_TIMEOUT_SECONDS = int(os.environ.get("PATH_CHAIN_LOCK_TIMEOUT_SECONDS", "30"))

# Watch-action-run execution (process_due_runs drain).
# A run is ONE action vs ONE feed item (a semantic_filter = one ~120s LLM
# call), so these size against a single call, NOT a feed's source count.
# MAX_ATTEMPTS: the drain claims a run only while attempts < this (each
# claim burns one) ; past it the run stays terminally FAILED so a broken
# or worker-crashing run can't retry forever.
WATCH_RUN_MAX_ATTEMPTS = int(os.environ.get("WATCH_RUN_MAX_ATTEMPTS", "3"))
# STALE_SECONDS: a run stuck in RUNNING longer than this is presumed crashed and
# reaped to FAILED. Sized above a run's worst-case wall-clock: the engine judge, up to
# (ENGINE_MAX_RETRIES + 1) x ~120s when the SDK retries a timing-out / 5xx backend (so
# ~360s at the default 2), plus (for an enrichment kind) a linked-article fetch up to
# ~30s direct + ~100s challenge-bypass sidecar. ~490s worst case still clears 600s, but
# raise this in step if you raise ENGINE_MAX_RETRIES. A slow-but-alive run is never
# false-reaped; a real crash recovers in <=10m.
WATCH_RUN_STALE_SECONDS = int(os.environ.get("WATCH_RUN_STALE_SECONDS", "600"))
# DRAIN_CONCURRENCY: how many runs process_due_runs drains at once (default 1 =
# serial). N>1 runs the network-bound judge in a thread pool while the CAS claim
# stays serial, so N is the number of concurrent engine calls. The `--concurrency`
# flag overrides this per invocation ; the env var lets the ticker (up-jobs, which
# passes no flag) scale without a Makefile edit. Keep N at or below the engine's rate
# limit (a 429 is retried with backoff up to ENGINE_MAX_RETRIES, then a retryable FAILED).
WATCH_RUN_DRAIN_CONCURRENCY = int(os.environ.get("WATCH_RUN_DRAIN_CONCURRENCY", "1"))

# Backfill jobs (process_due_backfills). A job is pure DB work (scan the source
# passes, optionally delete, bulk-enqueue), chunked, no LLM calls -- so it's not
# sized against the judge timeout. STALE_SECONDS is generous because a wide
# window can scan/enqueue many rows; a job reaped past it rejoins the pool (its
# setup is idempotent + delete-once guarded). Past MAX_ATTEMPTS a job stays FAILED.
WATCH_BACKFILL_MAX_ATTEMPTS = int(os.environ.get("WATCH_BACKFILL_MAX_ATTEMPTS", "3"))
WATCH_BACKFILL_STALE_SECONDS = int(os.environ.get("WATCH_BACKFILL_STALE_SECONDS", "900"))

# Refresh-token rotation lock failsafe. The critical section is one row
# read + revoke + mint, so milliseconds in practice; 30s leaves plenty
# of headroom if the DB is briefly slow.
REFRESH_TOKEN_LOCK_TIMEOUT_SECONDS = int(os.environ.get("REFRESH_TOKEN_LOCK_TIMEOUT_SECONDS", "30"))

# Digest delivery: bounds on a digest action's window length. The window
# open/close is coordinated by select_for_update on the window row (in the
# enqueueing / flushing transaction), not a cache lock, so it composes with
# the drain's completion transaction.
DIGEST_MIN_INTERVAL_SECONDS = int(os.environ.get("DIGEST_MIN_INTERVAL_SECONDS", "60"))
DIGEST_MAX_INTERVAL_SECONDS = int(os.environ.get("DIGEST_MAX_INTERVAL_SECONDS", str(7 * 86400)))
# Max items emitted in ONE digest flush. This is the MEMORY/payload bound: the
# flush loads every batched item's data to build one payload, so the cap is
# what keeps that bounded. A window with more pending runs drains over
# successive flushes, cap items each, oldest first ; the window stays open
# until fully drained. (Separately, the batch's id__in is chunked under the DB
# parameter ceiling — see common.db.ID_IN_CHUNK — so the cap can exceed it.)
DIGEST_MAX_BATCH_ITEMS = int(os.environ.get("DIGEST_MAX_BATCH_ITEMS", "500"))

# Single-flight lock for scheduled jobs (SingleFlightCommand): a second
# run of the same command skips while one is in flight. TTL is purely a
# CRASH failsafe, set deliberately LONG. A drain pass judges items
# synchronously and sequentially (each ~120s, no internal queue), so a
# backlog makes ONE legit pass run for hours; a short TTL would expire
# mid-pass and let a second drain in. A full day stays well past any real
# pass. A normal exit, an exception, and a SIGTERM that REACHES the run all
# release via the finally (SingleFlightCommand turns SIGTERM into a
# SystemExit). A SIGTERM that doesn't reach it (a supervisor killing a
# wrapper, e.g. `make down-jobs`) or a hard SIGKILL orphans the lock until
# the TTL; `manage.py clear_job_locks` is the manual release for those.
JOB_LOCK_TIMEOUT_SECONDS = int(os.environ.get("JOB_LOCK_TIMEOUT_SECONDS", "86400"))

# Relevance engine (the LLM that scores semantic-filter relevance). The engine
# talks to any backend via the OpenAI `/v1` API, so all it needs is an endpoint
# and (maybe) a key - no per-backend "kind" config. ENGINE_BASE_URL is the
# OpenAI-compatible `/v1` base URL (Ollama, vLLM, OpenAI, llama.cpp, LM Studio,
# ...) - required, fail fast at startup if unset (it has a sensible default in
# .env.example). ENGINE_API_KEY is sent as a Bearer token (hosted providers need
# it; local servers ignore it).
try:
    ENGINE_BASE_URL = os.environ["ENGINE_BASE_URL"]
except KeyError as exc:
    # Named explicitly (not a raw KeyError) so an upgrade from the pre-rename
    # config gets the fix, not a cryptic traceback: ENGINE_BASE_URL/ENGINE_MODEL
    # replaced OLLAMA_URL/OLLAMA_DEFAULT_MODEL.
    raise ImproperlyConfigured(
        "ENGINE_BASE_URL is not set. The relevance engine is now any OpenAI-compatible "
        "/v1 endpoint, and ENGINE_BASE_URL (+ optional ENGINE_MODEL) replaces the old "
        "OLLAMA_URL / OLLAMA_DEFAULT_MODEL. Set it in apps/core/.env, e.g. "
        "ENGINE_BASE_URL=http://host.docker.internal:11434/v1"
    ) from exc
ENGINE_API_KEY = os.environ.get("ENGINE_API_KEY", "")
# ENGINE_MODEL is the model to judge with (the fallback when an action leaves
# `engine.model` empty). NOT required - a generic model default would be wrong
# (models are backend-specific), so it's left unset and the stack still boots; an
# `engine.W001` system-check warning flags it and a judge with no model raises a
# clear EngineRequestRejected. List a backend's models with:
#   uv run --package openmagpie-core python -m engine.scripts.probe <ENGINE_BASE_URL> [key]
ENGINE_MODEL = os.environ.get("ENGINE_MODEL", "")
# ENGINE_MAX_RETRIES: how many times the OpenAI client itself retries a transient
# failure (429 / 5xx / timeout) with exponential backoff + jitter, honoring
# Retry-After, BEFORE it surfaces to the drain. This is the smoothing layer that
# matters under WATCH_RUN_DRAIN_CONCURRENCY: without it a rate-limited judge
# fast-fails and the drain instantly claims the next run (which also fails), burning
# attempts across the whole queue. The drain's attempt-based retry is the outer net.
ENGINE_MAX_RETRIES = int(os.environ.get("ENGINE_MAX_RETRIES", "2"))

# Webhook-action security gates (consumed by the WebhookAction when it
# lands). Defaults assume single-tenant self-host with possible internal
# targets. Set stricter values via env for multi-tenant / public deployments.
WEBHOOK_REQUIRE_HTTPS = env_bool("WEBHOOK_REQUIRE_HTTPS", "false")
WEBHOOK_BLOCK_PRIVATE_IPS = env_bool("WEBHOOK_BLOCK_PRIVATE_IPS", "false")
# Per-POST timeout for a webhook delivery. One run is one POST; a hung
# receiver shouldn't tie up a drain slot, and the drain's stale-reaper is
# the backstop above this. 30s matches the v1 notifier.
WEBHOOK_TIMEOUT_SECONDS = int(os.environ.get("WEBHOOK_TIMEOUT_SECONDS", "30"))

# Source-fetch SSRF gate. Parallel to WEBHOOK_BLOCK_PRIVATE_IPS but for
# OUTBOUND-from-connector fetches (RSS, future HTTP-based connectors).
# When true, a Source create / set with an IP-literal URL pointing at a
# private / loopback / link-local / multicast / reserved address is
# rejected at the policy seam (PolicyError -> 400), and the connector
# additionally re-resolves on every outbound request (including
# redirects) so a public hostname that resolves to a private address,
# or a 302 to an internal target, is rejected at poll time as a
# ConnectorParseError. Default off matches the webhook policy posture:
# single-tenant self-host where the operator might legitimately watch
# an internal feed; flip on for multi-tenant / public deployments.
SOURCE_BLOCK_PRIVATE_IPS = env_bool("SOURCE_BLOCK_PRIVATE_IPS", "false")

# Anti-bot challenge-bypass sidecar (shared connector primitive).
# Bot-management products flag Python TLS fingerprints and serve a JS-
# challenge page (HTTP 202, or 200-with-bot-HTML) in place of the real
# payload. When set, any connector mixing in `ChallengeBypassMixin`
# POSTs the failing URL to a FlareSolverr sidecar
# (https://github.com/FlareSolverr/FlareSolverr) which runs headless
# Chrome in its own container, passes the JS challenge, and returns
# the real body. Empty default (no localhost / docker fallback baked
# into prod settings) ; dev seeds the docker-compose service name in
# `conf.settings.local`.
# ALSO backs the linked-article enrichment fallback (watches.actions._external):
# when the direct pinned-IP fetch hits a challenge wall, the article is retried
# through this sidecar. SECURITY: the sidecar egresses via a real browser, so the
# pinned-IP SSRF guard cannot apply; that path pre-validates the (untrusted) URL's
# host and refuses private-resolving hosts, but still trusts the sidecar's egress
# (a self-hosted, operator-opted-in service). Leave empty to disable both uses.
SOURCE_CHALLENGE_BYPASS_URL = os.environ.get("SOURCE_CHALLENGE_BYPASS_URL", "")

# Allow the RSS connector to retry a failing fetch with TLS chain
# verification disabled. Some publisher feeds sit behind stale / self-
# signed / wrong-name certs and serve valid bodies once verification
# is dropped, BUT verify=False is a real MITM downgrade for feed
# content (anyone on-path can forge entries). Default off ; turn on
# only for single-tenant self-host where the operator accepts the
# trade-off.
SOURCE_ALLOW_INSECURE_TLS = env_bool("SOURCE_ALLOW_INSECURE_TLS", "false")

# Product telemetry (anonymous, opt-out; see apps/core/telemetry + TELEMETRY.md).
# POSTHOG_API_KEY defaults to the baked-in PUBLIC, WRITE-ONLY PostHog project key
# (OpenMagpie's anonymous self-hosted project, PostHog Cloud US) so a self-hoster
# reaches our project with zero config -- the same kind of capture-only key every
# PostHog-instrumented web page exposes in page source (it cannot read data or
# administer the project). Override via env to route to your own PostHog, or set it
# empty to hard-disable ingestion. The real gate is the telemetry mode (off /
# anonymous), default UNSET, which emits by default until an operator opts out (off).
# Personal/admin keys never live in the repo.
POSTHOG_API_KEY = os.environ.get("POSTHOG_API_KEY", "phc_mVK4VyhTsbamByvtK42fPQH47irS9fbZMrarsaGP8bw6")
POSTHOG_HOST = os.environ.get("POSTHOG_HOST", "https://us.i.posthog.com")

# Telemetry is opt-OUT + the key is baked in, so a test DB's UNSET singleton would
# otherwise emit real events to the shared project (many tests fire telemetry without
# mocking the client). This runner stubs the PostHog SDK for the whole suite so
# `manage.py test` (local + CI) can never send. See conf/test_runner.py.
TEST_RUNNER = "conf.test_runner.NoTelemetryTestRunner"

# ── Logging ────────────────────────────────────────────────────────────
#
# App loggers are configured explicitly so the warnings the polling /
# delivery / engine code emits surface predictably regardless of
# Python's default lastResort handler quirks. App level is INFO by
# default; per-logger override via the `LOG_LEVEL_<APP>` env so an
# operator can crank `LOG_LEVEL_FEEDS=DEBUG` without editing code.
#
# Django's own loggers are left at framework defaults via
# `disable_existing_loggers=False`.

LOG_LEVEL_APP = os.environ.get("LOG_LEVEL_APP", "INFO").upper()


def _app_log_level(app: str) -> str:
    key = f"LOG_LEVEL_{app.upper()}"
    return os.environ.get(key, LOG_LEVEL_APP).upper()


# Derived from LOCAL_APPS so a new app can't ship without its logger
# config (the drift this avoids).
_APP_LOGGERS = tuple(LOCAL_APPS)

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "concise": {
            "format": "{asctime} {levelname:<7} {name}: {message}",
            "style": "{",
            "datefmt": "%Y-%m-%d %H:%M:%SZ",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "concise",
            # stdout so application logs interleave cleanly with
            # management-command output and `docker compose logs` (and
            # `tee` capture). Operational errors still get emitted via
            # logger.warning/.error (same handler, same stdout); we
            # don't split INFO/ERROR across streams in v0.
            "stream": "ext://sys.stdout",
        },
    },
    "loggers": {
        app: {
            "handlers": ["console"],
            "level": _app_log_level(app),
            "propagate": False,
        }
        for app in _APP_LOGGERS
    },
}
