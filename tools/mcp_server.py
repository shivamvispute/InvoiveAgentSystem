"""
MCP (Model Context Protocol) Tool Server
Exposes business tools to the agent system via MCP protocol.
This demonstrates: agents are not hardcoded to tools — they discover and call
tools dynamically through the protocol, just like production SAP/enterprise integrations.

Tools available:
  - lookup_vendor       : query vendor registry
  - lookup_purchase_order: query PO database
  - check_duplicate      : detect duplicate invoice submissions
  - get_vendor_history   : fetch invoice history for statistical baseline
  - flag_for_review      : escalate invoice to human reviewer
  - get_business_rules   : fetch current business rules config
"""
import json
import asyncio
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp import types
from sqlalchemy import select, func

from db.connection import get_session
from db.models import Invoice, Vendor, PurchaseOrder


app = Server("invoice-business-tools")


# ── Tool definitions ───────────────────────────────────────────────────────────

@app.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="lookup_vendor",
            description="Look up a vendor by name or code. Returns approval status, payment terms, and invoice limits.",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Vendor name or vendor code to look up"},
                },
                "required": ["query"],
            },
        ),
        types.Tool(
            name="lookup_purchase_order",
            description="Look up a purchase order by PO number. Returns PO status, amount, and associated vendor.",
            inputSchema={
                "type": "object",
                "properties": {
                    "po_number": {"type": "string", "description": "PO number to look up (e.g. PO-2024-0001)"},
                },
                "required": ["po_number"],
            },
        ),
        types.Tool(
            name="check_duplicate",
            description="Check if an invoice number has already been submitted. Returns duplicate count and details.",
            inputSchema={
                "type": "object",
                "properties": {
                    "invoice_number": {"type": "string", "description": "Invoice number to check"},
                    "exclude_invoice_id": {"type": "integer", "description": "Invoice ID to exclude from the check (the current invoice)"},
                },
                "required": ["invoice_number"],
            },
        ),
        types.Tool(
            name="get_vendor_history",
            description="Get invoice history for a vendor for statistical anomaly baseline. Returns amounts and dates.",
            inputSchema={
                "type": "object",
                "properties": {
                    "vendor_id": {"type": "integer", "description": "Vendor ID"},
                    "limit": {"type": "integer", "description": "Max invoices to return (default 20)"},
                },
                "required": ["vendor_id"],
            },
        ),
        types.Tool(
            name="get_business_rules",
            description="Get current business rules configuration including approval thresholds and required fields.",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        types.Tool(
            name="flag_for_review",
            description="Flag an invoice for human review with a reason. Returns confirmation.",
            inputSchema={
                "type": "object",
                "properties": {
                    "invoice_id": {"type": "integer", "description": "Invoice ID to flag"},
                    "reason": {"type": "string", "description": "Reason for escalation"},
                    "priority": {"type": "string", "enum": ["low", "medium", "high", "critical"], "description": "Review priority"},
                },
                "required": ["invoice_id", "reason"],
            },
        ),
    ]


# ── Tool implementations ───────────────────────────────────────────────────────

@app.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[types.TextContent]:
    """Dispatch MCP tool calls to their implementations."""
    try:
        if name == "lookup_vendor":
            result = await _lookup_vendor(arguments["query"])
        elif name == "lookup_purchase_order":
            result = await _lookup_purchase_order(arguments["po_number"])
        elif name == "check_duplicate":
            result = await _check_duplicate(
                arguments["invoice_number"],
                arguments.get("exclude_invoice_id"),
            )
        elif name == "get_vendor_history":
            result = await _get_vendor_history(
                arguments["vendor_id"],
                arguments.get("limit", 20),
            )
        elif name == "get_business_rules":
            result = _get_business_rules()
        elif name == "flag_for_review":
            result = await _flag_for_review(
                arguments["invoice_id"],
                arguments["reason"],
                arguments.get("priority", "medium"),
            )
        else:
            result = {"error": f"Unknown tool: {name}"}

    except Exception as e:
        result = {"error": str(e), "tool": name}

    return [types.TextContent(type="text", text=json.dumps(result, indent=2))]


# ── Tool implementations ───────────────────────────────────────────────────────

