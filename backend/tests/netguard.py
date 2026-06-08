"""Test network guard. Tests must reach only LOCAL endpoints — the in-process LLM/embeddings
stubs, FakeGateway (no socket at all), and testcontainers (Postgres/Ryuk on 127.0.0.1, the
Docker daemon over its UNIX socket). A connection to any non-local address means a test is
hitting a real external service instead of the injected fake/stub — a test-isolation defect.

``install_network_guard()`` wraps ``socket.socket.connect``/``connect_ex`` so a non-local
connect fails fast with ``BlockedNetworkError`` naming the destination, instead of hanging,
retrying, or leaking a socket (which surfaces only as a flaky ResourceWarning). DNS resolution
(``getaddrinfo``) is untouched; only the connect is gated.

Permitted: AF_UNIX (the Docker daemon socket), IPv4/IPv6 loopback (127.0.0.0/8, ::1), and an
explicit ``tcp://`` ``DOCKER_HOST`` if one is configured (some CI setups). Everything else is
blocked. Idempotent: installing twice is a no-op.
"""

import os
import socket
from urllib.parse import urlparse

_AF_UNIX = getattr(socket, "AF_UNIX", None)


class BlockedNetworkError(RuntimeError):
    """Raised when a test attempts a non-local network connection."""


def _docker_tcp_host() -> str | None:
    host = os.environ.get("DOCKER_HOST", "")
    if host.startswith("tcp://"):
        return urlparse(host).hostname
    return None


def _is_local(family: int, address: object) -> bool:
    """True if ``address`` is a permitted local endpoint for socket family ``family``."""
    if _AF_UNIX is not None and family == _AF_UNIX:
        return True  # the Docker daemon socket
    if not isinstance(address, tuple) or not address:
        return True  # non-inet target (e.g. AF_UNIX path handled above) — don't interfere
    host = address[0]
    if not isinstance(host, str):
        return True
    if host in {"127.0.0.1", "::1", "localhost", "0.0.0.0", "::", ""} or host.startswith("127."):
        return True
    return host == _docker_tcp_host()


def install_network_guard() -> None:
    """Install the connect guard on ``socket.socket`` (idempotent)."""
    if getattr(socket.socket, "_onmixai_guarded", False):
        return
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex

    def _blocked(address: object) -> BlockedNetworkError:
        return BlockedNetworkError(
            f"Test attempted a non-local network connection to {address!r}. Tests must use the "
            "in-process stub / FakeGateway / testcontainers — inject the fake instead of "
            "reaching a real service."
        )

    def guarded_connect(self: socket.socket, address: object, *a: object, **k: object) -> object:
        if not _is_local(self.family, address):
            self.close()  # close before raising so the offender doesn't also leak a socket
            raise _blocked(address)
        return real_connect(self, address, *a, **k)  # type: ignore[arg-type]

    def guarded_connect_ex(self: socket.socket, address: object, *a: object, **k: object) -> object:
        if not _is_local(self.family, address):
            self.close()
            raise _blocked(address)
        return real_connect_ex(self, address, *a, **k)  # type: ignore[arg-type]

    socket.socket.connect = guarded_connect  # type: ignore[method-assign,assignment]
    socket.socket.connect_ex = guarded_connect_ex  # type: ignore[method-assign,assignment]
    socket.socket._onmixai_guarded = True  # type: ignore[attr-defined]
