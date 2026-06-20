"""_PinnedBackend: resolve the host once, validate the IP, and connect to that
pinned IP (so httpx can't re-resolve). Unit-tested without network by mocking
getaddrinfo + the parent connect_tcp; TLS-against-the-hostname-while-connected-
to-the-IP was verified separately against a real HTTPS host."""

from __future__ import annotations

from unittest import mock

from django.test import SimpleTestCase
from httpcore._backends.sync import SyncBackend

from common.safe_http import SsrfBlocked, _PinnedBackend

_MOD = "common.safe_http"


class PinnedBackendTests(SimpleTestCase):
    def test_blocks_a_host_that_resolves_to_a_private_ip(self) -> None:
        # The rebinding case: the single resolution returns a private IP -> refuse.
        with (
            mock.patch(f"{_MOD}.socket.getaddrinfo", return_value=[(2, 1, 6, "", ("10.0.0.5", 80))]),
            self.assertRaises(SsrfBlocked),
        ):
            _PinnedBackend().connect_tcp("rebind.evil.test", 80)

    def test_blocks_a_private_ip_literal(self) -> None:
        with self.assertRaises(SsrfBlocked):
            _PinnedBackend().connect_tcp("169.254.169.254", 80)  # link-local (cloud metadata)

    def test_connects_to_the_resolved_ip_not_the_hostname(self) -> None:
        # The pin: we connect to the validated IP, not the name (no re-resolution).
        with (
            mock.patch(f"{_MOD}.socket.getaddrinfo", return_value=[(2, 1, 6, "", ("93.184.216.34", 80))]),
            mock.patch.object(SyncBackend, "connect_tcp", return_value="STREAM") as parent,
        ):
            out = _PinnedBackend().connect_tcp("example.com", 80)
        self.assertEqual(out, "STREAM")
        self.assertEqual(parent.call_args.args[0], "93.184.216.34")
