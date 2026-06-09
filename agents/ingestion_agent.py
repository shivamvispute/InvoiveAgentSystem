"""
Agent 1 — Ingestion Agent
Responsibility: receive invoice text/document, store it, mark pipeline started.
In production this would parse PDFs, OCR images, parse emails, etc.
"""
import time
from datetime import datetime

from agents.state import AgentState
from db.connection import get_session
from db.models import AuditLog, Invoice, InvoiceStatus


async def ingestion_agent(state: AgentState) -> dict:
    """
    Entry point for the invoice pipeline.
    Validates the invoice exists, loads raw text into state, marks pipeline started.
    """
    start_ms = time.time()
    invoice_id = state["invoice_id"]

    async with get_session() as session:
        invoice = await session.get(Invoice, invoice_id)
        if not invoice:
            return {
                "ingestion_complete": False,
                "ingestion_error": f"Invoice {invoice_id} not found in database",
                "pipeline_status": "failed",
                "current_agent": "ingestion",
                "audit_trail": [_log("ingestion", f"Invoice {invoice_id} not found", "error")],
            }

        raw_text = invoice.raw_text or f"Invoice #{invoice.invoice_number} — Amount: ${invoice.amount}"
        invoice.status = InvoiceStatus.EXTRACTING

        log = AuditLog(
            invoice_id=invoice_id,
            agent_name="ingestion_agent",
            action="Invoice received and queued for extraction",
            details=f"Raw text length: {len(raw_text)} chars",
            duration_ms=int((time.time() - start_ms) * 1000),
        )
        session.add(log)

    return {
        "raw_text": raw_text,
        "invoice_number": invoice.invoice_number,
        "ingestion_complete": True,
        "ingestion_error": None,
        "pipeline_status": "running",
        "current_agent": "ingestion",
        "total_llm_calls": 0,
        "total_tokens_used": 0,
        "audit_trail": [_log("ingestion", "Invoice ingested successfully", "info")],
    }


def _log(agent: str, message: str, level: str = "info") -> dict:
    return {
        "agent": agent,
        "message": message,
        "level": level,
        "timestamp": datetime.utcnow().isoformat(),
    }