async def _lookup_vendor(query: str) -> dict:
    async with get_session() as session:
        # Try exact code match first, then name LIKE
        vendor = await session.scalar(
            select(Vendor).where(Vendor.code == query.upper())
        )
        if not vendor:
            vendor = await session.scalar(
                select(Vendor).where(Vendor.name.ilike(f"%{query}%"))
            )

        if not vendor:
            return {"found": False, "query": query, "message": "Vendor not found in registry"}

        return {
            "found": True,
            "vendor_id": vendor.id,
            "name": vendor.name,
            "code": vendor.code,
            "is_approved": vendor.is_approved,
            "payment_terms_days": vendor.payment_terms,
            "max_invoice_amount": vendor.max_invoice_amount,
        }


async def _lookup_purchase_order(po_number: str) -> dict:
    async with get_session() as session:
        po = await session.scalar(
            select(PurchaseOrder).where(PurchaseOrder.po_number == po_number)
        )
        if not po:
            return {"found": False, "po_number": po_number, "message": "PO not found"}

        vendor = await session.get(Vendor, po.vendor_id)
        return {
            "found": True,
            "po_id": po.id,
            "po_number": po.po_number,
            "amount": po.amount,
            "is_open": po.is_open,
            "vendor_name": vendor.name if vendor else "Unknown",
            "vendor_id": po.vendor_id,
        }


async def _check_duplicate(invoice_number: str, exclude_id: int | None = None) -> dict:
    async with get_session() as session:
        query = select(Invoice).where(Invoice.invoice_number == invoice_number)
        if exclude_id:
            query = query.where(Invoice.id != exclude_id)

        results = await session.execute(query)
        duplicates = results.scalars().all()

        return {
            "is_duplicate": len(duplicates) > 0,
            "duplicate_count": len(duplicates),
            "existing_invoices": [
                {
                    "id": inv.id,
                    "status": inv.status,
                    "received_at": inv.received_at.isoformat(),
                }
                for inv in duplicates
            ],
        }


async def _get_vendor_history(vendor_id: int, limit: int = 20) -> dict:
    async with get_session() as session:
        results = await session.execute(
            select(Invoice.amount, Invoice.invoice_date, Invoice.status)
            .where(Invoice.vendor_id == vendor_id, Invoice.amount.isnot(None))
            .order_by(Invoice.received_at.desc())
            .limit(limit)
        )
        rows = results.all()
        amounts = [r[0] for r in rows if r[0] is not None]

        import numpy as np
        stats = {}
        if amounts:
            arr = np.array(amounts)
            stats = {
                "mean": round(float(arr.mean()), 2),
                "std": round(float(arr.std()), 2),
                "min": round(float(arr.min()), 2),
                "max": round(float(arr.max()), 2),
                "count": len(amounts),
            }

        return {
            "vendor_id": vendor_id,
            "invoice_count": len(rows),
            "amount_stats": stats,
            "recent_invoices": [
                {"amount": r[0], "date": r[1], "status": r[2]}
                for r in rows[:10]
            ],
        }


def _get_business_rules() -> dict:
    from config import settings
    return {
        "auto_approve_threshold": settings.auto_approve_threshold,
        "anomaly_sensitivity": settings.anomaly_sensitivity,
        "required_fields": ["vendor_name", "amount", "invoice_date"],
        "po_required_above": 5000.0,
        "max_days_past_due": 90,
        "allowed_over_po_pct": 10.0,
    }


async def _flag_for_review(invoice_id: int, reason: str, priority: str = "medium") -> dict:
    from db.models import InvoiceStatus, AuditLog
    from datetime import datetime

    async with get_session() as session:
        invoice = await session.get(Invoice, invoice_id)
        if not invoice:
            return {"success": False, "error": "Invoice not found"}

        invoice.status = InvoiceStatus.REVIEW
        log = AuditLog(
            invoice_id=invoice_id,
            agent_name="mcp_tool:flag_for_review",
            action=f"Flagged for {priority.upper()} priority review",
            details=reason,
        )
        session.add(log)

    return {
        "success": True,
        "invoice_id": invoice_id,
        "priority": priority,
        "reason": reason,
        "message": f"Invoice {invoice_id} queued for {priority} review",
    }


# ── Server entrypoint ──────────────────────────────────────────────────────────

async def run_mcp_server():
    """Run the MCP server over stdio — connects to the agent system."""
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(run_mcp_server())
