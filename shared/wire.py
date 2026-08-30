"""
Length-prefixed send/recv helpers for the wire protocol.
Used by both chunk transfer and event messaging.
Every message: 4 bytes big-endian length + that many bytes of UTF-8 JSON.
"""

import socket
import json


def send_msg(sock: socket.socket, obj: dict):
    """Send a JSON-serialisable dict as a length-prefixed message."""
    data = json.dumps(obj).encode()
    sock.sendall(len(data).to_bytes(4, "big") + data)


def recv_msg(sock: socket.socket) -> dict:
    """Receive a length-prefixed JSON message and return the parsed dict."""
    length = int.from_bytes(_recv_exact(sock, 4), "big")
    return json.loads(_recv_exact(sock, length))


def _recv_exact(sock: socket.socket, n: int) -> bytes:
    """Read exactly *n* bytes from *sock*, raising on premature close."""
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("peer closed early")
        buf += chunk
    return buf
