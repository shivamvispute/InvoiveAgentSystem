"""
LangGraph shared state schema.
Every agent reads from and writes to this TypedDict as the invoice flows through the pipeline.
"""
from typing import Annotated, Any
from typing_extensions import TypedDict
import operator


class AgentState(TypedDict):
    # Invoice identity
    invoice_id: int
    invoice_number: str

    # Raw input
    raw_text: str

    # ── Stage outputs (each agent fills its own section) ──────────────────────

    # Ingestion agent
    ingestion_complete: bool
    ingestion_error: str | None

    # Extraction agent
    extracted_vendor_name: str | None
    extracted_amount: float | None
    extracted_invoice_date: str | None
    extracted_due_date: str | None
    extracted_po_number: str | None
    extracted_description: str | None
    extracted_line_items: list[dict] | None
    extraction_confidence: float
    extraction_notes: str

    # Validation agent
    validation_passed: bool
    validation_results: Annotated[list[dict], operator.add]   # accumulates across retries
    validation_warnings: Annotated[list[str], operator.add]

    # Anomaly detection agent
    anomalies_found: Annotated[list[dict], operator.add]
    anomaly_risk_level: str    # none | low | medium | high | critical
    requires_human_review: bool

    # Human-in-the-loop
    human_decision: str | None   # approved | rejected | override
    human_notes: str | None

    # Audit trail (all agents append here)
    audit_trail: Annotated[list[dict], operator.add]

    # Routing
    current_agent: str
    pipeline_status: str     # running | paused | completed | failed
    error_message: str | None

    # Cost tracking (for demo metrics)
    total_llm_calls: int
    total_tokens_used: int
