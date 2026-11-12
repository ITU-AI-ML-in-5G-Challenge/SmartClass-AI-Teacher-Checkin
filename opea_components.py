"""
OPEA microservice orchestration primitives.

Mirrors the composer concept in OPEA GenAIComps — ``MicroService``,
``ServiceOrchestrator`` and ``Gateway`` — which is used to wire individual
microservices into a single "mega-service" pipeline (for example ChatQnA:
embedding → retriever → rerank → llm).

Pure Python standard library.
"""

import logging

log = logging.getLogger("opea.orch")


class MicroService:
    """A named OPEA microservice node with an endpoint and an optional flow target."""

    def __init__(self, name, host="localhost", port=None, endpoint=None, use_remote=False):
        self.name = name
        self.host = host
        self.port = port
        self.endpoint = endpoint
        self.use_remote = use_remote
        self.flow_to = None

    def url(self):
        base = f"http://{self.host}"
        if self.port:
            base += f":{self.port}"
        return base + (self.endpoint or "")

    def __repr__(self):
        return f"<MicroService {self.name} @ {self.url()}>"


class ServiceOrchestrator:
    """Composes MicroServices into a directed pipeline (mega-service)."""

    def __init__(self, name="megaservice"):
        self.name = name
        self.services = {}

    def add(self, service):
        self.services[service.name] = service
        return service

    def flow(self, src_name, dst_name):
        """Connect src_name -> dst_name in the pipeline."""
        self.services[src_name].flow_to = self.services[dst_name]
        return self

    def topology(self):
        """Return the pipeline as a list of (name, next_name) edges."""
        return [
            (s.name, (s.flow_to.name if s.flow_to else None))
            for s in self.services.values()
        ]

    def __repr__(self):
        return f"<ServiceOrchestrator {self.name}: {self.topology()}>"


class Gateway:
    """Exposes a single public endpoint in front of an orchestrator."""

    def __init__(self, megaservice, name="gateway"):
        self.megaservice = megaservice
        self.name = name

    def __repr__(self):
        return f"<Gateway {self.name} -> {self.megaservice.name}>"
