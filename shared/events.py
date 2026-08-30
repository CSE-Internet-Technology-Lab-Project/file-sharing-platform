"""
Cross-process event transport over plain TCP sockets.
Data Keepers use send_event() to push events to the Master Tracker.
The Master Tracker uses start_event_listener() to receive them.
"""

import socket
import threading
from .wire import send_msg, recv_msg


def send_event(host: str, port: int, event_type: str, payload: dict, timeout: float = 2):
    """Fire-and-forget: send a single event to the tracker's event listener."""
    try:
        with socket.create_connection((host, port), timeout=timeout) as s:
            send_msg(s, {"type": event_type, "payload": payload})
    except OSError:
        pass  # tracker unreachable this tick — heartbeats retry every second anyway


def start_event_listener(port: int, dispatch_fn):
    """
    Start a TCP server on *port* that accepts one-shot event connections.
    Each connection delivers exactly one JSON message, which is passed to
    dispatch_fn(event_type, payload).
    """
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("0.0.0.0", port))
    srv.listen(32)

    def loop():
        while True:
            conn, _ = srv.accept()
            threading.Thread(target=_handle, args=(conn, dispatch_fn), daemon=True).start()

    threading.Thread(target=loop, daemon=True).start()
    return srv


def _handle(conn, dispatch_fn):
    with conn:
        try:
            msg = recv_msg(conn)
            dispatch_fn(msg["type"], msg["payload"])
        except (ConnectionError, KeyError):
            pass
