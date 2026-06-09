"""
Agent 5 — Human-in-the-Loop (HITL) Agent
Responsibility: handle invoices that require human review.
Demonstrates: LangGraph interrupt/resume pattern, approval workflow, audit trail.
"""
import time
from datetime import datetime

from agents.state import AgentState
from db.connection import get_session
from db.models import AuditLog, Invoice, InvoiceStatus


async def human_review_agent(state: AgentState) -> dict:
    """
    Presents invoice for human review.
    This node is reached after LangGraph interrupt() pauses execution.
    The human decision comes back via graph.update_state() and resumes here.
    """
    start_ms = time.time()
    invoice_id = state["invoice_id"]
    decision = state.get("human_decision")
    notes = state.get("human_notes", "")

    if not decision:
        # First entry — prepare the review packet (graph will interrupt after this)
        review_summary = _build_review_summary(state)
        return {
            "current_agent": "human_review",
            "pipeline_status": "paused",
            "audit_trail": [_log("human_review", "Invoice queued for human review — pipeline paused")],
        }

    # Resumed after human provided decision
    new_status = {
        "approved": InvoiceStatus.APPROVED,
        "rejected": InvoiceStatus.REJECTED,
        "override": InvoiceStatus.APPROVED,   # override = approve despite anomalies
    }.get(decision, InvoiceStatus.REVIEW)

    async with get_session() as session:
        invoice = await session.get(Invoice, invoice_id)
        if invoice:
            invoice.status = new_status
            invoice.reviewer_notes = notes
            invoice.reviewer_action = decision
            invoice.approved_at = datetime.utcnow() if decision in ("approved", "override") else None

            log = AuditLog(
                invoice_id=invoice_id,
                agent_name="human_review_agent",
                action=f"Human review decision: {decision.upper()}",
                details=f"Notes: {notes}" if notes else "No notes provided",
                duration_ms=int((time.time() - start_ms) * 1000),
            )
            session.add(log)

    return {
        "current_agent": "human_review",
        "pipeline_status": "completed",
        "audit_trail": [
            _log("human_review", f"Human decision: {decision.upper()} — {notes or 'no notes'}")
        ],
    }


async def auto_approve_agent(state: AgentState) -> dict:
    """
    Handles invoices that passed all checks and don't need human review.
    Auto-approves invoices below the threshold with no anomalies.
    """
    start_ms = time.time()
    invoice_id = state["invoice_id"]

    async with get_session() as session:
        invoice = await session.get(Invoice, invoice_id)
        if invoice:
            invoice.status = InvoiceStatus.APPROVED
            invoice.approved_at = datetime.utcnow()
            invoice.reviewer_notes = "Auto-approved: all checks passed, no anomalies detected"

            log = AuditLog(
                invoice_id=invoice_id,
                agent_name="auto_approve_agent",
                action="Invoice auto-approved",
                details=f"Amount: ${invoice.amount:,.2f} — all validations passed",
                duration_ms=int((time.time() - start_ms) * 1000),
            )
            session.add(log)

    return {
        "current_agent": "auto_approve",
        "pipeline_status": "completed",
        "human_decision": "auto_approved",
        "audit_trail": [_log("auto_approve", f"Invoice auto-approved — all checks passed")],
    }


def _build_review_summary(state: AgentState) -> str:
    lines = [
        f"Invoice: {state.get('invoice_number')}",
        f"Vendor: {state.get('extracted_vendor_name')}",
        f"Amount: ${state.get('extracted_amount', 0):,.2f}",
        f"Date: {state.get('extracted_invoice_date')}",
        f"PO: {state.get('extracted_po_number', 'N/A')}",
        "",
        f"Risk Level: {state.get('anomaly_risk_level', 'unknown').upper()}",
        f"Anomalies: {len(state.get('anomalies_found', []))}",
    ]
    for a in state.get("anomalies_found", []):
        lines.append(f"  [{a.get('severity', 'medium').upper()}] {a.get('description', '')}")
    return "\n".join(lines)


def _log(agent: str, message: str, level: str = "info") -> dict:
    return {
        "agent": agent,
        "message": message,
        "level": level,
        "timestamp": datetime.utcnow().isoformat(),
    }
