"""
SQLAlchemy ORM models for the invoice agent system.
All tables stored in SQLite — no Docker or external database needed.
"""
import enum
from datetime import datetime
from sqlalchemy import (
    String, Float, Integer, DateTime, Text, Boolean,
    ForeignKey, Enum as SAEnum, JSON
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


# ── Enums ──────────────────────────────────────────────────────────────────────

class InvoiceStatus(str, enum.Enum):
    PENDING       = "pending"        # Just received
    EXTRACTING    = "extracting"     # Entity extraction in progress
    VALIDATING    = "validating"     # Business rule validation
    ANOMALY_CHECK = "anomaly_check"  # Anomaly detection running
    REVIEW        = "review"         # Awaiting human approval
    APPROVED      = "approved"       # Approved for payment
    REJECTED      = "rejected"       # Rejected — requires resubmission
    PAID          = "paid"           # Payment processed


class AnomalyType(str, enum.Enum):
    DUPLICATE          = "duplicate"
    AMOUNT_OUTLIER     = "amount_outlier"
    UNKNOWN_VENDOR     = "unknown_vendor"
    MISSING_PO         = "missing_po"
    DATE_INCONSISTENCY = "date_inconsistency"
    RULE_VIOLATION     = "rule_violation"


class RuleResult(str, enum.Enum):
    PASS = "pass"
    FAIL = "fail"
    WARN = "warn"


# ── Tables ─────────────────────────────────────────────────────────────────────

class Vendor(Base):
    __tablename__ = "vendors"

    id:           Mapped[int]   = mapped_column(Integer, primary_key=True)
    name:         Mapped[str]   = mapped_column(String(200), unique=True, nullable=False)
    code:         Mapped[str]   = mapped_column(String(50), unique=True, nullable=False)
    is_approved:  Mapped[bool]  = mapped_column(Boolean, default=True)
    payment_terms: Mapped[int]  = mapped_column(Integer, default=30)   # days
    max_invoice_amount: Mapped[float] = mapped_column(Float, default=50000.0)
    created_at:   Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    invoices: Mapped[list["Invoice"]] = relationship(back_populates="vendor")

    def __repr__(self) -> str:
        return f"<Vendor {self.code}: {self.name}>"


class PurchaseOrder(Base):
    __tablename__ = "purchase_orders"

    id:          Mapped[int]   = mapped_column(Integer, primary_key=True)
    po_number:   Mapped[str]   = mapped_column(String(50), unique=True, nullable=False)
    vendor_id:   Mapped[int]   = mapped_column(ForeignKey("vendors.id"))
    amount:      Mapped[float] = mapped_column(Float, nullable=False)
    is_open:     Mapped[bool]  = mapped_column(Boolean, default=True)
    created_at:  Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    vendor: Mapped["Vendor"] = relationship()

    def __repr__(self) -> str:
        return f"<PO {self.po_number}: ${self.amount:.2f}>"


class Invoice(Base):
    __tablename__ = "invoices"

    id:             Mapped[int]   = mapped_column(Integer, primary_key=True)
    invoice_number: Mapped[str]   = mapped_column(String(100), nullable=False)
    vendor_id:      Mapped[int | None] = mapped_column(ForeignKey("vendors.id"), nullable=True)
    po_id:          Mapped[int | None] = mapped_column(ForeignKey("purchase_orders.id"), nullable=True)

    # Extracted fields
    raw_text:        Mapped[str | None]   = mapped_column(Text)
    vendor_name_raw: Mapped[str | None]   = mapped_column(String(200))
    amount:          Mapped[float | None] = mapped_column(Float)
    invoice_date:    Mapped[str | None]   = mapped_column(String(50))
    due_date:        Mapped[str | None]   = mapped_column(String(50))
    po_number_raw:   Mapped[str | None]   = mapped_column(String(50))
    description:     Mapped[str | None]   = mapped_column(Text)
    line_items:      Mapped[dict | None]  = mapped_column(JSON)

    # Pipeline state
    status:          Mapped[str] = mapped_column(
        SAEnum(InvoiceStatus), default=InvoiceStatus.PENDING
    )
    confidence_score: Mapped[float | None] = mapped_column(Float)   # extraction confidence
    extraction_notes: Mapped[str | None]   = mapped_column(Text)

    # Timestamps
    received_at:  Mapped[datetime]      = mapped_column(DateTime, default=datetime.utcnow)
    processed_at: Mapped[datetime|None] = mapped_column(DateTime, nullable=True)
    approved_at:  Mapped[datetime|None] = mapped_column(DateTime, nullable=True)

    # Human review fields
    reviewer_notes:  Mapped[str | None] = mapped_column(Text)
    reviewer_action: Mapped[str | None] = mapped_column(String(20))

    vendor: Mapped["Vendor | None"]        = relationship(back_populates="invoices")
    po:     Mapped["PurchaseOrder | None"] = relationship()
    anomalies: Mapped[list["Anomaly"]]     = relationship(back_populates="invoice", cascade="all, delete-orphan")
    audit_logs: Mapped[list["AuditLog"]]   = relationship(back_populates="invoice", cascade="all, delete-orphan")
    validation_results: Mapped[list["ValidationResult"]] = relationship(back_populates="invoice", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<Invoice {self.invoice_number}: ${self.amount} [{self.status}]>"


class Anomaly(Base):
    __tablename__ = "anomalies"

    id:           Mapped[int] = mapped_column(Integer, primary_key=True)
    invoice_id:   Mapped[int] = mapped_column(ForeignKey("invoices.id"), nullable=False)
    anomaly_type: Mapped[str] = mapped_column(SAEnum(AnomalyType), nullable=False)
    severity:     Mapped[str] = mapped_column(String(20), default="medium")   # low/medium/high
    description:  Mapped[str] = mapped_column(Text, nullable=False)
    confidence:   Mapped[float] = mapped_column(Float, default=0.8)
    resolved:     Mapped[bool]  = mapped_column(Boolean, default=False)
    detected_at:  Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    invoice: Mapped["Invoice"] = relationship(back_populates="anomalies")


class ValidationResult(Base):
    __tablename__ = "validation_results"

    id:         Mapped[int] = mapped_column(Integer, primary_key=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id"), nullable=False)
    rule_name:  Mapped[str] = mapped_column(String(100), nullable=False)
    result:     Mapped[str] = mapped_column(SAEnum(RuleResult), nullable=False)
    message:    Mapped[str] = mapped_column(Text)
    checked_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    invoice: Mapped["Invoice"] = relationship(back_populates="validation_results")


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id:         Mapped[int]   = mapped_column(Integer, primary_key=True)
    invoice_id: Mapped[int]   = mapped_column(ForeignKey("invoices.id"), nullable=False)
    agent_name: Mapped[str]   = mapped_column(String(100), nullable=False)
    action:     Mapped[str]   = mapped_column(String(200), nullable=False)
    details:    Mapped[str | None] = mapped_column(Text)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    invoice: Mapped["Invoice"] = relationship(back_populates="audit_logs")

    def __repr__(self) -> str:
        return f"<AuditLog [{self.agent_name}]: {self.action}>"
