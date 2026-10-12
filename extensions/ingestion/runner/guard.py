"""Refuse source addresses on the platform's side of the network.

The run sits on an egress network for its source and on an internal network with only the catalog
and storage. Every name resolution goes through `getaddrinfo`, so wrapping it refuses loopback,
private, link-local (cloud metadata at 169.254.169.254 included), multicast and reserved addresses
for any host but the catalog and storage aliases. Callers connect to the vetted results only, so a
name that resolves differently a second time (DNS rebinding) cannot slip through.
"""

import ipaddress
import socket

PLATFORM = frozenset({"polaris-control-plane", "rustfs"})
HOST = "host.docker.internal"
_system = socket.getaddrinfo
_original = _system


class BlockedAddress(OSError):
    pass


def public(address):
    ip = ipaddress.ip_address(address.split("%", 1)[0])
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


def install(allow_private=False):
    def getaddrinfo(host, *args, **kwargs):
        results = _original(host, *args, **kwargs)
        name = host.decode() if isinstance(host, bytes) else str(host or "")
        if name.lower().rstrip(".") in PLATFORM or allow_private:
            return results
        if name.lower().rstrip(".") == HOST:
            raise BlockedAddress(f"{name} is only reachable when the platform allows private sources.")
        blocked = [r for r in results if r[0] in (socket.AF_INET, socket.AF_INET6) and not public(r[4][0])]
        if blocked:
            raise BlockedAddress(f"{name} resolves to a private or reserved address; the source must be public.")
        return results

    socket.getaddrinfo = getaddrinfo


def uninstall():
    socket.getaddrinfo = _system
