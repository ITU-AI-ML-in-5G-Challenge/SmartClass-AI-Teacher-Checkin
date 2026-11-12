"""
Decorators for mounting OPEA onto existing, stable functions.

``@safe_opea_wrapper`` is the primary, one-line mount: it returns the wrapped
function's result unchanged while dispatching an OPEA operation in a background
daemon thread. The OPEA path is guarded by a 0.8 s timeout and a circuit
breaker, so it can never block or break the caller.
"""

import functools
import logging
import threading

from .client import OpeaMicroserviceClient
from .telemetry import TelemetrySink
from .models import OpeaTelemetryEvent

log = logging.getLogger("opea.bridge.decorators")

_default_client = None
_default_telemetry = None


def _client():
    global _default_client
    if _default_client is None:
        _default_client = OpeaMicroserviceClient(mode="local", timeout=0.8)
    return _default_client


def _telemetry():
    global _default_telemetry
    if _default_telemetry is None:
        _default_telemetry = TelemetrySink()
    return _default_telemetry


def _side_channel(route, timeout, payload):
    """Fire-and-forget OPEA dispatch; never raises, never blocks the caller."""
    try:
        _client().invoke(route, payload)
        _telemetry().emit(OpeaTelemetryEvent(
            name=route, route="opea", ok=True,
            detail={"payload": str(payload)[:200]},
        ))
    except Exception as exc:  # noqa: BLE001 - dispatch must be silent
        log.warning("OPEA '%s' background route degraded (%s) -> local fallback", route, exc)


def safe_opea_wrapper(opea_route="audit", timeout=0.8, background=True):
    """Decorate a local function with a fire-and-forget OPEA dispatch.

    Example (one line)::

        @safe_opea_wrapper(opea_route="audit", timeout=0.8)
        def my_stable_logic(x):
            return x * 2

    ``my_stable_logic`` keeps its original behaviour and return value; OPEA runs
    in a background daemon thread and can never block, slow, or break it.
    """
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            # 1) Canonical local execution — untouched and always returned.
            result = func(*args, **kwargs)

            # 2) Optional OPEA dispatch (background thread).
            payload = {
                "args": str(args)[:200],
                "kwargs": str(kwargs)[:200],
                "result": str(result)[:200],
            }
            if background:
                try:
                    threading.Thread(
                        target=_side_channel,
                        args=(opea_route, timeout, payload),
                        daemon=True,
                    ).start()
                except Exception as exc:  # noqa: BLE001
                    log.warning("OPEA dispatch skipped (%s) -> local fallback", exc)
            else:
                _side_channel(opea_route, timeout, payload)

            return result
        return wrapper
    return decorator


def opea_fallback(operation, payload=None, local=None, timeout=0.8):
    """Try an OPEA operation and return ``local`` on ANY failure/timeout.

    A synchronous "try OPEA, else local" primitive for callers that want an
    OPEA-enhanced value but can never afford to break.
    """
    try:
        value = _client().invoke(operation, payload or {})
        return local if value is None else value
    except Exception as exc:  # noqa: BLE001
        log.warning("OPEA '%s' degraded (%s) -> returning local value", operation, exc)
        return local


__all__ = ["safe_opea_wrapper", "opea_fallback"]
