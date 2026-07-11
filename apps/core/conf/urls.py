import logging
import re

from django.conf import settings
from django.urls import path
from django.urls.resolvers import URLPattern, URLResolver

from common.urls import api_include
from common.views import healthz

logger = logging.getLogger("plugins")

# NOTE: we intentionally do NOT mount `oauth2_provider.urls`. The Toolkit
# models (AccessToken / RefreshToken / Application) are useful storage
# primitives that our own /v1/auth/* views compose with directly; we have
# no need for Toolkit's HTTP surface (which would expose /oauth/token's
# password / client_credentials grants and bypass our login/audit
# pipeline). Reinstate only if you build a real OAuth-provider flow.
#
# `api_include` makes the trailing slash on the prefix optional;
# `api_path` (inside each app's urlconf) makes the trailing slash on
# each leaf route optional. Together they let every /v1/<app>[/<leaf>]
# route work with or without a final slash.
_V1 = settings.API_VERSION_PREFIX

urlpatterns = [
    path("healthz", healthz, name="healthz"),
    api_include(f"{_V1}/auth", "auth_api.urls"),
    api_include(f"{_V1}/feeds", "feeds.urls"),
    # One item's / source's detail by its own ULID (dependent records of a feed,
    # so parent-qualified) ; the lists are nested under the feed.
    api_include(f"{_V1}/feed-items", "feeds.item_urls"),
    api_include(f"{_V1}/feed-sources", "feeds.source_urls"),
    api_include(f"{_V1}/watches", "watches.urls"),
    # Per-action ops live at the top level (addressed by the action's own
    # ULID), not under /watches/<id>/actions ; see watches.action_urls.
    api_include(f"{_V1}/actions", "watches.action_urls"),
    # One run's / delivery's detail by its own ULID (dependent records of an
    # action, so parent-qualified) ; the lists are nested under the action.
    api_include(f"{_V1}/action-activity", "watches.activity_urls"),
    api_include(f"{_V1}/action-deliveries", "watches.delivery_urls"),
    # A backfill job's list + status by its own ULID (dependent records of an action,
    # so parent-qualified, like action-activity / action-deliveries; the submit is
    # action-scoped at /v1/actions/<id>/backfill ; see watches.backfill_urls).
    api_include(f"{_V1}/action-backfills", "watches.backfill_urls"),
    api_include(f"{_V1}/engines", "engine.urls"),
    # Public, unauthenticated waitlist signup (marketing site).
    api_include(f"{_V1}/waitlist", "waitlist.urls"),
    # Anonymous, opt-out product telemetry mode (read by any user; set by an account owner).
    api_include(f"{_V1}/telemetry", "telemetry.urls"),
]


# Leading `/<version>/<segment>` a pattern claims, for collision LOGGING only.
# Best-effort: an exotic pattern that doesn't match just yields no log line, never
# a wrong route (Django's own resolution is unaffected).
_LEADING_SEGMENT = re.compile(rf"\^?(?:{re.escape(_V1)}/)?(?P<seg>[\w-]+)")


def _leading_segment(pattern: URLPattern | URLResolver) -> str | None:
    match = _LEADING_SEGMENT.match(str(pattern.pattern))
    return match.group("seg") if match else None


# The top-level `/<version>/<segment>` names core already owns (from the static
# patterns above, before any plugin includes are appended).
_CORE_V1_SEGMENTS = {
    seg for p in urlpatterns if str(p.pattern).lstrip("^").startswith(f"{_V1}/") and (seg := _leading_segment(p))
}


def _plugin_api_includes() -> list[URLResolver]:
    """Fork urlconf includes contributed via OPENMAGPIE_PLUGIN_API_URLS
    (settings.PLUGIN_API_URLS, dotted module paths), mounted UNDER the API version
    prefix so the fork writes version-relative routes (e.g. `database` -> /v1/database)
    without repeating the prefix. Core patterns are listed first, so any core route
    that actually MATCHES is served by core; but Django backtracks past a core include
    that 404s internally, so a path UNDER a core segment that core doesn't serve (e.g.
    /v1/feeds/<unknown>) can still fall through to a plugin. So this is additive for
    served core routes, not a blanket "core owns its whole segment" guarantee. Empty by
    default, so core's URL surface is unchanged until a fork opts in.

    Mounts via `api_include` (the mandated helper for mounting an app's urlconf into
    a parent), so the trailing slash on the version prefix is optional just like
    core's own mounts. Paired with the fork's `api_path` leaves, `/v1/<route>` and
    `/v1/<route>/` both resolve."""
    return [api_include(_V1, module) for module in settings.PLUGIN_API_URLS]


def _log_plugin_routes(includes: list[URLResolver]) -> None:
    """Log the `/<version>/<segment>` routes each plugin urlconf mounts, warning on a
    segment that overlaps a core segment (core serves the paths it matches; the plugin
    only sees what core 404s under that segment) or an earlier plugin (first match
    wins). Leading-segment granularity; visibility only, never a throw."""
    claimed: dict[str, str] = {}
    for module, resolver in zip(settings.PLUGIN_API_URLS, includes, strict=True):
        for seg in sorted({s for leaf in resolver.url_patterns if (s := _leading_segment(leaf))}):
            route = f"/{_V1}/{seg}"
            if seg in _CORE_V1_SEGMENTS:
                logger.warning(
                    "plugin urlconf %r mounts %s, overlapping a core segment (core serves the paths it "
                    "matches; the plugin only sees sub-paths core 404s)",
                    module,
                    route,
                )
            elif seg in claimed:
                logger.warning(
                    "plugin urlconf %r mounts %s, already claimed by %r (first wins)", module, route, claimed[seg]
                )
            else:
                claimed[seg] = module
                logger.info("plugin urlconf %r mounts %s", module, route)


_plugin_includes = _plugin_api_includes()
urlpatterns += _plugin_includes
_log_plugin_routes(_plugin_includes)
