"""
Pipeline runner — wraps the LangGraph execution with error handling, logging, and metrics.
Used by both the FastAPI endpoints and the CLI demo script.
"""
import asyncio
import time
import uuid
from typing import AsyncGenerator

from workflow.graph import get_graph, get_checkpointer
from agents.state import AgentState


async def run_invoice_pipeline(
    invoice_id: int,
    invoice_number: str,
    raw_text: str = "",
    with_hitl: bool = True,
) -> tuple[dict, str]:
    """
    Run the full invoice processing pipeline.

    Returns:
        (final_state, thread_id) — thread_id is needed to resume after HITL pause
    """
    graph = get_graph(with_hitl=with_hitl)
    thread_id = str(uuid.uuid4())

    initial_state: AgentState = {
        "invoice_id": invoice_id,
        "invoice_number": invoice_number,
        "raw_text": raw_text,
        # Initialize all list/accumulator fields
        "validation_results": [],
        "validation_warnings": [],
        "anomalies_found": [],
        "audit_trail": [],
        "ingestion_complete": False,
        "ingestion_error": None,
        "extracted_vendor_name": None,
        "extracted_amount": None,
        "extracted_invoice_date": None,
        "extracted_due_date": None,
        "extracted_po_number": None,
        "extracted_description": None,
        "extracted_line_items": None,
        "extraction_confidence": 0.0,
        "extraction_notes": "",
        "validation_passed": False,
        "anomaly_risk_level": "none",
        "requires_human_review": False,
        "human_decision": None,
        "human_notes": None,
        "current_agent": "init",
        "pipeline_status": "running",
        "error_message": None,
        "total_llm_calls": 0,
        "total_tokens_used": 0,
    }

    config = {"configurable": {"thread_id": thread_id}}

    final_state = {}
    async for chunk in graph.astream(initial_state, config=config):
        for node_name, node_output in chunk.items():
            final_state.update(node_output)

    return final_state, thread_id


async def resume_pipeline_with_decision(
    thread_id: str,
    human_decision: str,
    human_notes: str = "",
) -> dict:
    """
    Resume a paused pipeline after human provides a decision.
    decision: "approved" | "rejected" | "override"
    """
    graph = get_graph(with_hitl=True)
    config = {"configurable": {"thread_id": thread_id}}

    # Inject human decision into state
    graph.update_state(
        config,
        {"human_decision": human_decision, "human_notes": human_notes},
        as_node="human_review",
    )

    final_state = {}
    async for chunk in graph.astream(None, config=config):
        for node_name, node_output in chunk.items():
            final_state.update(node_output)

    return final_state


async def get_pipeline_state(thread_id: str) -> dict | None:
    """Fetch the current state of a paused or running pipeline."""
    graph = get_graph(with_hitl=True)
    config = {"configurable": {"thread_id": thread_id}}
    try:
        snapshot = graph.get_state(config)
        return snapshot.values if snapshot else None
    except Exception:
        return None
