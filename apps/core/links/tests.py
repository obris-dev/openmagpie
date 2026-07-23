"""Tests for the links URL shortener: minting, host-scoped redirect, and
deduplicated click stats.
"""

from django.conf import settings
from django.core.cache import caches
from django.test import TestCase, override_settings

from links.checks import check_shortlink_host
from links.constants import CLICK_DEDUP_CACHE_ALIAS
from links.models import ClickEvent
from links.services import ShortLinkService
from links.services.short_link import _hash_ip, _referer_origin

_SHORT_HOST = "mgpie.ai"
# The redirect tests exercise the per-visitor click dedup, which needs a real
# cache; the test DB has no db-cache table, so pin an in-memory backend (mirrors
# waitlist/tests.py's throttle setup).
_LOCMEM = {
    "default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "test-default"},
    "clickdedup": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "test-clickdedup"},
}


class ShortLinkServiceTests(TestCase):
    def test_create_generates_code_and_stores_url(self) -> None:
        link = ShortLinkService.create(url="https://example.com/dest")
        self.assertEqual(len(link.code), 6)
        self.assertEqual(link.url, "https://example.com/dest")

    def test_create_with_custom_code(self) -> None:
        link = ShortLinkService.create(url="https://example.com/dest", code="demo")
        self.assertEqual(link.code, "demo")

    def test_create_rejects_taken_code(self) -> None:
        ShortLinkService.create(url="https://example.com/a", code="demo")
        with self.assertRaises(ValueError):
            ShortLinkService.create(url="https://example.com/b", code="demo")

    def test_taken_code_does_not_poison_transaction(self) -> None:
        # The caught IntegrityError on a duplicate code must roll back only that
        # insert (savepoint), not poison the enclosing transaction: a following
        # insert still succeeds.
        ShortLinkService.create(url="https://example.com/a", code="demo")
        with self.assertRaises(ValueError):
            ShortLinkService.create(url="https://example.com/b", code="demo")
        link = ShortLinkService.create(url="https://example.com/c", code="other")
        self.assertEqual(link.code, "other")

    def test_create_rejects_bad_slug(self) -> None:
        with self.assertRaises(ValueError):
            ShortLinkService.create(url="https://example.com/a", code="bad/slug")

    def test_create_rejects_empty_url(self) -> None:
        with self.assertRaises(ValueError):
            ShortLinkService.create(url="   ")

    def test_create_rejects_non_http_url(self) -> None:
        for bad in ("example.com/x", "javascript:alert(1)", "/relative/path", "ftp://host/file"):
            with self.assertRaises(ValueError):
                ShortLinkService.create(url=bad)

    def test_stats_excludes_empty_ip_hash_from_unique(self) -> None:
        # IP-less clicks (blank ip_hash) count toward total but must not fold into
        # a single phantom "unique" visitor.
        link = ShortLinkService.create(url="https://example.com/x")
        ClickEvent.objects.create(short_link_id=link.id, ip_hash="", country="US")
        ClickEvent.objects.create(short_link_id=link.id, ip_hash="", country="US")
        stats = ShortLinkService.stats(link)
        self.assertEqual(stats.total, 2)
        self.assertEqual(stats.unique, 0)

    def test_delete_removes_link_and_its_click_events(self) -> None:
        link = ShortLinkService.create(url="https://example.com/x", code="gone")
        ClickEvent.objects.create(short_link_id=link.id, ip_hash="h", country="US")
        self.assertTrue(ShortLinkService.delete("gone"))
        self.assertIsNone(ShortLinkService.find_by_code("gone"))
        self.assertEqual(ClickEvent.objects.filter(short_link_id=link.id).count(), 0)

    def test_delete_missing_code_returns_false(self) -> None:
        self.assertFalse(ShortLinkService.delete("nope"))

    def test_referer_origin_strips_userinfo_and_keeps_ipv6(self) -> None:
        cases = {
            "https://user:pass@evil.com:8443/s?token=abc": "https://evil.com:8443",  # userinfo dropped
            "http://[::1]:8080/p": "http://[::1]:8080",  # IPv6 brackets preserved (with port)
            "http://[::1]/p": "http://[::1]",  # IPv6 brackets preserved (no port)
            "https://EVIL.COM/x": "https://evil.com",  # host lowercased
            "/relative": "",
            "": "",
        }
        for referer, expected in cases.items():
            self.assertEqual(_referer_origin(referer), expected, referer)

    def test_clickdedup_cache_alias_is_configured(self) -> None:
        # The service reads caches[CLICK_DEDUP_CACHE_ALIAS]; a missing/renamed settings
        # CACHES key would fail open (silently disabling dedup), so pin the wiring. This
        # case does NOT override CACHES, so it checks the real settings dict.
        self.assertIn(CLICK_DEDUP_CACHE_ALIAS, settings.CACHES)


