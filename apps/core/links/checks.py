"""System checks for the shortener: SHORTLINK_HOST must be coherent with the
rest of the host config, so a misconfigured env fails at boot instead of at
request time. Registered from LinksConfig.ready() (mirrors engine/checks.py).
"""

from typing import Any
from urllib.parse import urlparse

from django.conf import settings
from django.core.checks import Error, register
from django.http.request import split_domain_port, validate_host


@register()
def check_shortlink_host(app_configs: Any = None, **kwargs: Any) -> list[Error]:
    host = settings.SHORTLINK_HOST
    if not host:
        return []  # shortener is off
    # SHORTLINK_HOST must be a BARE hostname. The middleware matches the request's
    # Host domain against it exactly, so a port, scheme, path, or leading/trailing
    # dot would pass ALLOWED_HOSTS (validate_host is wildcard-aware) yet never match
    # at request time, silently killing the shortener. Reject it here, at boot, with
    # a clear message instead. Fail fast on the whole non-bare class before the
    # ALLOWED_HOSTS / API-host checks, which assume a bare host.
    host_lower = host.lower()
    host_domain, host_port = split_domain_port(host_lower)
    if host_port or host_domain != host_lower or host_domain.startswith("."):
        return [
            Error(
                f"SHORTLINK_HOST {host!r} must be a bare hostname: no scheme, port, path, or leading/trailing dot.",
                hint="Set SHORTLINK_HOST to just the domain, e.g. 'mgpie.ai' (put any wildcard in ALLOWED_HOSTS).",
                id="links.E003",
            )
        ]
    errors: list[Error] = []
    # validate_host honors "*", leading-dot subdomain wildcards, and case, so a
    # legal ALLOWED_HOSTS entry (e.g. ".mgpie.ai") isn't mistaken for a miss.
    if not validate_host(host_domain, settings.ALLOWED_HOSTS):
        errors.append(
            Error(
                f"SHORTLINK_HOST {host!r} is not in ALLOWED_HOSTS; every short-host request would be "
                f"rejected with DisallowedHost.",
                hint="Add SHORTLINK_HOST to ALLOWED_HOSTS (an exact entry, or a leading-dot wildcard like '.mgpie.ai').",
                id="links.E001",
            )
        )
    # urlparse().hostname is already lowercased; compare against the port-stripped host.
    api_host = urlparse(settings.BASE_URL).hostname
    if api_host and host_domain == api_host:
        errors.append(
            Error(
                f"SHORTLINK_HOST {host!r} equals the API host; it would swap the urlconf on every "
                f"request and 404 the entire /v1 API.",
                hint="Serve the shortener on its own hostname, distinct from BASE_URL's host.",
                id="links.E002",
            )
        )
    return errors
