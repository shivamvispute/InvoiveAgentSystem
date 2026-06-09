"""
Agent 3 — Validation Agent
Responsibility: apply deterministic business rules against extracted invoice data.
Demonstrates: rule engine, database lookups, structured results, partial failures.
"""
import time
from datetime import datetime, date

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from agents.state import AgentState
from db.connection import get_session
from db.models import AuditLog, Invoice, InvoiceStatus, PurchaseOrder, RuleResult, ValidationResult, Vendor

# Business rules — each returns (RuleResult, message)
RULES = [
    "vendor_approved",
    "amount_within_vendor_limit",
    "po_reference_valid",
    "amount_matches_po",
    "invoice_date_not_future",
    "required_fields_present",
]


async def validation_agent(state: AgentState) -> dict:
    """Run deterministic business rules on the extracted invoice data."""
    start_ms = time.time()
    invoice_id = state["invoice_id"]

    results: list[dict] = []
    warnings: list[str] = []
    all_passed = True

    async with get_session() as session:
        result_row = await session.execute(
            select(Invoice)
            .options(selectinload(Invoice.vendor), selectinload(Invoice.po))
            .where(Invoice.id == invoice_id)
        )
        invoice = result_row.scalar_one_or_none()
        if not invoice:
            return {
                "validation_passed": False,
                "validation_results": [{"rule": "load_invoice", "result": "fail", "message": "Invoice not found"}],
                "audit_trail": [_log("validation", "Invoice not found", "error")],
            }

        vendor = invoice.vendor
        po = invoice.po

        # Rule 1: Vendor must be approved
        result, msg = _check_vendor_approved(vendor)
        results.append({"rule": "vendor_approved", "result": result.value, "message": msg})
        db_result = ValidationResult(invoice_id=invoice_id, rule_name="vendor_approved", result=result, message=msg)
        session.add(db_result)
        if result == RuleResult.FAIL:
            all_passed = False

        # Rule 2: Amount within vendor limit
        amount = state.get("extracted_amount") or invoice.amount
        result, msg = _check_amount_limit(vendor, amount)
        results.append({"rule": "amount_within_vendor_limit", "result": result.value, "message": msg})
        db_result = ValidationResult(invoice_id=invoice_id, rule_name="amount_within_vendor_limit", result=result, message=msg)
        session.add(db_result)
        if result == RuleResult.FAIL:
            all_passed = False
        elif result == RuleResult.WARN:
            warnings.append(msg)

        # Rule 3: PO reference valid (if PO number is present)
        po_number = state.get("extracted_po_number") or invoice.po_number_raw
        result, msg = _check_po_valid(po, po_number)
        results.append({"rule": "po_reference_valid", "result": result.value, "message": msg})
        db_result = ValidationResult(invoice_id=invoice_id, rule_name="po_reference_valid", result=result, message=msg)
        session.add(db_result)
        if result == RuleResult.FAIL:
            all_passed = False

        # Rule 4: Invoice amount doesn't exceed PO amount
        result, msg = _check_amount_vs_po(po, amount)
        results.append({"rule": "amount_matches_po", "result": result.value, "message": msg})
        db_result = ValidationResult(invoice_id=invoice_id, rule_name="amount_matches_po", result=result, message=msg)
        session.add(db_result)
        if result == RuleResult.FAIL:
            all_passed = False
        elif result == RuleResult.WARN:
            warnings.append(msg)

        # Rule 5: Invoice date not in the future
        invoice_date_str = state.get("extracted_invoice_date") or invoice.invoice_date
        result, msg = _check_date_not_future(invoice_date_str)
        results.append({"rule": "invoice_date_not_future", "result": result.value, "message": msg})
        db_result = ValidationResult(invoice_id=invoice_id, rule_name="invoice_date_not_future", result=result, message=msg)
        session.add(db_result)
        if result == RuleResult.FAIL:
            all_passed = False

        # Rule 6: Required fields present
        result, msg = _check_required_fields(state)
        results.append({"rule": "required_fields_present", "result": result.value, "message": msg})
        db_result = ValidationResult(invoice_id=invoice_id, rule_name="required_fields_present", result=result, message=msg)
        session.add(db_result)
        if result == RuleResult.FAIL:
            all_passed = False

        invoice.status = InvoiceStatus.ANOMALY_CHECK
        log = AuditLog(
            invoice_id=invoice_id,
            agent_name="validation_agent",
            action=f"Business rule validation: {'PASSED' if all_passed else 'FAILED'}",
            details=f"{sum(1 for r in results if r['result'] == 'pass')}/{len(results)} rules passed",
            duration_ms=int((time.time() - start_ms) * 1000),
        )
        session.add(log)

    return {
        "validation_passed": all_passed,
        "validation_results": results,
        "validation_warnings": warnings,
        "current_agent": "validation",
        "audit_trail": [
            _log(
                "validation",
                f"Validation {'passed' if all_passed else 'failed'}: "
                f"{sum(1 for r in results if r['result'] == 'pass')}/{len(results)} rules passed",
            )
        ],
    }


