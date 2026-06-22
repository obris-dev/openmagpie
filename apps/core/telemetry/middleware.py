"""SurfaceMiddleware: tag each request with the client surface (cli / web / api).

Reads the explicit `X-Magpie-Surface` header, validated against an allowlist so
a client can't inject arbitrary surface labels into analytics. Falls back to the
`User-Agent` (`magpie-cli...` -> cli), then `api`. Sets `request.surface` for the
event helpers to read; carries no PII.
"""

from .constants import ALLOWED_SURFACES, SURFACE_HEADER, Surface


class SurfaceMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.surface = self._surface(request)
        return self.get_response(request)

    @staticmethod
    def _surface(request) -> str:
        declared = request.headers.get(SURFACE_HEADER, "").strip().lower()
        if declared in ALLOWED_SURFACES:
            return declared
        if request.headers.get("User-Agent", "").startswith("magpie-cli"):
            return Surface.CLI.value
        return Surface.API.value
