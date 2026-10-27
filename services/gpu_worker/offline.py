"""No network for a proof (make catalog-proof): with MICROMIND_REFUSE_NETWORK=1, every connection from this process to
anything but this Mac itself fails at once. The proof sets it for its own process, the GPU workers it starts (boot.serve)
and micro-sam's own Python (microsam_server.py), so a model that runs there ran from its pinned files alone: a library
that reached for the Hub or a download would fail, never quietly fetch.

Standard library only, so each of those processes can install it before anything else is imported.
"""
from __future__ import annotations

import ipaddress
import os
import socket

ENV = "MICROMIND_REFUSE_NETWORK"


class NetworkRefused(OSError):
    pass


def _local(address) -> bool:
    if not isinstance(address, tuple) or not address:
        return True  # a Unix socket, a pipe: on this Mac
    host = str(address[0])
    if host in ("localhost", ""):
        return True
    try:
        return ipaddress.ip_address(host.split("%")[0]).is_loopback
    except ValueError:
        return False  # a name: resolving it is reaching out


def refuse(environ=os.environ) -> bool:
    """Install the refusal when ENV says so (returns whether it did). Idempotent."""
    if environ.get(ENV) != "1" or getattr(socket.socket.connect, "_micromind_refused", False):
        return environ.get(ENV) == "1"
    original_connect, original_connect_ex = socket.socket.connect, socket.socket.connect_ex
    original_getaddrinfo = socket.getaddrinfo

    def connect(self, address):
        if not _local(address):
            raise NetworkRefused(f"the network is refused here ({ENV}): {address[0]}")
        return original_connect(self, address)

    def connect_ex(self, address):
        if not _local(address):
            raise NetworkRefused(f"the network is refused here ({ENV}): {address[0]}")
        return original_connect_ex(self, address)

    def getaddrinfo(host, *args, **kwargs):
        if host not in (None, "localhost", "") and not _local((host,)):
            raise NetworkRefused(f"the network is refused here ({ENV}): {host}")
        return original_getaddrinfo(host, *args, **kwargs)

    connect._micromind_refused = True
    socket.socket.connect, socket.socket.connect_ex, socket.getaddrinfo = connect, connect_ex, getaddrinfo
    return True
