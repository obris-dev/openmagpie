"""A bower connect's outbound calls: pinned to the checked address when private
addresses are blocked, refused when the pinned lookup lands on one, and only
OpenBower's own refusal words relayed."""

from unittest import mock

import httpx
from django.test import SimpleTestCase, override_settings

from common.safe_http import SsrfBlocked
from watches.services import _bower_http
from watches.services._bower_errors import BowerConnectError
from watches.services.bower_connect import RELAYED_DETAIL_MAX_LENGTH, _detail

URL = "https://api.example.io/v1/signals"


class BowerHttpTests(SimpleTestCase):
    def _transport_used(self) -> object:
        with (
            mock.patch.object(_bower_http, "pinned_transport", return_value="pinned"),
            mock.patch.object(_bower_http.httpx, "Client") as client,
        ):
            _bower_http.get(URL, field="webhook_url")
        return client.call_args.kwargs["transport"]

    @override_settings(WEBHOOK_BLOCK_PRIVATE_IPS=True)
    def test_blocking_private_addresses_pins_the_checked_address(self) -> None:
        # FAILS if the call resolves the host again after the check.
        self.assertEqual(self._transport_used(), "pinned")

    @override_settings(WEBHOOK_BLOCK_PRIVATE_IPS=False)
    def test_allowing_private_addresses_needs_no_pin(self) -> None:
        # A local OpenBower is a private address; pinning would refuse it.
        self.assertIsNone(self._transport_used())

    @override_settings(WEBHOOK_BLOCK_PRIVATE_IPS=True)
    def test_a_pinned_lookup_landing_on_a_blocked_address_is_refused(self) -> None:
        with (
            mock.patch.object(_bower_http.httpx.Client, "request", side_effect=SsrfBlocked("10.0.0.1")),
            self.assertRaises(BowerConnectError) as raised,
        ):
            _bower_http.post(URL, field="api_url", json={})
        self.assertIn("api_url rejected", str(raised.exception))
        self.assertEqual(raised.exception.status, 400)


class RelayedDetailTests(SimpleTestCase):
    def _response(self, body: object) -> httpx.Response:
        return httpx.Response(400, json=body, request=httpx.Request("POST", URL))

    def test_only_openbower_s_own_words_are_relayed(self) -> None:
        # FAILS if any other body is echoed back to the caller.
        self.assertEqual(_detail(self._response({"internal": "value"})), "Bad Request")
        self.assertEqual(_detail(self._response({"detail": "Invalid API key."})), "Invalid API key.")

    def test_a_long_detail_is_cut(self) -> None:
        relayed = _detail(self._response({"detail": "x" * (RELAYED_DETAIL_MAX_LENGTH + 10)}))
        self.assertEqual(len(relayed), RELAYED_DETAIL_MAX_LENGTH)
