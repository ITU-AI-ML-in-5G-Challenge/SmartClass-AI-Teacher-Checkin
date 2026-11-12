"""
RAG pipeline over OPEA microservices (ChatQnA / DocIndexRetriever style).

Implements the classic OPEA retrieval-augmented-generation chain:

    ingest → embed → retrieve → rerank → generate

with graceful fallback at every stage: if any OPEA service is unavailable, the
corresponding result is ``None`` and the caller (e.g. opea_demo.py) simply
reports it.
"""

from opea_integration import OPEA
from opea_components import MicroService, ServiceOrchestrator, Gateway


class RAGPipeline:
    """A thin RAG pipeline backed by OPEA microservices."""

    def __init__(self, opea=None):
        self.opea = opea or OPEA()
        self.orchestrator = self._build_orchestrator()
        self.gateway = Gateway(self.orchestrator, name="chatqna-gateway")

    def _build_orchestrator(self):
        orch = ServiceOrchestrator(name="ChatQnA")
        orch.add(MicroService("embedding", port=6000, endpoint="/v1/embeddings"))
        orch.add(MicroService("retriever", port=7000, endpoint="/v1/retrieval"))
        orch.add(MicroService("rerank", port=8000, endpoint="/v1/reranking"))
        orch.add(MicroService("llm", port=9000, endpoint="/v1/chat/completions"))
        orch.flow("embedding", "retriever") \
            .flow("retriever", "rerank") \
            .flow("rerank", "llm")
        return orch

    def ingest(self, files=None, links=None):
        """Ingest documents into the dataprep index."""
        return self.opea.dataprep(files=files, links=links)

    def query(self, question, k=4, top_n=2):
        """Run the full RAG chain and return a dict of stage results."""
        embedding = self.opea.embedding([question])
        docs = self.opea.retrieve(question, k=k)
        reranked = self.opea.rerank(question, docs or [], top_n=top_n) if docs else None
        context = "\n".join(text for _, text in (reranked or [])) or None
        messages = [{"role": "user", "content": question}]
        if context:
            messages = [
                {"role": "system",
                 "content": "Answer using only the following context:\n" + context},
                {"role": "user", "content": question},
            ]
        answer = self.opea.chat(messages)
        return {
            "embedding": embedding,
            "documents": docs,
            "reranked": reranked,
            "answer": answer,
        }


def rag_query(question, **kwargs):
    """One-shot convenience wrapper returning the RAG answer string or None."""
    return RAGPipeline().query(question, **kwargs).get("answer")
