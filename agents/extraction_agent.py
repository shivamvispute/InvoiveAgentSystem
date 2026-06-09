"""
Agent 2 — Extraction Agent
Responsibility: use LLM structured output to extract entities from raw invoice text.
Demonstrates: prompt engineering, structured output parsing, confidence scoring.
"""
import json
import re
import time
from datetime import datetime

from langchain_core.messages import HumanMessage, SystemMessage

from agents.llm import get_llm
from agents.state import AgentState
from db.connection import get_session
from db.models import AuditLog, Invoice, InvoiceStatus

EXTRACTION_SYSTEM_PROMPT = """You are an expert invoice data extraction AI.
Extract structured information from the invoice text provided.
You MUST respond with valid JSON only — no markdown, no explanation.

Required JSON schema:
{
  "vendor_name": "string or null",
  "amount": number or null,
  "invoice_date": "YYYY-MM-DD string or null",
  "due_date": "YYYY-MM-DD string or null",
  "po_number": "string or null",
  "description": "string or null",
  "line_items": [{"item": "string", "amount": number}] or [],
  "confidence": number between 0 and 1,
  "extraction_notes": "any observations about data quality or missing fields"
}

Rules:
- If a field is not present in the text, set it to null
- Convert all amounts to numbers (remove $, commas)
- Normalize dates to YYYY-MM-DD format
- confidence = 1.0 means all fields clearly present, 0.0 means very unclear
- Be conservative with confidence — partial data should be 0.5-0.7"""


async def extraction_agent(state: AgentState) -> dict:
    """Extract structured entities from raw invoice text using LLM."""
    start_ms = time.time()
    invoice_id = state["invoice_id"]
    raw_text = state.get("raw_text", "")

    if not raw_text:
        return {
            "extraction_confidence": 0.0,
            "extraction_notes": "No raw text available for extraction",
            "audit_trail": [_log("extraction", "No raw text to extract", "error")],
        }

    llm = get_llm(temperature=0.0)
    messages = [
        SystemMessage(content=EXTRACTION_SYSTEM_PROMPT),
        HumanMessage(content=f"Extract entities from this invoice:\n\n{raw_text}"),
    ]

    try:
        response = await llm.ainvoke(messages)
        raw_output = response.content.strip()

        # Strip markdown code fences if present
        raw_output = re.sub(r"^```json\s*", "", raw_output)
        raw_output = re.sub(r"\s*```$", "", raw_output)

        extracted = json.loads(raw_output)
        tokens_used = response.usage_metadata.get("total_tokens", 0) if hasattr(response, "usage_metadata") else 0

    except (json.JSONDecodeError, Exception) as e:
        extracted = {}
        tokens_used = 0
        return {
            "extraction_confidence": 0.1,
            "extraction_notes": f"LLM extraction failed: {str(e)}",
            "audit_trail": [_log("extraction", f"Extraction failed: {e}", "error")],
        }

    # Persist extracted data to database
    async with get_session() as session:
        invoice = await session.get(Invoice, invoice_id)
        if invoice:
            invoice.vendor_name_raw = extracted.get("vendor_name")
            invoice.amount = extracted.get("amount") or invoice.amount
            invoice.invoice_date = extracted.get("invoice_date") or invoice.invoice_date
            invoice.due_date = extracted.get("due_date") or invoice.due_date
            invoice.po_number_raw = extracted.get("po_number") or invoice.po_number_raw
            invoice.description = extracted.get("description") or invoice.description
            invoice.line_items = extracted.get("line_items") or invoice.line_items
            invoice.confidence_score = extracted.get("confidence", 0.5)
            invoice.extraction_notes = extracted.get("extraction_notes", "")
            invoice.status = InvoiceStatus.VALIDATING

            log = AuditLog(
                invoice_id=invoice_id,
                agent_name="extraction_agent",
                action="Entity extraction completed",
                details=f"Vendor: {extracted.get('vendor_name')}, Amount: {extracted.get('amount')}, Confidence: {extracted.get('confidence')}",
                duration_ms=int((time.time() - start_ms) * 1000),
            )
            session.add(log)

    return {
        "extracted_vendor_name": extracted.get("vendor_name"),
        "extracted_amount": extracted.get("amount"),
        "extracted_invoice_date": extracted.get("invoice_date"),
        "extracted_due_date": extracted.get("due_date"),
        "extracted_po_number": extracted.get("po_number"),
        "extracted_description": extracted.get("description"),
        "extracted_line_items": extracted.get("line_items", []),
        "extraction_confidence": extracted.get("confidence", 0.5),
        "extraction_notes": extracted.get("extraction_notes", ""),
        "total_llm_calls": state.get("total_llm_calls", 0) + 1,
        "total_tokens_used": state.get("total_tokens_used", 0) + tokens_used,
        "current_agent": "extraction",
        "audit_trail": [
            _log(
                "extraction",
                f"Extracted: vendor={extracted.get('vendor_name')}, "
                f"amount=${extracted.get('amount')}, confidence={extracted.get('confidence'):.2f}",
            )
        ],
    }


def _log(agent: str, message: str, level: str = "info") -> dict:
    return {
        "agent": agent,
        "message": message,
        "level": level,
        "timestamp": datetime.utcnow().isoformat(),
    }
