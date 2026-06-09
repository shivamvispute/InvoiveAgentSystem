"""
Basic smoke tests — verify the pipeline runs without LLM calls.
Run: pytest tests/ -v
"""
import asyncio
import pytest
from unittest.mock import AsyncMock, patch


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.mark.asyncio
async def test_db_init():
    """Database tables can be created."""
    from db.connection import init_db, drop_db
    await drop_db()
    await init_db()  # Should not raise


@pytest.mark.asyncio
async def test_seed_idempotent():
    """Seeding twice does not raise or duplicate data."""
    from db.seed import seed
    await seed()
    await seed()  # Second call should be no-op


@pytest.mark.asyncio
async def test_vendor_lookup():
    """Vendor lookup returns expected data."""
    from db.seed import seed
    from db.connection import get_session
    from db.models import Vendor
    from sqlalchemy import select

    await seed()
    async with get_session() as session:
        vendor = await session.scalar(select(Vendor).where(Vendor.code == "ACME-001"))
        assert vendor is not None
        assert vendor.is_approved is True
        assert vendor.max_invoice_amount == 5000.0


@pytest.mark.asyncio
async def test_invoice_seeded():
    """Sample invoices are present after seed."""
    from db.seed import seed
    from db.connection import get_session
    from db.models import Invoice
    from sqlalchemy import select, func

    await seed()
    async with get_session() as session:
        count = await session.scalar(select(func.count()).select_from(Invoice))
        assert count >= 5


@pytest.mark.asyncio
async def test_validation_agent_approved_vendor():
    """Validation agent passes for approved vendor."""
    from db.seed import seed
    from db.connection import get_session
    from db.models import Invoice, InvoiceStatus
    from sqlalchemy import select
    from agents.validation_agent import validation_agent
    from agents.state import AgentState

    await seed()

    async with get_session() as session:
        invoice = await session.scalar(
            select(Invoice).where(Invoice.invoice_number == "INV-ACME-20240115")
        )
        assert invoice is not None
        invoice_id = invoice.id

    state: AgentState = {
        "invoice_id": invoice_id,
        "invoice_number": "INV-ACME-20240115",
        "raw_text": "",
        "extracted_vendor_name": "Acme Office Supplies",
        "extracted_amount": 3200.0,
        "extracted_invoice_date": "2024-01-15",
        "extracted_due_date": "2024-02-14",
        "extracted_po_number": "PO-2024-0001",
        "extracted_description": "Office supplies",
        "extracted_line_items": [],
        "extraction_confidence": 0.95,
        "extraction_notes": "",
        "validation_results": [],
        "validation_warnings": [],
        "anomalies_found": [],
        "audit_trail": [],
        "ingestion_complete": True,
        "ingestion_error": None,
        "anomaly_risk_level": "none",
        "requires_human_review": False,
        "human_decision": None,
        "human_notes": None,
        "current_agent": "validation",
        "pipeline_status": "running",
        "error_message": None,
        "total_llm_calls": 0,
        "total_tokens_used": 0,
    }

    result = await validation_agent(state)
    assert "validation_results" in result
    # Vendor approved rule should pass
    vendor_rule = next(
        (r for r in result["validation_results"] if r["rule"] == "vendor_approved"), None
    )
    assert vendor_rule is not None
    assert vendor_rule["result"] == "pass"


@pytest.mark.asyncio
async def test_graph_builds():
    """LangGraph StateGraph compiles without error."""
    from workflow.graph import build_graph
    graph = build_graph(checkpointer=None)
    assert graph is not None