@override_settings(
    SHORTLINK_HOST=_SHORT_HOST,
    ALLOWED_HOSTS=[_SHORT_HOST, "testserver"],
    CACHES=_LOCMEM,
    SHORTLINK_TRUST_CF_HEADERS=True,
)
class ShortLinkRedirectTests(TestCase):
    def setUp(self) -> None:
        caches["clickdedup"].clear()  # LocMemCache is process-global; keep the dedup window per-test
        self.link = ShortLinkService.create(url="https://example.com/some/long/destination", code="demo")

    def test_redirect_on_short_host(self) -> None:
        resp = self.client.get("/demo", HTTP_HOST=_SHORT_HOST, HTTP_CF_CONNECTING_IP="1.2.3.4", HTTP_CF_IPCOUNTRY="US")
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp["Location"], "https://example.com/some/long/destination")
        self.assertEqual(ClickEvent.objects.filter(short_link_id=self.link.id).count(), 1)

    def test_repeat_hits_dedup_to_one_row_per_visitor(self) -> None:
        # A visitor refreshing 3x inside the window writes ONE row (deduped total);
        # a second distinct visitor adds one. total == unique here because every
        # repeat from the same IP collapses within the dedup window.
        for _ in range(3):
            self.client.get("/demo", HTTP_HOST=_SHORT_HOST, HTTP_CF_CONNECTING_IP="1.2.3.4", HTTP_CF_IPCOUNTRY="US")
        self.client.get("/demo", HTTP_HOST=_SHORT_HOST, HTTP_CF_CONNECTING_IP="9.9.9.9", HTTP_CF_IPCOUNTRY="GB")
        stats = ShortLinkService.stats(self.link)
        self.assertEqual(stats.total, 2)
        self.assertEqual(stats.unique, 2)
        self.assertEqual(stats.by_country, {"US": 1, "GB": 1})

    @override_settings(SHORTLINK_TRUST_CF_HEADERS=False)
    def test_cf_headers_ignored_when_untrusted(self) -> None:
        # Off-tunnel the CF headers are forgeable: country is not read, and the IP
        # comes from REMOTE_ADDR (the test client's 127.0.0.1), not CF-Connecting-IP.
        self.client.get("/demo", HTTP_HOST=_SHORT_HOST, HTTP_CF_CONNECTING_IP="1.2.3.4", HTTP_CF_IPCOUNTRY="US")
        event = ClickEvent.objects.get(short_link_id=self.link.id)
        self.assertEqual(event.country, "")
        self.assertEqual(event.ip_hash, _hash_ip("127.0.0.1"))

    def test_referer_stored_as_origin_only(self) -> None:
        self.client.get(
            "/demo",
            HTTP_HOST=_SHORT_HOST,
            HTTP_CF_CONNECTING_IP="5.5.5.5",
            HTTP_REFERER="https://ref.example.com/secret/path?token=abc123",
        )
        event = ClickEvent.objects.get(short_link_id=self.link.id)
        self.assertEqual(event.props["ref"], "https://ref.example.com")

    def test_head_redirects_without_recording_click(self) -> None:
        resp = self.client.head("/demo", HTTP_HOST=_SHORT_HOST, HTTP_CF_CONNECTING_IP="1.2.3.4")
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp["Location"], "https://example.com/some/long/destination")
        self.assertEqual(ClickEvent.objects.filter(short_link_id=self.link.id).count(), 0)

    def test_unknown_code_is_404(self) -> None:
        resp = self.client.get("/nope", HTTP_HOST=_SHORT_HOST)
        self.assertEqual(resp.status_code, 404)

    def test_main_host_does_not_resolve_code(self) -> None:
        # On a non-short host the middleware does not swap urlconf, so the bare
        # code is not a route (host-swap isolation).
        resp = self.client.get("/demo", HTTP_HOST="testserver")
        self.assertEqual(resp.status_code, 404)

    def test_short_host_matches_with_port_and_mixed_case(self) -> None:
        # get_host() keeps the Host header's case and any non-default port, so the
        # middleware normalizes the REQUEST side (domain-only, lowercased) to still
        # swap urlconf for a `mgpie.ai:8000` / `MgPie.AI` Host against the bare
        # SHORTLINK_HOST. (A ported SHORTLINK_HOST itself is rejected at boot by E003.)
        for host_header in ("mgpie.ai:8000", "MgPie.AI"):
            resp = self.client.get("/demo", HTTP_HOST=host_header)
            self.assertEqual(resp.status_code, 302, host_header)
            self.assertEqual(resp["Location"], "https://example.com/some/long/destination", host_header)


