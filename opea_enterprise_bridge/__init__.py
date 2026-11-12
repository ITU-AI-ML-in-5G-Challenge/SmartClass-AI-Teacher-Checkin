"""
OPEA Enterprise Bridge — fault-tolerant OPEA adaptation layer.

This package provides an enterprise-grade OPEA (https://opea.dev/) integration:
every OPEA call is bounded by a 0.8 s timeout, guarded by a circuit breaker, and
wrapped so that no exception can ever reach the host application.

Importing this package is always safe — it depends only on the Python standard
library (Pydantic is used opportunistically for the contract models and has a
built-in zero-dependency fallback).

Typical one-line mount::

    from opea_enterprise_bridge import safe_opea_wrapper

    @safe_opea_wrapper(opea_route="audit", timeout=0.8)
    def my_stable_logic(x):
        return x * 2
"""

from .circuit import OpeaCircuitBreaker, run_with_timeout, guard  # noqa: F401
from .models import (  # noqa: F401
    BaseModel,
    Field,
    OpeaServiceConfig,
    OpeaRequest,
    OpeaResponse,
    OpeaPipelineStage,
    OpeaTelemetryEvent,
)
from .client import OpeaMicroserviceClient  # noqa: F401
from .orchestrator import OpeaPipelineOrchestrator, PipelineStage  # noqa: F401
from .telemetry import TelemetrySink  # noqa: F401
from .decorators import safe_opea_wrapper, opea_fallback  # noqa: F401
from .bridge import OpeaEnterpriseBridge  # noqa: F401

__version__ = "1.0.0"

__all__ = [
    "OpeaCircuitBreaker",
    "run_with_timeout",
    "guard",
    "BaseModel",
    "Field",
    "OpeaServiceConfig",
    "OpeaRequest",
    "OpeaResponse",
    "OpeaPipelineStage",
    "OpeaTelemetryEvent",
    "OpeaMicroserviceClient",
    "OpeaPipelineOrchestrator",
    "PipelineStage",
    "TelemetrySink",
    "safe_opea_wrapper",
    "opea_fallback",
    "OpeaEnterpriseBridge",
]
