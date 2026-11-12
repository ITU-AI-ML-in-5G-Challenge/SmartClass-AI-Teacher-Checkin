"""
Iron-wall circuit breaker, hard-timeout executor, and fallback guards.

These are the fault-isolation primitives: every OPEA interaction is bounded by
an extremely short timeout (default 0.8 s), counted by a circuit breaker, and
wrapped so that no exception can ever propagate into the host application.
"""

import functools
import logging
import threading
import time

log = logging.getLogger("opea.bridge.circuit")


class CircuitOpenError(RuntimeError):
    """Raised internally when the breaker is OPEN; always swallowed by callers."""


class OpeaCircuitBreaker:
    """A classic CLOSED / OPEN / HALF_OPEN breaker with a fast reset window."""

    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"

    def __init__(self, failure_threshold=3, reset_timeout=2.0, name="opea"):
        self.name = name
        self.failure_threshold = failure_threshold
        self.reset_timeout = reset_timeout
        self._state = self.CLOSED
        self._failures = 0
        self._opened_at = 0.0
        self._lock = threading.Lock()

    @property
    def state(self):
        with self._lock:
            return self._state

    def allow_request(self):
        """Return True if a request may be attempted under the current state."""
        with self._lock:
            if self._state == self.CLOSED:
                return True
            if self._state == self.OPEN:
                if (time.monotonic() - self._opened_at) >= self.reset_timeout:
                    self._state = self.HALF_OPEN
                    return True
                return False
            return True  # HALF_OPEN lets one probe through

    def record_success(self):
        with self._lock:
            self._state = self.CLOSED
            self._failures = 0

    def record_failure(self):
        with self._lock:
            self._failures += 1
            if self._failures >= self.failure_threshold:
                self._state = self.OPEN
                self._opened_at = time.monotonic()
                log.warning("OPEA circuit '%s' OPEN after %d failures; "
                            "routing to local fallback", self.name, self._failures)

    def __repr__(self):
        return f"<OpeaCircuitBreaker {self.name} {self.state}>"


def run_with_timeout(func, timeout, *args, **kwargs):
    """Run ``func(*args, **kwargs)`` in a daemon thread, bounded by ``timeout``.

    Returns the function result, re-raises any underlying exception, and raises
    ``TimeoutError`` if the deadline is exceeded. The daemon thread guarantees a
    hung OPEA call can never block process shutdown.
    """
    box = {}

    def _target():
        try:
            box["value"] = func(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 - captured and re-raised on caller side
            box["error"] = exc

    thread = threading.Thread(target=_target, daemon=True)
    thread.start()
    thread.join(timeout)
    if thread.is_alive():
        raise TimeoutError(f"OPEA call exceeded {timeout}s and was abandoned")
    if "error" in box:
        raise box["error"]
    return box.get("value")


def guard(fallback=None, timeout=0.8, breaker=None):
    """Decorator that guards a callable.

    On ANY exception or timeout, logs a warning and returns ``fallback`` —
    never raises into the caller.
    """
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            cb = breaker
            try:
                if cb is not None and not cb.allow_request():
                    log.warning("OPEA route skipped (circuit open) -> local fallback")
                    return fallback() if callable(fallback) else fallback
                result = run_with_timeout(func, timeout, *args, **kwargs)
                if cb is not None:
                    cb.record_success()
                return result
            except Exception as exc:  # noqa: BLE001 - intentionally swallowed
                if cb is not None:
                    cb.record_failure()
                log.warning("OPEA route degraded (%s) -> local fallback", exc)
                return fallback() if callable(fallback) else fallback
        return wrapper
    return decorator


__all__ = [
    "OpeaCircuitBreaker",
    "CircuitOpenError",
    "run_with_timeout",
    "guard",
]
