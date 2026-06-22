"""Telemetry constants: the deployment label + the inbound-surface allowlist.

(The PostHog ingestion key + host defaults live in conf.settings.base, declared
inline alongside every other setting default; the client reads them via
`settings.POSTHOG_API_KEY` / `settings.POSTHOG_HOST`.)
"""

from openmagpie_schema.telemetry import SURFACE_HEADER, Surface

# How this deployment labels itself on every event. Self-hosted is the only value
# today; the hosted product adds its own. A named constant, not a literal at the
# capture site.
DEPLOYMENT_SELF_HOSTED = "self_hosted"

# Inbound-header allowlist (server policy): a client may declare cli/web/api but
# NOT system, which is server-set only (the scheduler / quickstart) so it can't be
# spoofed via the header. Surface (the enum) + SURFACE_HEADER are the shared
# CLI<->server contract in openmagpie_schema, re-exported here for server imports.
ALLOWED_SURFACES = frozenset(Surface) - {Surface.SYSTEM}

__all__ = [
    "ALLOWED_SURFACES",
    "DEPLOYMENT_SELF_HOSTED",
    "SURFACE_HEADER",
    "Surface",
]
