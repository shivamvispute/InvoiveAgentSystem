"""
LangGraph StateGraph — the brain of the system.
Wires all 5 agents into a directed graph with conditional routing and HITL interrupt.

Pipeline flow:
  ingestion → extraction → validation → anomaly_detection
      → [requires review?] → human_review → END
      → [clean invoice?]   → auto_approve  → END
"""
from langgraph.graph import END, StateGraph
from langgraph.checkpoint.memory import MemorySaver

from agents.state import AgentState
from agents.ingestion_agent import ingestion_agent
from agents.extraction_agent import extraction_agent
from agents.validation_agent import validation_agent
from agents.anomaly_agent import anomaly_detection_agent
from agents.hitl_agent import auto_approve_agent, human_review_agent


# ── Routing functions ──────────────────────────────────────────────────────────

def route_after_ingestion(state: AgentState) -> str:
    """Skip to end if ingestion failed."""
    if not state.get("ingestion_complete") or state.get("pipeline_status") == "failed":
        return END
    return "extraction"


def route_after_validation(state: AgentState) -> str:
    """If critical validation failure with no possible override, skip anomaly check."""
    validation_results = state.get("validation_results", [])
    critical_fails = [
        r for r in validation_results
        if r.get("result") == "fail" and r.get("rule") in ("vendor_approved",)
    ]
    # Still run anomaly detection even on validation failures — we want the full picture
    return "anomaly_detection"


def route_after_anomaly(state: AgentState) -> str:
    """Route to human review if anomalies found, otherwise auto-approve."""
    if state.get("requires_human_review"):
        return "human_review"
    validation_passed = state.get("validation_passed", False)
    if not validation_passed:
        return "human_review"
    return "auto_approve"


def route_after_human_review(state: AgentState) -> str:
    """Always end after human review."""
    return END


# ── Graph construction ─────────────────────────────────────────────────────────

def build_graph(checkpointer=None) -> StateGraph:
    """
    Build and compile the invoice processing state graph.
    checkpointer: pass MemorySaver() to enable HITL interrupt/resume.
    """
    graph = StateGraph(AgentState)

    # Register nodes
    graph.add_node("ingestion", ingestion_agent)
    graph.add_node("extraction", extraction_agent)
    graph.add_node("validation", validation_agent)
    graph.add_node("anomaly_detection", anomaly_detection_agent)
    graph.add_node("human_review", human_review_agent)
    graph.add_node("auto_approve", auto_approve_agent)

    # Entry point
    graph.set_entry_point("ingestion")

    # Edges
    graph.add_conditional_edges(
        "ingestion",
        route_after_ingestion,
        {"extraction": "extraction", END: END},
    )
    graph.add_edge("extraction", "validation")
    graph.add_conditional_edges(
        "validation",
        route_after_validation,
        {"anomaly_detection": "anomaly_detection"},
    )
    graph.add_conditional_edges(
        "anomaly_detection",
        route_after_anomaly,
        {"human_review": "human_review", "auto_approve": "auto_approve"},
    )
    graph.add_edge("auto_approve", END)
    graph.add_conditional_edges(
        "human_review",
        route_after_human_review,
        {END: END},
    )

    # Compile with checkpointer for HITL support
    compile_kwargs = {}
    if checkpointer:
        compile_kwargs["checkpointer"] = checkpointer
        compile_kwargs["interrupt_before"] = ["human_review"]

    return graph.compile(**compile_kwargs)


# Singleton instances
_memory_checkpointer = MemorySaver()
_graph_with_hitl = build_graph(checkpointer=_memory_checkpointer)
_graph_no_hitl = build_graph()


def get_graph(with_hitl: bool = True):
    """Return the compiled graph. Use with_hitl=True for production, False for testing."""
    return _graph_with_hitl if with_hitl else _graph_no_hitl


def get_checkpointer() -> MemorySaver:
    return _memory_checkpointer
