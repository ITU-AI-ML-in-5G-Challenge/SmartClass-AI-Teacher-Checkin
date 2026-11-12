"""
Enterprise data models conforming to the OPEA microservice contracts.

Uses Pydantic when it is available (the norm in OPEA deployments); otherwise it
transparently falls back to a zero-dependency shim so that importing this
package can NEVER fail — a hard requirement of the fault-tolerant bridge.

Reference: https://opea.dev/  (Open Platform for Enterprise AI)
"""

from typing import Any, Dict, List, Optional

# pydantic is OPTIONAL. The bridge must stay import-safe even without it, so a
# minimal stand-in is provided below. No import here may ever raise.
try:
    from pydantic import BaseModel as _PydanticModel  # noqa: F401
    from pydantic import Field as _PydanticField  # noqa: F401
    HAS_PYDANTIC = True
except Exception:  # pragma: no cover - defensive import guard
    HAS_PYDANTIC = False


if HAS_PYDANTIC:
    BaseModel = _PydanticModel
    Field = _PydanticField
else:
    class Field:
        """Minimal stand-in for ``pydantic.Field`` (fallback mode only)."""

        def __init__(self, default=None, **kwargs):
            self.default = default
            self.kwargs = kwargs

    class BaseModel:
        """Minimal stand-in for ``pydantic.BaseModel`` (fallback mode only)."""

        def __init__(self, **kwargs):
            for name in getattr(type(self), "__annotations__", {}):
                default = getattr(type(self), name, None)
                if isinstance(default, Field):
                    default = default.default
                setattr(self, name, kwargs.get(name, default))

        def model_dump(self, **kwargs):
            return {n: getattr(self, n) for n in getattr(type(self), "__annotations__", {})}

        def dict(self, *args, **kwargs):
            return self.model_dump()

        def __repr__(self):
            attrs = ", ".join(f"{k}={v!r}" for k, v in self.model_dump().items())
            return f"{type(self).__name__}({attrs})"


def to_dict(obj):
    """Convert a model/dataclass/object into a plain dict, best-effort."""
    if obj is None:
        return None
    if hasattr(obj, "model_dump"):
        try:
            return obj.model_dump()
        except Exception:
            pass
    if hasattr(obj, "dict"):
        try:
            return obj.dict()
        except Exception:
            pass
    if hasattr(obj, "__dict__"):
        return dict(vars(obj))
    return obj


# --- OPEA contract models ---------------------------------------------------

class OpeaServiceConfig(BaseModel):
    """Configuration for a single OPEA GenAIComps microservice."""

    name: str = "embedding"
    host: str = "localhost"
    port: int = 6000
    endpoint: str = "/v1/embeddings"
    timeout: float = 0.8
    enabled: bool = True


class OpeaRequest(BaseModel):
    """Normalised outbound OPEA request envelope."""

    operation: str = "embedding"
    payload: Optional[Dict[str, Any]] = None
    trace_id: Optional[str] = None


class OpeaResponse(BaseModel):
    """Normalised OPEA response envelope with explicit fallback signalling."""

    ok: bool = True
    data: Any = None
    fallback: bool = False
    latency_ms: float = 0.0
    route: str = "local"


class OpeaPipelineStage(BaseModel):
    """A single stage inside a multi-stage OPEA pipeline."""

    name: str = ""
    service: str = ""
    order: int = 0
    timeout: float = 0.8


class OpeaTelemetryEvent(BaseModel):
    """A telemetry event emitted by the OPEA telemetry sink."""

    name: str = "event"
    route: str = "opea"
    ok: bool = True
    detail: Optional[Dict[str, Any]] = None


__all__ = [
    "BaseModel",
    "Field",
    "HAS_PYDANTIC",
    "to_dict",
    "OpeaServiceConfig",
    "OpeaRequest",
    "OpeaResponse",
    "OpeaPipelineStage",
    "OpeaTelemetryEvent",
]
