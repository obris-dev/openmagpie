"""SSRF-safe HTTP client with pinned-IP DNS resolution.

Closes the DNS-rebinding TOCTOU in the plain block-then-fetch pattern (resolve +
check, then httpx independently re-resolves and connects): here a hostname is
resolved ONCE, the resolved IP is validated against `common.ssrf.ip_is_blocked`,
and the connection is made to THAT IP, so httpx never re-resolves the name. TLS
SNI + certificate verification still use the original hostname (httpcore drives
`start_tls` with it), so HTTPS verification is unchanged. Re-pins on every
redirect hop (each hop is a fresh connection through the backend).

Used for fetches of UNTRUSTED URLs (the relevance engine's lazy external-link
article fetch). The block here is unconditional (a submitter's link is never
trusted), unlike the operator-gated `validate_request_url` used for RSS feeds.

Wiring note: httpx.HTTPTransport exposes no `network_backend`, so we subclass
httpcore's SyncBackend and swap it onto the transport's connection pool. Both
touch httpcore internals, so apps/core bounds httpcore to 1.x (a 2.x can't be
resolved and silently break pinning); the TLS-against-hostname behavior was
verified against a real HTTPS host.
"""

import ipaddress
import socket
from typing import Any

import httpx
from httpcore import ConnectError, ConnectionPool, NetworkStream
from httpcore._backends.sync import SyncBackend

from common.ssrf import ip_is_blocked


class SsrfBlocked(Exception):
    """The pinned-IP guard refused to connect to a blocked address."""


class _PinnedBackend(SyncBackend):
    """SyncBackend that resolves the host once, validates the IP, and connects to
    that pinned IP (so httpx can't re-resolve). TLS is left to httpcore, which
    uses the original hostname for SNI + cert verification."""

    def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Any = None,
    ) -> NetworkStream:
        try:
            ipaddress.ip_address(host)
            target = host  # already an IP literal: no DNS lookup
        except ValueError:
            try:
                infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
            except socket.gaierror as exc:
                # DNS failure -> an httpcore transport error (httpx maps it to
                # httpx.ConnectError), so fetch_url_safely's documented "httpx.* on
                # transport errors" contract holds instead of leaking socket.gaierror.
                raise ConnectError(str(exc)) from exc
            target = str(infos[0][4][0])  # sockaddr[0] is the address string
        # INVARIANT: validate and connect to EXACTLY `target` (one resolved IP).
        # Do NOT add a happy-eyeballs / multi-address fallback that tries
        # infos[1..] without re-running ip_is_blocked per candidate -- that would
        # connect to an unvalidated address and reopen the SSRF hole.
        if ip_is_blocked(ipaddress.ip_address(target)):
            raise SsrfBlocked(f"blocked connection target {target} (host {host!r})")
        return super().connect_tcp(
            target, port, timeout=timeout, local_address=local_address, socket_options=socket_options
        )


def pinned_transport(**kwargs: Any) -> httpx.HTTPTransport:
    """An httpx transport whose connections resolve-once + validate + pin the IP
    (and re-pin per redirect hop). `kwargs` pass through to HTTPTransport (e.g.
    `verify`)."""
    transport = httpx.HTTPTransport(**kwargs)
    # httpx.HTTPTransport exposes no `network_backend`, so swap it on the pool
    # directly (httpcore internals; apps/core bounds httpcore to 1.x). `_pool` is typed as a
    # union (ConnectionPool | SOCKSProxy); a plain (proxy-less) transport always
    # uses ConnectionPool -- narrow to it so the assignment is typed AND that
    # invariant is enforced at runtime.
    pool = transport._pool
    if not isinstance(pool, ConnectionPool):  # pragma: no cover - proxy-less transport always uses ConnectionPool
        raise RuntimeError(f"cannot pin IP: expected ConnectionPool, got {type(pool).__name__}")
    pool._network_backend = _PinnedBackend()
    return transport
