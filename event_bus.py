"""
In-process event fan-out inside the Master Tracker.

Usage:
    from event_bus import bus
    bus.subscribe(my_handler)      # handler(event_dict)
    bus.publish("chunk.stored", {...})
    bus.recent(20)                 # last 20 events
"""

from collections import deque
from threading import Lock
import time


class EventBus:
    def __init__(self, history_size: int = 200):
        self._subscribers: list = []
        self._history: deque = deque(maxlen=history_size)
        self._lock = Lock()

    def subscribe(self, handler):
        """Register a handler that receives every published event."""
        self._subscribers.append(handler)

    def publish(self, event_type: str, payload: dict):
        """Publish an event to all subscribers and store in history."""
        event = {"type": event_type, "payload": payload, "ts": time.time()}
        with self._lock:
            self._history.append(event)
        for handler in self._subscribers:
            try:
                handler(event)
            except Exception:
                pass  # don't let one subscriber crash the bus

    def recent(self, n: int = 20) -> list[dict]:
        """Return the last *n* events."""
        with self._lock:
            return list(self._history)[-n:]


bus = EventBus()
