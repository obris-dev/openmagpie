"""Short-link redirect endpoints.

Served at the short domain's ROOT via ShortLinkHostMiddleware (which swaps
request.urlconf to links.urls for the SHORTLINK_HOST), not under /v1.
"""

from django.conf import settings
from django.http import Http404, HttpRequest, HttpResponseRedirect
from django.views.decorators.http import require_http_methods

from .services import ShortLinkService


@require_http_methods(["GET", "HEAD"])
def shortlink_redirect(request: HttpRequest, code: str) -> HttpResponseRedirect:
    """Resolve a code and 302 to its destination; an unknown code is a 404. A
    302 (not 301) keeps browsers from caching it, so clicks keep being counted
    and the destination stays editable. HEAD is allowed (link unfurlers / uptime
    probes send it) but does NOT record a click, so bot unfurls don't inflate totals.
    """
    link = ShortLinkService.find_by_code(code)
    if link is None:
        raise Http404("unknown short link")
    if request.method == "GET":
        ShortLinkService.record_click(short_link=link, request=request)
    return HttpResponseRedirect(link.url)


@require_http_methods(["GET", "HEAD"])
def shortlink_root(request: HttpRequest) -> HttpResponseRedirect:
    """The bare short domain (no code): send visitors to the product app."""
    return HttpResponseRedirect(settings.APP_BASE_URL)
