"""
Catalogue of OPEA GenAIExamples (https://github.com/opea-project/GenAIExamples).

Maps each named example pipeline to the microservices it composes, mirroring
the OPEA project's example catalogue (ChatQnA, DocSum, CodeGen, CodeTrans,
Translation, AudioQnA, VisualQnA, MultimodalQnA, AgentQnA, SearchQnA, ...).

Purely informational / illustrative catalogue.
"""

from opea_components import MicroService, ServiceOrchestrator, Gateway

EXAMPLES = {
    "ChatQnA": {
        "description": "Chatbot with Retrieval-Augmented Generation (RAG).",
        "services": ["embedding", "retriever", "rerank", "llm"],
    },
    "DocIndexRetriever": {
        "description": "Document retrieval with RAG.",
        "services": ["dataprep", "embedding", "retriever", "rerank"],
    },
    "DocSum": {
        "description": "Document summarization.",
        "services": ["llm"],
    },
    "CodeGen": {
        "description": "Gen-AI powered code generation.",
        "services": ["llm"],
    },
    "CodeTrans": {
        "description": "Code translation between languages.",
        "services": ["llm"],
    },
    "Translation": {
        "description": "Machine translation.",
        "services": ["llm"],
    },
    "AudioQnA": {
        "description": "Question answering over audio.",
        "services": ["asr", "llm", "tts"],
    },
    "VisualQnA": {
        "description": "Visual question answering.",
        "services": ["embedding", "llm"],
    },
    "MultimodalQnA": {
        "description": "Multimodal question answering.",
        "services": ["embedding", "retriever", "llm"],
    },
    "AgentQnA": {
        "description": "Autonomous AI agent workflows.",
        "services": ["llm", "retriever"],
    },
    "SearchQnA": {
        "description": "Web-grounded question answering.",
        "services": ["web_retriever", "rerank", "llm"],
    },
    "FaqGen": {
        "description": "FAQ generation from documents.",
        "services": ["dataprep", "llm"],
    },
}


def list_examples():
    """Return the names of all known OPEA example pipelines."""
    return list(EXAMPLES.keys())


def describe(name):
    """Return the one-line description for a named example."""
    return EXAMPLES[name]["description"]


def build_example(name):
    """Build a microservice orchestrator for a named OPEA example.

    Returns (orchestrator, gateway); raises KeyError for unknown names.
    """
    if name not in EXAMPLES:
        raise KeyError(f"unknown OPEA example: {name!r} (known: {list_examples()})")
    orch = ServiceOrchestrator(name=name)
    for svc in EXAMPLES[name]["services"]:
        orch.add(MicroService(svc))
    gateway = Gateway(orch, name=f"{name.lower()}-gateway")
    return orch, gateway
