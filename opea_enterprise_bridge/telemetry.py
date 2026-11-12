"""
Telemetry sink for the OPEA enterprise bridge.

Provides a lightweight, non-blocking telemetry sink that "reports" events to an
OPEA collector in a background daemon thread. Every emission is fire-and-forget
and fully swallowed — telemetry can never block or crash the main application.

Reference: https://opea.dev/
"""

import logging
import threading
from collections import deque

from .models import OpeaTelemetryEvent, to_dict

log = logging.getLogger("opea.bridge.telemetry")


class TelemetrySink:
    """In-memory, background-flushing telemetry collector.

    By default it is a fire-and-forget sink: events are buffered locally and
    flushed on a background thread, so there is zero network cost and zero
    failure surface on the hot path.
    """

    def __init__(self, enabled=True, max_events=1024):
        self.enabled = enabled
        self._events = deque(maxlen=max_events)
        self._lock = threading.Lock()

    def emit(self, event):
        """Emit a telemetry event without ever raising."""
        if not self.enabled:
            return None
        try:
            record = event if isinstance(event, OpeaTelemetryEvent) else OpeaTelemetryEvent(**event)
            with self._lock:
                self._events.append(to_dict(record))
            return record
        except Exception as exc:  # noqa: BLE001 - telemetry is best-effort
            log.debug("OPEA telemetry emit skipped: %s", exc)
            return None

    def flush_async(self):
        """Flush buffered events in a background daemon thread (fire-and-forget)."""
        try:
            threading.Thread(target=self._flush, daemon=True).start()
        except Exception as exc:  # noqa: BLE001
            log.debug("OPEA telemetry flush skipped: %s", exc)

    def _flush(self):
        try:
            with self._lock:
                events = list(self._events)
                self._events.clear()
            # Events are buffered locally; a deployment can wire this sink to
            # the OPEA telemetry collector.
            log.debug("OPEA telemetry flushed %d event(s) (sink)", len(events))
        except Exception as exc:  # noqa: BLE001
            log.debug("OPEA telemetry flush error: %s", exc)

    def snapshot(self):
        with self._lock:
            return list(self._events)

    def __len__(self):
        with self._lock:
            return len(self._events)


__all__ = ["TelemetrySink"]
