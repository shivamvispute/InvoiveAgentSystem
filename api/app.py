"""
FastAPI application — REST API for the invoice agent system.

Endpoints:
  POST /invoices/process          — submit invoice for agent pipeline
  GET  /invoices/{id}             — get invoice status + details
  GET  /invoices                  — list all invoices with filters
  POST /invoices/{id}/review      — submit human review decision (HITL resume)
  GET  /invoices/{id}/audit       — get full audit trail
  GET  /invoices/{id}/anomalies   — get detected anomalies
  GET  /health                    — health check
  GET  /metrics                   — pipeline metrics summary
"""
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, HTTPException, Depends, BackgroundTasks, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from api.tracing import setup_tracing
from db.connection import get_db, init_db
from db.models import Anomaly, AuditLog, Invoice, InvoiceStatus, ValidationResult
from db.seed import seed
from workflow.runner import run_invoice_pipeline, resume_pipeline_with_decision

# Active HITL sessions: invoice_id -> thread_id
_active_threads: dict[int, str] = {}


# ── Lifespan ───────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_tracing()
    await seed()   # idempotent — skips if already seeded
    yield


# ── App ────────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="Invoice Agent System",
    description="Multi-agent invoice processing with LangGraph, MCP, and OpenTelemetry",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

try:
    app.mount("/static", StaticFiles(directory="static"), name="static")
except Exception:
    pass  # static dir optional


# ── Pydantic schemas ───────────────────────────────────────────────────────────

class ProcessInvoiceRequest(BaseModel):
    invoice_id: int
    raw_text: Optional[str] = None


class ReviewDecisionRequest(BaseModel):
    decision: str  # approved | rejected | override
    notes: Optional[str] = ""


class InvoiceListItem(BaseModel):
    id: int
    invoice_number: str
    vendor_name: Optional[str]
    amount: Optional[float]
    status: str
    risk_level: Optional[str]
    anomaly_count: int
    received_at: str