class ShortlinkHostCheckTests(TestCase):
    def test_no_errors_when_off(self) -> None:
        with override_settings(SHORTLINK_HOST=""):
            self.assertEqual(check_shortlink_host(), [])

    def test_no_errors_when_allowed_and_distinct(self) -> None:
        with override_settings(
            SHORTLINK_HOST="mgpie.ai", ALLOWED_HOSTS=["mgpie.ai"], BASE_URL="https://api.openmagpie.ai"
        ):
            self.assertEqual(check_shortlink_host(), [])

    def test_no_error_when_covered_by_leading_dot_wildcard(self) -> None:
        # ".mgpie.ai" is a legal ALLOWED_HOSTS subdomain wildcard that Django
        # accepts for host "mgpie.ai"; validate_host must not flag it as a miss.
        with override_settings(
            SHORTLINK_HOST="mgpie.ai", ALLOWED_HOSTS=[".mgpie.ai"], BASE_URL="https://api.openmagpie.ai"
        ):
            self.assertEqual(check_shortlink_host(), [])

    def test_error_when_host_has_port(self) -> None:
        # A ported SHORTLINK_HOST would pass ALLOWED_HOSTS on its domain yet never
        # match the middleware's exact compare: reject the non-bare host at boot.
        with override_settings(
            SHORTLINK_HOST="mgpie.ai:8000", ALLOWED_HOSTS=["mgpie.ai"], BASE_URL="https://api.openmagpie.ai"
        ):
            self.assertTrue(any(e.id == "links.E003" for e in check_shortlink_host()))

    def test_error_when_host_has_leading_dot(self) -> None:
        # ".mgpie.ai" passes validate_host against a ".mgpie.ai" wildcard but the
        # middleware compares exact domains, so it would be silently dead: E003.
        with override_settings(
            SHORTLINK_HOST=".mgpie.ai", ALLOWED_HOSTS=[".mgpie.ai"], BASE_URL="https://api.openmagpie.ai"
        ):
            self.assertTrue(any(e.id == "links.E003" for e in check_shortlink_host()))

    def test_error_when_equals_api_host_case_insensitively(self) -> None:
        with override_settings(
            SHORTLINK_HOST="API.OpenMagpie.ai",
            ALLOWED_HOSTS=["*"],
            BASE_URL="https://api.openmagpie.ai",
        ):
            self.assertTrue(any(e.id == "links.E002" for e in check_shortlink_host()))

    def test_error_when_not_in_allowed_hosts(self) -> None:
        with override_settings(
            SHORTLINK_HOST="mgpie.ai", ALLOWED_HOSTS=["api.openmagpie.ai"], BASE_URL="https://api.openmagpie.ai"
        ):
            self.assertTrue(any(e.id == "links.E001" for e in check_shortlink_host()))

    def test_error_when_equals_api_host(self) -> None:
        with override_settings(
            SHORTLINK_HOST="api.openmagpie.ai",
            ALLOWED_HOSTS=["api.openmagpie.ai"],
            BASE_URL="https://api.openmagpie.ai",
        ):
            self.assertTrue(any(e.id == "links.E002" for e in check_shortlink_host()))
