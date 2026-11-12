"""
OPEA standard microservice client.

Presents the canonical OPEA GenAIComps surface (embedding, chat, rerank,
retrieval, dataprep, guardrails, asr, tts, web search) behind a circuit-breaker
and a hard 0.8 s timeout. In ``local`` mode every call returns deterministic
sample data instantly (no network round-trip); in ``http`` mode it delegates to
the HTTP client (opea_integration) under the same fault-isolation guarantees.

Reference: https://opea.dev/ and https://github.com/opea-project/GenAIComps
"""

import hashlib
import logging
import random
import time

from .circuit import OpeaCircuitBreaker, guard, run_with_timeout
from .models import OpeaResponse, to_dict

log = logging.getLogger("opea.bridge.client")


def _stable_vector(text, dim=16):
    """Deterministic pseudo-embedding so local results are reproducible."""
    seed = int(hashlib.md5(str(text).encode("utf-8")).hexdigest(), 16)
    rng = random.Random(seed)
    return [round(rng.uniform(-1.0, 1.0), 6) for _ in range(dim)]


class OpeaMicroserviceClient:
    """Local/HTTP OPEA microservice client with built-in fault isolation."""

    def __init__(self, mode="local", timeout=0.8, circuit=None):
        self.mode = mode
        self.timeout = timeout
        self.circuit = circuit or OpeaCircuitBreaker(name="opea-client")

    # -- internal dispatch ---------------------------------------------------

    def invoke(self, operation, payload=None):
        """Invoke a named OPEA operation, fully guarded; never raises."""
        started = time.monotonic()
        handler = getattr(self, operation, None)
        if handler is None:
            log.warning("OPEA unknown operation '%s' -> local fallback", operation)
            return None
        try:
            if not self.circuit.allow_request():
                log.warning("OPEA route skipped (circuit open) -> local fallback")
                return None
            result = run_with_timeout(handler, self.timeout, payload or {})
            self.circuit.record_success()
            return result
        except Exception as exc:  # noqa: BLE001 - intentionally swallowed
            self.circuit.record_failure()
            log.warning("OPEA '%s' degraded (%s) -> local fallback", operation, exc)
            return None

    # -- local microservices (canonical OPEA surface) ------------------------

    def embedding(self, payload=None):
        payload = payload or {}
        texts = payload.get("input") or payload.get("texts") or ["default"]
        if isinstance(texts, str):
            texts = [texts]
        if self.mode == "http":
            from opea_integration import OPEA  # lazy import
            return OPEA(timeout=self.timeout).embedding(texts)
        return [_stable_vector(t) for t in texts]

    def chat(self, payload=None):
        payload = payload or {}
        if self.mode == "http":
            from opea_integration import OPEA
            return OPEA(timeout=self.timeout).chat(payload.get("messages", []))
        return ("[OPEA] enterprise RAG response for: "
                + str(payload.get("messages", [{}])[-1].get("content", ""))[:80])

    def rerank(self, payload=None):
        payload = payload or {}
        docs = payload.get("documents") or []
        if self.mode == "http":
            from opea_integration import OPEA
            return OPEA(timeout=self.timeout).rerank(payload.get("query", ""), docs)
        scored = sorted(
            ((round(random.uniform(0.5, 1.0), 4), d) for d in docs),
            reverse=True,
        )
        return scored

    def retrieval(self, payload=None):
        payload = payload or {}
        query = payload.get("text") or payload.get("query") or ""
        k = int(payload.get("k", 3))
        if self.mode == "http":
            from opea_integration import OPEA
            return OPEA(timeout=self.timeout).retrieve(query, k=k)
        return [f"[OPEA] retrieved chunk {i} for {query!r}" for i in range(1, k + 1)]

    def dataprep(self, payload=None):
        payload = payload or {}
        count = len(payload.get("files") or payload.get("link_list") or [1])
        if self.mode == "http":
            from opea_integration import OPEA
            return OPEA(timeout=self.timeout).dataprep(
                files=payload.get("files"), links=payload.get("link_list"))
        return {"status": "indexed", "documents": count}

    def guardrails(self, payload=None):
        payload = payload or {}
        if self.mode == "http":
            from opea_integration import OPEA
            return OPEA(timeout=self.timeout).guardrails(payload.get("input"))
        return {"safe": True, "risk": "low", "input": str(payload.get("input", ""))[:60]}

    def asr(self, payload=None):
        payload = payload or {}
        if self.mode == "http":
            from opea_integration import OPEA
            return OPEA(timeout=self.timeout).asr(payload.get("file_path"))
        return {"text": "[OPEA] sample transcription"}

    def tts(self, payload=None):
        payload = payload or {}
        if self.mode == "http":
            from opea_integration import OPEA
            return OPEA(timeout=self.timeout).tts(payload.get("input"))
        return b"OPEA-AUDIO"

    def web_search(self, payload=None):
        payload = payload or {}
        if self.mode == "http":
            from opea_integration import OPEA
            return OPEA(timeout=self.timeout).web_search(payload.get("query", ""))
        return {"results": [{"title": "[OPEA] result", "url": "https://opea.dev/"}]}

    def health(self, payload=None):
        return {"mode": self.mode, "circuit": self.circuit.state, "ok": True}

    def audit(self, payload=None):
        """OPEA AI-enhanced audit."""
        payload = payload or {}
        if self.mode == "http":
            from opea_integration import OPEA  # lazy import
            return OPEA(timeout=self.timeout).guardrails(payload.get("result"))
        return {
            "audited": True,
            "risk": "low",
            "summary": "OPEA-enhanced audit",
            "input": str(payload.get("result", ""))[:80],
        }


__all__ = ["OpeaMicroserviceClient"]
