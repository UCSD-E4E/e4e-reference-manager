"""Guard outbound fetches of user/third-party URLs against reaching internal services.

The API runs on a private network next to postgres, ollama, grobid, object storage and
the cloud metadata endpoint. Any URL that came from a user or a third party must only
be fetched from hosts whose every DNS answer is a globally routable address.
"""
from __future__ import annotations

import ipaddress
import socket

_NAT64 = ipaddress.ip_network("64:ff9b::/96")


def _is_public(addr: str) -> bool:
    try:
        ip = ipaddress.ip_address(addr.split("%", 1)[0])  # drop an IPv6 scope id
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            ip = ip.ipv4_mapped
        elif ip in _NAT64:  # NAT64 embeds an IPv4 address in the low 32 bits
            ip = ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
    return ip.is_global


def public_addresses(host: str) -> list[str]:
    """The addresses `host` resolves to, or [] unless every one is globally routable
    (also [] when it doesn't resolve). Blocking: call it in a thread."""
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except (socket.gaierror, UnicodeError, ValueError):
        return []
    addrs = list(dict.fromkeys(str(i[4][0]) for i in infos))
    return addrs if addrs and all(_is_public(a) for a in addrs) else []
