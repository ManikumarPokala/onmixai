"""The test network guard (installed session-wide in conftest) permits local endpoints and
blocks everything else — so an accidental real-service call in any test fails fast and named."""

import os
import socket

import pytest

from tests.netguard import BlockedNetworkError


def test_loopback_connect_is_allowed() -> None:
    server = socket.socket()
    client = socket.socket()
    try:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        client.connect(("127.0.0.1", server.getsockname()[1]))  # testcontainers/stub live here
    finally:
        client.close()
        server.close()


def test_non_local_connect_is_blocked_and_named() -> None:
    # 192.0.2.0/24 is TEST-NET-1 (RFC 5737) — never routed; the guard raises before any connect.
    s = socket.socket()
    try:
        with pytest.raises(BlockedNetworkError) as exc:
            s.connect(("192.0.2.1", 443))
        assert "192.0.2.1" in str(exc.value)
    finally:
        s.close()


def test_connect_ex_is_also_guarded() -> None:
    s = socket.socket()
    try:
        with pytest.raises(BlockedNetworkError):
            s.connect_ex(("192.0.2.1", 443))
    finally:
        s.close()


@pytest.mark.skipif(not hasattr(socket, "AF_UNIX"), reason="AF_UNIX unavailable on this platform")
def test_unix_socket_is_allowed() -> None:
    # The Docker daemon socket is AF_UNIX — must be permitted (testcontainers uses it). Use a
    # short /tmp path (AF_UNIX sun_path is ~104 bytes; pytest's tmp_path can exceed that).
    path = f"/tmp/og-netguard-{os.getpid()}.sock"
    if os.path.exists(path):
        os.unlink(path)
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        server.bind(path)
        server.listen(1)
        client.connect(path)  # must NOT raise
    finally:
        client.close()
        server.close()
        if os.path.exists(path):
            os.unlink(path)
