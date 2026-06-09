"""
Agent 4 — Anomaly Detection Agent
Responsibility: statistical + LLM-based anomaly detection.
Demonstrates: hybrid classical ML + LLM reasoning, risk scoring, guardrails.
"""
import json
import time
from datetime import datetime

import numpy as np
from langchain_core.messages import HumanMessage, SystemMessage
from sqlalchemy import select

from agents.llm import get_llm
from agents.state import AgentState
from config import settings
from db.connection import get_session
from db.models import Anomaly, AnomalyType, AuditLog, Invoice, InvoiceStatus

SENSITIVITY_THRESHOLDS = {
    "low":    {"z_score": 3.0, "llm_confidence": 0.85},
    "medium": {"z_score": 2.0, "llm_confidence": 0.70},
    "high":   {"z_score": 1.5, "llm_confidence": 0.55},
}

ANOMALY_SYSTEM_PROMPT = """You are a financial fraud detection AI specializing in invoice anomalies.
Analyze the invoice data and validation results to identify potential anomalies or fraud indicators.

Respond with valid JSON only:
{
  "anomalies": [
    {
      "type": "one of: duplicate|amount_outlier|unknown_vendor|missing_po|date_inconsistency|rule_violation",
      "severity": "low|medium|high|critical",
      "description": "specific explanation of what was detected",
      "confidence": 0.0-1.0
    }
  ],
  "overall_risk": "none|low|medium|high|critical",
  "requires_human_review": true/false,
  "reasoning": "brief explanation of your overall assessment"
}

Consider: duplicate submissions, unusually high amounts, vendor inconsistencies,
missing required references, suspicious timing patterns."""


async def anomaly_detection_agent(state: AgentState) -> dict:
    """Hybrid anomaly detection: statistical checks + LLM reasoning."""
    start_ms = time.time()
    invoice_id = state["invoice_id"]
    anomalies: list[dict] = []

    # ── Step 1: Statistical anomaly checks ────────────────────────────────────
    stat_anomalies = await _statistical_checks(invoice_id, state)
    anomalies.extend(stat_anomalies)

    # ── Step 2: LLM-based anomaly reasoning ───────────────────────────────────
    llm_anomalies, risk_level, requires_review, tokens = await _llm_anomaly_check(state, anomalies)
    anomalies.extend(llm_anomalies)

    # ── Step 3: Override requires_review if any critical/high anomaly found ───
    if any(a.get("severity") in ("critical", "high") for a in anomalies):
        requires_review = True
        risk_level = "high" if risk_level not in ("critical",) else risk_level

    # ── Step 4: Persist anomalies ──────────────────────────────────────────────
    async with get_session() as session:
        for a in anomalies:
            anomaly = Anomaly(
                invoice_id=invoice_id,
                anomaly_type=_map_anomaly_type(a.get("type", "rule_violation")),
                severity=a.get("severity", "medium"),
                description=a.get("description", ""),
                confidence=a.get("confidence", 0.7),
            )
            session.add(anomaly)

        invoice = await session.get(Invoice, invoice_id)
        if invoice:
            invoice.status = InvoiceStatus.REVIEW if requires_review else InvoiceStatus.APPROVED

        log = AuditLog(
            invoice_id=invoice_id,
            agent_name="anomaly_detection_agent",
            action=f"Anomaly scan: {len(anomalies)} anomalies, risk={risk_level}",
            details=f"Requires review: {requires_review}",
            duration_ms=int((time.time() - start_ms) * 1000),
        )
        session.add(log)

    return {
        "anomalies_found": anomalies,
        "anomaly_risk_level": risk_level,
        "requires_human_review": requires_review,
        "current_agent": "anomaly_detection",
        "total_llm_calls": state.get("total_llm_calls", 0) + 1,
        "total_tokens_used": state.get("total_tokens_used", 0) + tokens,
        "audit_trail": [
            _log(
                "anomaly_detection",
                f"Found {len(anomalies)} anomalies — risk: {risk_level} — review: {requires_review}",
                "warn" if anomalies else "info",
            )
        ],
    }