# ── Routes ─────────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def dashboard():
    """Serve the HTML dashboard."""
    try:
        with open("static/index.html", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    except FileNotFoundError:
        return HTMLResponse(content="<h1>Invoice Agent System API</h1><p>Visit /docs for API documentation</p>")


@app.get("/health")
async def health():
    return {
        "status": "healthy",
        "service": "invoice-agent-system",
        "timestamp": datetime.utcnow().isoformat(),
        "active_pipelines": len(_active_threads),
    }


@app.post("/invoices/process")
async def process_invoice(
    request: ProcessInvoiceRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    """
    Submit an invoice for processing through the multi-agent pipeline.
    The pipeline runs in the background — poll GET /invoices/{id} for status.
    """
    invoice = await db.get(Invoice, request.invoice_id)
    if not invoice:
        raise HTTPException(status_code=404, detail=f"Invoice {request.invoice_id} not found")

    if invoice.status not in (InvoiceStatus.PENDING, InvoiceStatus.REJECTED):
        raise HTTPException(
            status_code=409,
            detail=f"Invoice already in pipeline: status={invoice.status}",
        )

    async def _run():
        final_state, thread_id = await run_invoice_pipeline(
            invoice_id=request.invoice_id,
            invoice_number=invoice.invoice_number,
            raw_text=request.raw_text or invoice.raw_text or "",
        )
        _active_threads[request.invoice_id] = thread_id

    background_tasks.add_task(_run)

    return {
        "message": "Pipeline started",
        "invoice_id": request.invoice_id,
        "status_url": f"/invoices/{request.invoice_id}",
    }


@app.get("/invoices")
async def list_invoices(
    status: Optional[str] = Query(None),
    limit: int = Query(50, le=200),
    offset: int = Query(0),
    db: AsyncSession = Depends(get_db),
):
    """List invoices with optional status filter."""
    query = select(Invoice).options(
        selectinload(Invoice.vendor),
        selectinload(Invoice.anomalies),
    ).order_by(Invoice.received_at.desc()).limit(limit).offset(offset)

    if status:
        query = query.where(Invoice.status == status)

    result = await db.execute(query)
    invoices = result.scalars().all()

    return {
        "invoices": [
            {
                "id": inv.id,
                "invoice_number": inv.invoice_number,
                "vendor_name": inv.vendor.name if inv.vendor else inv.vendor_name_raw,
                "amount": inv.amount,
                "status": inv.status,
                "confidence_score": inv.confidence_score,
                "anomaly_count": len(inv.anomalies),
                "received_at": inv.received_at.isoformat(),
                "approved_at": inv.approved_at.isoformat() if inv.approved_at else None,
                "requires_review": inv.id in _active_threads and inv.status == InvoiceStatus.REVIEW,
            }
            for inv in invoices
        ],
        "total": len(invoices),
        "offset": offset,
        "limit": limit,
    }


@app.get("/invoices/{invoice_id}")
async def get_invoice(invoice_id: int, db: AsyncSession = Depends(get_db)):
    """Get full invoice details including extracted data, validation, and anomalies."""
    result = await db.execute(
        select(Invoice)
        .options(
            selectinload(Invoice.vendor),
            selectinload(Invoice.po),
            selectinload(Invoice.anomalies),
            selectinload(Invoice.validation_results),
            selectinload(Invoice.audit_logs),
        )
        .where(Invoice.id == invoice_id)
    )
    invoice = result.scalar_one_or_none()
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")

    thread_id = _active_threads.get(invoice_id)

    return {
        "id": invoice.id,
        "invoice_number": invoice.invoice_number,
        "status": invoice.status,
        "vendor": {
            "id": invoice.vendor.id if invoice.vendor else None,
            "name": invoice.vendor.name if invoice.vendor else invoice.vendor_name_raw,
            "code": invoice.vendor.code if invoice.vendor else None,
            "is_approved": invoice.vendor.is_approved if invoice.vendor else False,
        },
        "extracted": {
            "amount": invoice.amount,
            "invoice_date": invoice.invoice_date,
            "due_date": invoice.due_date,
            "po_number": invoice.po_number_raw,
            "description": invoice.description,
            "line_items": invoice.line_items,
            "confidence_score": invoice.confidence_score,
            "extraction_notes": invoice.extraction_notes,
        },
        "validation": [
            {"rule": vr.rule_name, "result": vr.result, "message": vr.message}
            for vr in invoice.validation_results
        ],
        "anomalies": [
            {
                "type": a.anomaly_type,
                "severity": a.severity,
                "description": a.description,
                "confidence": a.confidence,
                "resolved": a.resolved,
            }
            for a in invoice.anomalies
        ],
        "review": {
            "requires_review": invoice.status == InvoiceStatus.REVIEW,
            "thread_id": thread_id,
            "reviewer_notes": invoice.reviewer_notes,
            "reviewer_action": invoice.reviewer_action,
        },
        "timestamps": {
            "received_at": invoice.received_at.isoformat(),
            "processed_at": invoice.processed_at.isoformat() if invoice.processed_at else None,
            "approved_at": invoice.approved_at.isoformat() if invoice.approved_at else None,
        },
    }


@app.post("/invoices/{invoice_id}/review")
async def submit_review_decision(
    invoice_id: int,
    request: ReviewDecisionRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Submit a human review decision to resume a paused pipeline.
    decision: 'approved' | 'rejected' | 'override'
    """
    if request.decision not in ("approved", "rejected", "override"):
        raise HTTPException(status_code=400, detail="decision must be: approved | rejected | override")

    invoice = await db.get(Invoice, invoice_id)
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")

    thread_id = _active_threads.get(invoice_id)
    if not thread_id:
        # Pipeline already completed or not started — update directly
        from db.models import InvoiceStatus, AuditLog
        invoice.status = InvoiceStatus.APPROVED if request.decision in ("approved", "override") else InvoiceStatus.REJECTED
        invoice.reviewer_notes = request.notes
        invoice.reviewer_action = request.decision
        invoice.approved_at = datetime.utcnow() if request.decision != "rejected" else None
        log = AuditLog(
            invoice_id=invoice_id,
            agent_name="api:human_review",
            action=f"Direct review decision: {request.decision.upper()}",
            details=request.notes or "",
        )
        db.add(log)
        return {"message": "Review decision applied", "decision": request.decision}

    # Resume the paused LangGraph pipeline
    final_state = await resume_pipeline_with_decision(
        thread_id=thread_id,
        human_decision=request.decision,
        human_notes=request.notes or "",
    )
    _active_threads.pop(invoice_id, None)

    return {
        "message": "Review decision submitted — pipeline resumed",
        "decision": request.decision,
        "final_status": final_state.get("pipeline_status"),
    }


@app.get("/invoices/{invoice_id}/audit")
async def get_audit_trail(invoice_id: int, db: AsyncSession = Depends(get_db)):
    """Get the complete audit trail for an invoice."""
    result = await db.execute(
        select(AuditLog)
        .where(AuditLog.invoice_id == invoice_id)
        .order_by(AuditLog.created_at.asc())
    )
    logs = result.scalars().all()

    return {
        "invoice_id": invoice_id,
        "audit_trail": [
            {
                "agent": log.agent_name,
                "action": log.action,
                "details": log.details,
                "duration_ms": log.duration_ms,
                "timestamp": log.created_at.isoformat(),
            }
            for log in logs
        ],
    }


@app.get("/metrics")
async def get_metrics(db: AsyncSession = Depends(get_db)):
    """Pipeline performance metrics for the dashboard."""
    total = await db.scalar(select(func.count()).select_from(Invoice))
    by_status = await db.execute(
        select(Invoice.status, func.count()).group_by(Invoice.status)
    )
    anomaly_count = await db.scalar(select(func.count()).select_from(Anomaly))

    return {
        "total_invoices": total or 0,
        "by_status": {row[0]: row[1] for row in by_status},
        "total_anomalies": anomaly_count or 0,
        "active_pipelines": len(_active_threads),
        "timestamp": datetime.utcnow().isoformat(),
    }
