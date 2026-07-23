"""Host-swap middleware for the short-link domain.

When a request arrives on SHORTLINK_HOST, point it at `links.urls` so a bare
`<code>` resolves to the redirect view at the domain root. Every other host is
untouched, so short codes can never shadow the main API's routes. A no-op when
SHORTLINK_HOST is unset (the shortener is off by default).
"""

from django.conf import settings
from django.http.request import split_domain_port


class ShortLinkHostMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        host = settings.SHORTLINK_HOST
        if host:
            # SHORTLINK_HOST is a bare hostname (enforced by the links.E003 boot
            # check), so compare it lowercased against the request's domain. get_host()
            # keeps the Host header's case and any non-default port, so normalize the
            # REQUEST side via split_domain_port (which also handles IPv6 literals like
            # [::1]:8000) before the case-insensitive compare.
            want = host.lower()
            got, _ = split_domain_port(request.get_host())
            if got == want:
                request.urlconf = "links.urls"
        return self.get_response(request)
