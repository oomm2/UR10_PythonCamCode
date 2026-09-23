#!/usr/bin/env python3
"""Server-Sent Events fan-out for the read-only UR monitor.

Two cooperating pieces:

* ``EventStreamBroker`` keeps a set of per-subscriber bounded queues. Every
  dashboard gets its own queue, so one slow browser cannot stall the RTDE
  reader thread or any other viewer. That fan-out is exactly what makes
  multiple simultaneous clients work.
* ``StatePublisher`` samples the shared state on a fixed cadence and hands each
  snapshot to the broker, but only while at least one client is listening.
"""
from __future__ import annotations

import json
import logging
import queue
import threading
from typing import Any

LOGGER = logging.getLogger(__name__)


class EventStreamBroker:
    """Fan out state snapshots to SSE subscribers without blocking the poller.

    Each subscriber owns a bounded queue. A slow client that falls behind is
    dropped from that broadcast instead of stalling every other dashboard, which
    keeps the RTDE reader thread independent of browser performance.
    """

    def __init__(self, max_queue: int = 8) -> None:
        self._lock = threading.Lock()
        self._subscribers: set[queue.Queue[str]] = set()
        self._max_queue = max(1, int(max_queue))
        self._dropped = 0

    def subscribe(self) -> queue.Queue[str]:
        subscriber: queue.Queue[str] = queue.Queue(maxsize=self._max_queue)
        with self._lock:
            self._subscribers.add(subscriber)
        return subscriber

    def unsubscribe(self, subscriber: queue.Queue[str]) -> None:
        with self._lock:
            self._subscribers.discard(subscriber)

    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subscribers)

    def stats(self) -> dict[str, Any]:
        with self._lock:
            return {
                "subscribers": len(self._subscribers),
                "max_queue": self._max_queue,
                "dropped_samples": self._dropped,
            }

    def publish(self, payload: dict[str, Any]) -> None:
        try:
            message = json.dumps(payload, separators=(",", ":"), allow_nan=False)
        except (TypeError, ValueError):
            return
        with self._lock:
            subscribers = list(self._subscribers)
        for subscriber in subscribers:
            try:
                subscriber.put_nowait(message)
            except queue.Full:
                # Drop the oldest sample so the client still makes forward progress.
                try:
                    subscriber.get_nowait()
                    subscriber.put_nowait(message)
                except queue.Empty:
                    pass
                with self._lock:
                    self._dropped += 1


class StatePublisher(threading.Thread):
    """Publish a rate-limited snapshot to SSE subscribers.

    ``snapshot`` may be a plain callable returning the payload to broadcast; the
    handler uses that to fold in the controller and latency sections that do not
    live in the raw state store.
    """

    def __init__(
        self,
        state: Any,
        broker: EventStreamBroker,
        config: dict[str, Any],
        snapshot: Any | None = None,
    ) -> None:
        super().__init__(daemon=True)
        self.state = state
        self.broker = broker
        self.interval = 1.0 / max(0.1, float(config.get("event_stream_frequency", 10.0)))
        self.snapshot_fn = snapshot
        self.publisher_name = "state-publisher"
        self.stop_event = threading.Event()

    def build_payload(self) -> dict[str, Any]:
        if self.snapshot_fn is not None:
            return self.snapshot_fn()
        return self.state.snapshot()

    def run(self) -> None:
        while not self.stop_event.wait(self.interval):
            if self.broker.subscriber_count() == 0:
                continue
            try:
                self.broker.publish(self.build_payload())
            except Exception:  # pragma: no cover - defensive, never kill the thread
                LOGGER.exception("State publisher iteration failed")
