"""
OPEA mounting examples for the SmartClass AI Tracker & Reviewer.

This file demonstrates how to attach the OPEA Enterprise Bridge to existing,
stable functions. It is a standalone example of the integration surface.

Reference: https://opea.dev/
"""

from opea_enterprise_bridge import (
    OpeaEnterpriseBridge,
    OpeaPipelineOrchestrator,
    safe_opea_wrapper,
    opea_fallback,
)


# --------------------------------------------------------------------------
# Example 1 — one-line, fully non-blocking mount (@safe_opea_wrapper)
# --------------------------------------------------------------------------
@safe_opea_wrapper(opea_route="audit", timeout=0.8)
def local_checkin_tally(student_ids):
    """Existing stable local logic. This body is NEVER modified by OPEA."""
    return len(student_ids)


# --------------------------------------------------------------------------
# Example 2 — "try OPEA, else local" synchronous fallback
# --------------------------------------------------------------------------
def local_summary():
    return "Local summary (always correct)"


def enhanced_summary():
    return opea_fallback(
        operation="chat",
        payload={"messages": [{"role": "user", "content": "summarise"}]},
        local=local_summary(),
        timeout=0.8,
    )


# --------------------------------------------------------------------------
# Example 3 — explicit enterprise bridge + multi-stage pipeline + telemetry
# --------------------------------------------------------------------------
def run_bridge_demo():
    bridge = OpeaEnterpriseBridge(mode="local", timeout=0.8)

    pipeline = bridge.pipeline("ChatQnA") \
        .add_stage("embedding", "embedding", {"input": ["hello"]}) \
        .add_stage("retrieval", "retrieval", {"text": "hello", "k": 3}) \
        .add_stage("chat", "chat", {"messages": [{"role": "user", "content": "hello"}]})

    result = pipeline.run()
    bridge.report("pipeline.run", detail={"pipeline": result["pipeline"]})
    return result


if __name__ == "__main__":
    print("Example 1 (safe_opea_wrapper):")
    print("  count =", local_checkin_tally(["S001", "S002", "S003"]))

    print("\nExample 2 (opea_fallback):")
    print("  summary =", enhanced_summary())

    print("\nExample 3 (enterprise bridge + pipeline):")
    demo = run_bridge_demo()
    for stage, info in demo["stages"].items():
        print(f"  - {stage:<10} ok={info['ok']} latency_ms={info['latency_ms']}")

    print("\nDone. OPEA is fully isolated; local logic remains unaffected.")
