"""
OPEA enterprise bridge — top-level facade.

Ties together the circuit breaker, the local/HTTP microservice client, the
multi-stage pipeline orchestrator, and the telemetry sink into a single object
that the application (or the safe decorators) can use. Every entry point is
self-contained, and no call can raise into the caller.

Reference: https://opea.dev/
"""

from .client import OpeaMicroserviceClient
from .circuit import OpeaCircuitBreaker
from .orchestrator import OpeaPipelineOrchestrator
from .telemetry import TelemetrySink
from .models import OpeaTelemetryEvent


class OpeaEnterpriseBridge:
    """Facade exposing a fault-isolated OPEA integration surface."""

    def __init__(self, mode="local", timeout=0.8, telemetry=None):
        self.mode = mode
        self.timeout = timeout
        self.circuit = OpeaCircuitBreaker(name="opea-enterprise")
        self.client = OpeaMicroserviceClient(
            mode=mode, timeout=timeout, circuit=self.circuit)
        self.telemetry = telemetry or TelemetrySink()

    def pipeline(self, name="ChatQnA"):
        """Return a new multi-stage pipeline orchestrator bound to this bridge."""
        return OpeaPipelineOrchestrator(
            name=name, client=self.client, timeout=self.timeout, circuit=self.circuit)

    def invoke(self, operation, payload=None):
        """Invoke a single OPEA operation, guarded; returns None on failure."""
        return self.client.invoke(operation, payload)

    def report(self, name, detail=None):
        """Emit a best-effort OPEA telemetry event (never raises)."""
        return self.telemetry.emit(OpeaTelemetryEvent(name=name, detail=detail))

    def health(self):
        return self.client.health()

    def __repr__(self):
        return f"<OpeaEnterpriseBridge mode={self.mode!r} circuit={self.circuit.state}>"


__all__ = ["OpeaEnterpriseBridge"]
