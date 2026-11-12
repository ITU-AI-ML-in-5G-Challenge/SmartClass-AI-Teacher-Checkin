# OPEA Integration

This repository includes an optional integration with
[OPEA — Open Platform for Enterprise AI](https://opea.dev/), an open,
Linux Foundation–hosted project (initiated with Intel) that provides
cloud-native, composable GenAI microservices and enterprise blueprints.

The integration is **additive only**: it lives in standalone files and is not
imported by the main check-in / homework-review code, so it cannot change how
the normal application runs. If no OPEA service is deployed, every OPEA call
falls back gracefully and returns `None`.

## About OPEA

OPEA's main sub-projects:

| Sub-project    | Purpose                                                        |
| -------------- | -------------------------------------------------------------- |
| GenAIComps     | Microservice-level GenAI components + service composer         |
| GenAIExamples  | Ready-made pipelines (ChatQnA, DocSum, CodeGen, …)             |
| GenAIEval      | Evaluation / benchmarking harness                              |
| GenAIInfra     | Containerization & cloud-native (Kubernetes) deployment suite  |

All are under [github.com/opea-project](https://github.com/opea-project).

## Files in this repo

| File                  | Purpose                                                             |
| --------------------- | ------------------------------------------------------------------- |
| `opea_config.py`      | Project references + microservice registry (name → port → endpoint). |
| `opea_integration.py` | Self-contained stdlib HTTP client for the OPEA microservices.       |
| `opea_components.py`  | `MicroService` / `ServiceOrchestrator` / `Gateway` orchestration.   |
| `opea_rag.py`         | RAG pipeline (ingest → embed → retrieve → rerank → generate).       |
| `opea_examples.py`    | Catalogue of OPEA GenAIExamples and their service composition.      |
| `opea_demo.py`        | Standalone connectivity + pipeline demo; always exits 0.            |
| `Dockerfile`          | Packages the cross-platform parts into a container image.           |
| `docker-compose.yml`  | Local deployment with optional OPEA microservices (`--profile opea`). |
| `.dockerignore`       | Keeps the image lean.                                               |
| `.github/workflows/opea-docker.yml` | CI smoke test for the OPEA integration.            |

## OPEA microservices

The client targets the standard
[GenAIComps](https://github.com/opea-project/GenAIComps) REST endpoints
(default ports shown; all configurable via environment variables):

| Service        | Default endpoint                            | Env var                 |
| -------------- | ------------------------------------------- | ----------------------- |
| Embedding      | `http://localhost:6000/v1/embeddings`       | `OPEA_EMBEDDING_URL`    |
| LLM (vLLM)     | `http://localhost:9000/v1/chat/completions` | `OPEA_LLM_URL`          |
| LLM (TGI)      | `http://localhost:8008/v1/chat/completions` | `OPEA_LLM_TGI_URL`      |
| Rerank         | `http://localhost:8000/v1/reranking`        | `OPEA_RERANK_URL`       |
| Retriever      | `http://localhost:7000/v1/retrieval`        | `OPEA_RETRIEVER_URL`    |
| Dataprep       | `http://localhost:6007/v1/dataprep`         | `OPEA_DATAPREP_URL`     |
| Guardrails     | `http://localhost:9090/v1/guardrails`       | `OPEA_GUARDRAILS_URL`   |
| ASR            | `http://localhost:9099/v1/audio/transcriptions` | `OPEA_ASR_URL`      |
| TTS            | `http://localhost:9080/v1/audio/speech`     | `OPEA_TTS_URL`          |
| Web retriever  | `http://localhost:7070/v1/web_search`       | `OPEA_WEB_RETRIEVER_URL`|
| Gateway        | `http://localhost:8888/v1/chatqna`          | `OPEA_GATEWAY_URL`      |

The request timeout is `OPEA_TIMEOUT` (seconds, default 10).

## Orchestration model

OPEA composes microservices into a single "mega-service" pipeline. This repo
mirrors that with `opea_components.py`:

```python
from opea_components import MicroService, ServiceOrchestrator, Gateway

orch = ServiceOrchestrator(name="ChatQnA")
orch.add(MicroService("embedding", port=6000, endpoint="/v1/embeddings"))
orch.add(MicroService("retriever", port=7000, endpoint="/v1/retrieval"))
orch.add(MicroService("rerank",   port=8000, endpoint="/v1/reranking"))
orch.add(MicroService("llm",      port=9000, endpoint="/v1/chat/completions"))
orch.flow("embedding", "retriever").flow("retriever", "rerank").flow("rerank", "llm")
gateway = Gateway(orch, name="chatqna-gateway")
print(orch.topology())
```

## RAG pipeline

```python
from opea_rag import RAGPipeline, rag_query

pipeline = RAGPipeline()
result = pipeline.query("How are student check-ins reported?")
print(result["answer"])          # None if the OPEA LLM is not running

answer = rag_query("Summarise yesterday's homework submissions")
```

## OPEA GenAIExamples

`opea_examples.py` catalogues the named pipelines (ChatQnA, DocIndexRetriever,
DocSum, CodeGen, CodeTrans, Translation, AudioQnA, VisualQnA, MultimodalQnA,
AgentQnA, SearchQnA, FaqGen). Example:

```python
from opea_examples import list_examples, build_example
print(list_examples())
orch, gateway = build_example("ChatQnA")
```

## Quick start

```bash
# Run the demo locally (works with or without OPEA services running)
python opea_demo.py

# Build and run the containerised demo
docker build -t smartclass-ai-teacher-checkin .
docker run --rm smartclass-ai-teacher-checkin

# Or use compose, optionally bringing up OPEA microservices too
docker compose up --build
docker compose --profile opea up
```

If the OPEA services are not deployed, the demo and every helper simply report
"unavailable" and return `None`; the rest of the application is unaffected.
