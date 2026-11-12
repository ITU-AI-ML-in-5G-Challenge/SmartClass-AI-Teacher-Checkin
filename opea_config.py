"""
OPEA configuration, microservice registry, and project references.

OPEA — Open Platform for Enterprise AI — https://opea.dev/ — is an open,
Linux Foundation–hosted project (initiated with Intel) providing cloud-native,
composable GenAI microservices and enterprise blueprints. Key sub-projects:

    * GenAIComps     — microservice-level GenAI components + service composer
                       (https://github.com/opea-project/GenAIComps)
    * GenAIExamples  — ready-made pipelines (ChatQnA, DocSum, CodeGen, ...)
                       (https://github.com/opea-project/GenAIExamples)
    * GenAIEval      — evaluation / benchmarking harness
                       (https://github.com/opea-project/GenAIEval)
    * GenAIInfra     — containerization & cloud-native (Kubernetes) suite
                       (https://github.com/opea-project/GenAIInfra)

This module centralises those references and the canonical GenAIComps
microservice registry (name → default port → HTTP endpoint). Every endpoint is
overridable via environment variables, so the integration can point at any
deployment (Docker Compose, Kubernetes/GenAIInfra, Gaudi/Xeon/NVIDIA, or the
AWS/Azure marketplace stacks).
"""

import os

# --- project references -----------------------------------------------------
OPEA_HOMEPAGE = "https://opea.dev/"
OPEA_GITHUB = "https://github.com/opea-project"
OPEA_GENAICOMP_REPO = "https://github.com/opea-project/GenAIComps"
OPEA_GENAIEXAMPLES_REPO = "https://github.com/opea-project/GenAIExamples"
OPEA_GENAIEVAL_REPO = "https://github.com/opea-project/GenAIEval"
OPEA_GENAIINFRA_REPO = "https://github.com/opea-project/GenAIInfra"

# --- microservice registry ---------------------------------------------------
# name -> (default port, endpoint path, environment variable)
# Ports are the GenAIComps defaults and are illustrative/configurable.
MICROSERVICES = {
    "embedding":     ("6000", "/v1/embeddings",          "OPEA_EMBEDDING_URL"),
    "llm":           ("9000", "/v1/chat/completions",    "OPEA_LLM_URL"),
    "llm_tgi":       ("8008", "/v1/chat/completions",    "OPEA_LLM_TGI_URL"),
    "rerank":        ("8000", "/v1/reranking",           "OPEA_RERANK_URL"),
    "retriever":     ("7000", "/v1/retrieval",           "OPEA_RETRIEVER_URL"),
    "dataprep":      ("6007", "/v1/dataprep",            "OPEA_DATAPREP_URL"),
    "guardrails":    ("9090", "/v1/guardrails",          "OPEA_GUARDRAILS_URL"),
    "asr":           ("9099", "/v1/audio/transcriptions", "OPEA_ASR_URL"),
    "tts":           ("9080", "/v1/audio/speech",        "OPEA_TTS_URL"),
    "web_retriever": ("7070", "/v1/web_search",          "OPEA_WEB_RETRIEVER_URL"),
    "gateway":       ("8888", "/v1/chatqna",             "OPEA_GATEWAY_URL"),
}


def default_endpoint(name):
    """Return the default localhost endpoint for a microservice name."""
    if name not in MICROSERVICES:
        return None
    port, path, _ = MICROSERVICES[name]
    return f"http://localhost:{port}{path}"


def configured_endpoint(name):
    """Return the env-overridden endpoint, or the default if unset."""
    _, _, env_key = MICROSERVICES[name]
    return os.environ.get(env_key) or default_endpoint(name)


def all_endpoints():
    """Return {name: configured_endpoint} for the whole registry."""
    return {name: configured_endpoint(name) for name in MICROSERVICES}
