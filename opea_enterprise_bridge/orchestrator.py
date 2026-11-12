"""
Multi-stage OPEA pipeline orchestrator.

Composes several OPEA microservice stages into a single logical pipeline
(e.g. ChatQnA: embedding -> retrieval -> rerank -> chat). Every stage is
executed through the circuit breaker and a hard 0.8 s timeout; a failing or
missing stage is skipped and marked as degraded, so the pipeline can never
raise into the caller.

Reference: https://opea.dev/
"""

import logging
import time

from .client import OpeaMicroserviceClient
from .circuit import OpeaCircuitBreaker, run_with_timeout
from .models import OpeaPipelineStage, to_dict

log = logging.getLogger("opea.bridge.orchestrator")


class PipelineStage:
    """A named stage wrapping a client operation."""

    def __init__(self, name, operation, payload=None, timeout=0.8):
        self.name = name
        self.operation = operation
        self.payload = payload or {}
        self.timeout = timeout

    def __repr__(self):
        return f"<PipelineStage {self.name} -> {self.operation}>"


class OpeaPipelineOrchestrator:
    """Runs a linear multi-stage OPEA pipeline with per-stage fault isolation."""

    def __init__(self, name="ChatQnA", client=None, timeout=0.8, circuit=None):
        self.name = name
        self.client = client or OpeaMicroserviceClient(timeout=timeout)
        self.timeout = timeout
        self.circuit = circuit or OpeaCircuitBreaker(name=f"opea-{name}")
        self.stages = []

    def add_stage(self, name, operation, payload=None):
        self.stages.append(PipelineStage(name, operation, payload, self.timeout))
        return self

    def run(self, context=None):
        """Execute every stage, collecting results; never raises."""
        context = dict(context or {})
        results = {}
        for stage in self.stages:
            started = time.monotonic()
            try:
                payload = dict(stage.payload)
                payload.setdefault("context", context)
                value = run_with_timeout(
                    lambda p=payload: self.client.invoke(stage.operation, p),
                    stage.timeout,
                )
                context[stage.name] = value
                results[stage.name] = {"ok": value is not None, "value": value}
            except Exception as exc:  # noqa: BLE001 - stage fault isolation
                results[stage.name] = {"ok": False, "degraded": True}
                log.warning("OPEA stage '%s' degraded (%s) -> skipped", stage.name, exc)
            results[stage.name]["latency_ms"] = round((time.monotonic() - started) * 1000, 2)
        return {"pipeline": self.name, "stages": results, "context": context}

    def topology(self):
        return [to_dict(OpeaPipelineStage(name=s.name, service=s.operation, order=i,
                                          timeout=s.timeout))
                for i, s in enumerate(self.stages)]

    def __repr__(self):
        return f"<OpeaPipelineOrchestrator {self.name}: {[s.name for s in self.stages]}>"


__all__ = ["PipelineStage", "OpeaPipelineOrchestrator"]
