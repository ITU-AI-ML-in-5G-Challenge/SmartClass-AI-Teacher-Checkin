"""
OPEA connectivity + pipeline demo for the SmartClass AI Tracker & Reviewer.

This is a STANDALONE, optional demo. It exercises the OPEA integration
(opea_integration.py client, opea_components.py orchestration, opea_rag.py
RAG pipeline, opea_examples.py catalogue) against locally deployed OPEA
GenAIComps microservices, and never touches the main check-in / homework-review
code.

It always exits cleanly (exit code 0) even when no OPEA service is running, so
it is safe to run on any machine or inside the packaged Docker image:

    python opea_demo.py

Reference: https://opea.dev/
"""

import sys

from opea_integration import OPEA
from opea_rag import RAGPipeline
from opea_examples import list_examples, build_example


def main():
    print("=" * 64)
    print("  OPEA demo - SmartClass AI Tracker & Reviewer")
    print("  https://opea.dev/")
    print("=" * 64)

    opea = OPEA()

    print("\n[1/5] Health probe (configured GenAIComps endpoints):")
    for name, ok in opea.health().items():
        print(f"  - {name:<14} {'reachable' if ok else 'not reachable (skipped)'}")

    print("\n[2/5] RAG chain (embedding -> retriever -> rerank -> llm):")
    rag = RAGPipeline(opea)
    print("  topology:", rag.orchestrator.topology())
    result = rag.query("How are student check-ins reported?", k=4, top_n=2)
    for key in ("embedding", "documents", "reranked", "answer"):
        print(f"  - {key:<10} {'ok' if result.get(key) is not None else 'unavailable (fallback)'}")

    print("\n[3/5] Other microservices (ASR / TTS / dataprep / web retriever):")
    for name in ("asr", "tts", "dataprep", "web_retriever"):
        print(f"  - {name:<14} {'reachable' if opea.is_available(name) else 'not reachable (skipped)'}")

    print("\n[4/5] OPEA GenAIExamples catalogue:")
    for ex in list_examples():
        print(f"  - {ex}")

    print("\n[5/5] Build an example orchestrator:")
    orch, gw = build_example("ChatQnA")
    print(f"  {gw} -> {orch.topology()}")

    print("\nDone. (Optional demo; does not affect normal operation.)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