async def _statistical_checks(invoice_id: int, state: AgentState) -> list[dict]:
    """Deterministic statistical anomaly checks — no LLM needed."""
    anomalies = []
    amount = state.get("extracted_amount")
    thresholds = SENSITIVITY_THRESHOLDS[settings.anomaly_sensitivity]

    async with get_session() as session:
        # Check 1: Duplicate invoice number
        from sqlalchemy import select, func
        from db.models import Invoice
        dup_count = await session.scalar(
            select(func.count()).where(
                Invoice.invoice_number == state.get("invoice_number"),
                Invoice.id != invoice_id,
            )
        )
        if dup_count and dup_count > 0:
            anomalies.append({
                "type": "duplicate",
                "severity": "critical",
                "description": f"Invoice number '{state.get('invoice_number')}' already exists — possible duplicate submission",
                "confidence": 0.99,
                "source": "statistical",
            })

        # Check 2: Amount outlier vs vendor history
        if amount:
            # Fetch vendor_id directly without triggering relationship load
            vendor_id_row = await session.scalar(
                select(Invoice.vendor_id).where(Invoice.id == invoice_id)
            )
            if vendor_id_row:
                vendor_amounts = await session.execute(
                    select(Invoice.amount).where(
                        Invoice.vendor_id == vendor_id_row,
                        Invoice.amount.isnot(None),
                        Invoice.id != invoice_id,
                    )
                )
                amounts = [row[0] for row in vendor_amounts if row[0] is not None]
            else:
                amounts = []

            if len(amounts) >= 3:
                arr = np.array(amounts)
                mean, std = arr.mean(), arr.std()
                if std > 0:
                    z_score = abs(amount - mean) / std
                    if z_score > thresholds["z_score"]:
                        anomalies.append({
                            "type": "amount_outlier",
                            "severity": "high" if z_score > 3 else "medium",
                            "description": (
                                f"Amount ${amount:,.2f} is {z_score:.1f} standard deviations "
                                f"from vendor average ${mean:,.2f} (σ=${std:,.2f})"
                            ),
                            "confidence": min(0.95, 0.5 + z_score * 0.1),
                            "source": "statistical",
                        })

    return anomalies


async def _llm_anomaly_check(
    state: AgentState, existing_anomalies: list[dict]
) -> tuple[list[dict], str, bool, int]:
    """LLM reasoning pass — catches context-dependent anomalies statistical checks miss."""
    llm = get_llm(temperature=0.1)

    context = {
        "invoice_number": state.get("invoice_number"),
        "vendor_name": state.get("extracted_vendor_name"),
        "amount": state.get("extracted_amount"),
        "invoice_date": state.get("extracted_invoice_date"),
        "po_number": state.get("extracted_po_number"),
        "description": state.get("extracted_description"),
        "validation_results": state.get("validation_results", []),
        "validation_warnings": state.get("validation_warnings", []),
        "statistical_anomalies_already_detected": existing_anomalies,
        "extraction_confidence": state.get("extraction_confidence"),
    }

    messages = [
        SystemMessage(content=ANOMALY_SYSTEM_PROMPT),
        HumanMessage(content=f"Analyze this invoice for anomalies:\n\n{json.dumps(context, indent=2)}"),
    ]

    try:
        response = await llm.ainvoke(messages)
        raw = response.content.strip()
        import re
        raw = re.sub(r"^```json\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
        result = json.loads(raw)
        tokens = response.usage_metadata.get("total_tokens", 0) if hasattr(response, "usage_metadata") else 0

        # Only include LLM anomalies not already caught statistically
        existing_types = {a["type"] for a in existing_anomalies}
        new_anomalies = [
            {**a, "source": "llm"}
            for a in result.get("anomalies", [])
            if a.get("type") not in existing_types
        ]

        return (
            new_anomalies,
            result.get("overall_risk", "none"),
            result.get("requires_human_review", False),
            tokens,
        )
    except Exception as e:
        return [], "unknown", False, 0


def _map_anomaly_type(type_str: str) -> AnomalyType:
    mapping = {
        "duplicate": AnomalyType.DUPLICATE,
        "amount_outlier": AnomalyType.AMOUNT_OUTLIER,
        "unknown_vendor": AnomalyType.UNKNOWN_VENDOR,
        "missing_po": AnomalyType.MISSING_PO,
        "date_inconsistency": AnomalyType.DATE_INCONSISTENCY,
        "rule_violation": AnomalyType.RULE_VIOLATION,
    }
    return mapping.get(type_str, AnomalyType.RULE_VIOLATION)


def _log(agent: str, message: str, level: str = "info") -> dict:
    return {
        "agent": agent,
        "message": message,
        "level": level,
        "timestamp": datetime.utcnow().isoformat(),
    }
