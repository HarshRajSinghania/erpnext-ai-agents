"""Where the server may be told to send a request: a guard against being used to reach the machine itself or the private network (SSRF).

A hostname is resolved and EVERY address it resolves to is checked, so a public-looking name that points at 127.0.0.1 or 169.254.169.254 (a cloud metadata service)
is refused too. Callers decide which kinds they accept: a hosted model provider must be public; an Ollama server may be on this computer or the local network,
but never on a link-local address.
"""
from __future__ import annotations

import ipaddress
import socket
from typing import Callable, Optional
from urllib.parse import urlparse

Resolver = Callable[..., list]


def no_redirects(session: object) -> object:
    """A requests session that carries a credential (an API key, a bot token, a bearer token) must never follow a redirect: the server it was sent to could
    point it at another host, and custom key headers (x-api-key and the like) are not stripped by requests on the way. A redirect then fails instead."""
    try:
        session.max_redirects = 0          # type: ignore[attr-defined]
    except AttributeError:
        pass
    return session


def _kind(ip: ipaddress._BaseAddress) -> Optional[str]:
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:          # ::ffff:127.0.0.1 is 127.0.0.1
        ip = ip.ipv4_mapped
    if ip.is_loopback:
        return "loopback"
    if ip.is_link_local:
        return "link-local"
    if ip.is_unspecified or ip.is_multicast or ip.is_reserved:
        return "reserved"
    if ip.is_private or not ip.is_global:
        return "private"
    return None


def blocked_target(url: str, refuse: tuple[str, ...] = ("loopback", "link-local", "private", "reserved"), resolve: Optional[Resolver] = None) -> Optional[str]:
    """None when `url` may be contacted; otherwise why not, in words. `refuse` lists the kinds of address that are not allowed."""
    host = urlparse(url).hostname
    if not host:
        return "the address has no host name"
    try:
        literal = ipaddress.ip_address(host)
        found = [literal]
    except ValueError:
        try:
            infos = (resolve or socket.getaddrinfo)(host, None)
        except (OSError, UnicodeError):
            return f"the host name {host} could not be found"
        found = []
        for info in infos:
            try:
                found.append(ipaddress.ip_address(info[4][0].split("%")[0]))
            except (ValueError, IndexError, TypeError):
                continue
        if not found:
            return f"the host name {host} could not be found"
    for ip in found:
        kind = _kind(ip)
        if kind in refuse:
            return f"{host} points to a {kind} address ({ip}), which is not allowed here"
    return None