# ── Individual rule checks ─────────────────────────────────────────────────────

def _check_vendor_approved(vendor: Vendor | None) -> tuple[RuleResult, str]:
    if vendor is None:
        return RuleResult.FAIL, "Vendor not found in approved vendor list"
    if not vendor.is_approved:
        return RuleResult.FAIL, f"Vendor '{vendor.name}' is not approved for payments"
    return RuleResult.PASS, f"Vendor '{vendor.name}' is approved"


def _check_amount_limit(vendor: Vendor | None, amount: float | None) -> tuple[RuleResult, str]:
    if amount is None:
        return RuleResult.FAIL, "Invoice amount could not be determined"
    if vendor is None:
        return RuleResult.WARN, "Cannot check amount limit — vendor unknown"
    if amount > vendor.max_invoice_amount:
        return RuleResult.FAIL, (
            f"Amount ${amount:,.2f} exceeds vendor limit ${vendor.max_invoice_amount:,.2f}"
        )
    if amount > vendor.max_invoice_amount * 0.9:
        return RuleResult.WARN, f"Amount ${amount:,.2f} is within 10% of vendor limit"
    return RuleResult.PASS, f"Amount ${amount:,.2f} within vendor limit ${vendor.max_invoice_amount:,.2f}"


def _check_po_valid(po: PurchaseOrder | None, po_number: str | None) -> tuple[RuleResult, str]:
    if not po_number:
        return RuleResult.WARN, "No PO number referenced — may require manual approval"
    if po is None:
        return RuleResult.FAIL, f"PO number '{po_number}' not found in system"
    if not po.is_open:
        return RuleResult.FAIL, f"PO {po_number} is closed and cannot receive invoices"
    return RuleResult.PASS, f"PO {po_number} is valid and open"


def _check_amount_vs_po(po: PurchaseOrder | None, amount: float | None) -> tuple[RuleResult, str]:
    if po is None or amount is None:
        return RuleResult.WARN, "Cannot verify amount against PO — PO or amount missing"
    if amount > po.amount * 1.1:  # Allow 10% over-run
        return RuleResult.FAIL, f"Invoice ${amount:,.2f} exceeds PO ${po.amount:,.2f} by more than 10%"
    if amount > po.amount:
        return RuleResult.WARN, f"Invoice slightly over PO amount (${amount:,.2f} vs PO ${po.amount:,.2f})"
    return RuleResult.PASS, f"Invoice amount ${amount:,.2f} matches PO ${po.amount:,.2f}"


def _check_date_not_future(invoice_date_str: str | None) -> tuple[RuleResult, str]:
    if not invoice_date_str:
        return RuleResult.WARN, "Invoice date not found"
    try:
        invoice_date = date.fromisoformat(invoice_date_str[:10])
        if invoice_date > date.today():
            return RuleResult.FAIL, f"Invoice date {invoice_date_str} is in the future"
        return RuleResult.PASS, f"Invoice date {invoice_date_str} is valid"
    except ValueError:
        return RuleResult.WARN, f"Could not parse invoice date: {invoice_date_str}"


def _check_required_fields(state: AgentState) -> tuple[RuleResult, str]:
    missing = []
    if not state.get("extracted_vendor_name"):
        missing.append("vendor name")
    if not state.get("extracted_amount"):
        missing.append("amount")
    if not state.get("extracted_invoice_date"):
        missing.append("invoice date")
    if missing:
        return RuleResult.FAIL, f"Required fields missing: {', '.join(missing)}"
    return RuleResult.PASS, "All required fields present"


def _log(agent: str, message: str, level: str = "info") -> dict:
    return {
        "agent": agent,
        "message": message,
        "level": level,
        "timestamp": datetime.utcnow().isoformat(),
    }
