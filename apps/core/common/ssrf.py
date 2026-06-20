"""Outbound-destination SSRF check, settings-agnostic.

`destination_block_reason` returns a human reason an http(s) URL is
unsafe to call under the given flags, or None if it's allowed. Flags
(not settings) are passed in so the helper is pure and testable ; the
caller reads the relevant settings.

Used by the webhook action at TWO seams: write-time (`watches.policy`,
`resolve_dns=False` ; catch an operator pasting a private IP literal) and
send-time (the impl, `resolve_dns=True` ; re-resolve the hostname so a
public name that points at an internal address, or DNS that changed since
create, is still caught). Mirrors the split `feeds` uses for source URLs.
"""

import ipaddress
import socket
from urllib.parse import urlsplit


def ip_is_blocked(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """An address in a range that must never be an outbound target."""
    return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified


def destination_block_reason(
    url: str,
    *,
    require_https: bool,
    block_private_ips: bool,
    resolve_dns: bool,
) -> str | None:
    """Why `url` is blocked, or None if allowed.

    - `require_https`: reject any non-https scheme.
    - `block_private_ips`: reject a host that IS, or (when `resolve_dns`)
      RESOLVES TO, a private / loopback / link-local / multicast /
      reserved / unspecified address.
    - `resolve_dns`: do a DNS lookup for hostnames (send-time). When False
      (write-time) only IP-literal hosts are checked, since DNS can change
      between create and send."""
    parts = urlsplit(url)
    if require_https and parts.scheme != "https":
        return "scheme must be https"
    if not block_private_ips:
        return None
    host = parts.hostname
    if not host:
        return None
    try:
        return (
            f"host is a blocked address ({ipaddress.ip_address(host)})"
            if ip_is_blocked(ipaddress.ip_address(host))
            else None
        )
    except ValueError:
        pass  # a hostname, not an IP literal
    if not resolve_dns:
        return None
    try:
        infos = socket.getaddrinfo(host, parts.port, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        return f"host {host!r} could not be resolved"
    for info in infos:
        addr = info[4][0]
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            continue
        if ip_is_blocked(ip):
            return f"host {host!r} resolves to a blocked address ({ip})"
    return None
