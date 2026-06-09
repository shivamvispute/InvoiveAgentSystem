"""
Seeds the database with realistic vendors, purchase orders, and sample invoices.
Run once on startup or reset with: python -m db.seed --reset
"""
import asyncio
from datetime import datetime, timedelta

from sqlalchemy import select

from db.connection import get_session, init_db, drop_db
from db.models import Invoice, InvoiceStatus, PurchaseOrder, Vendor


VENDORS = [
    {"name": "Acme Office Supplies", "code": "ACME-001", "payment_terms": 30, "max_invoice_amount": 5000.0},
    {"name": "TechPro Solutions Inc.", "code": "TECH-002", "payment_terms": 45, "max_invoice_amount": 50000.0},
    {"name": "CloudInfra Systems", "code": "CLDI-003", "payment_terms": 30, "max_invoice_amount": 100000.0},
    {"name": "Global Consulting Group", "code": "GCON-004", "payment_terms": 60, "max_invoice_amount": 200000.0},
    {"name": "FastShip Logistics", "code": "FAST-005", "payment_terms": 15, "max_invoice_amount": 25000.0},
    {"name": "DataVault Analytics", "code": "DAVA-006", "payment_terms": 30, "max_invoice_amount": 75000.0},
    # Unapproved vendor — triggers anomaly
    {"name": "Shady Deals LLC", "code": "SHDY-007", "is_approved": False, "payment_terms": 0, "max_invoice_amount": 0.0},
]

PURCHASE_ORDERS = [
    {"po_number": "PO-2024-0001", "vendor_code": "ACME-001", "amount": 3500.00},
    {"po_number": "PO-2024-0002", "vendor_code": "TECH-002", "amount": 45000.00},
    {"po_number": "PO-2024-0003", "vendor_code": "CLDI-003", "amount": 82000.00},
    {"po_number": "PO-2024-0004", "vendor_code": "GCON-004", "amount": 150000.00},
    {"po_number": "PO-2024-0005", "vendor_code": "FAST-005", "amount": 12500.00},
    {"po_number": "PO-2024-0006", "vendor_code": "DAVA-006", "amount": 60000.00},
]

# Sample invoices — mix of clean, anomalous, and edge-case invoices
SAMPLE_INVOICES = [
    # --- Normal clean invoices ---
    {
        "invoice_number": "INV-ACME-20240115",
        "vendor_code": "ACME-001",
        "po_number": "PO-2024-0001",
        "amount": 3200.00,
        "invoice_date": "2024-01-15",
        "due_date": "2024-02-14",
        "description": "Office supplies Q1 2024 — stationery, printer cartridges, desk supplies",
        "line_items": [
            {"item": "Printer Cartridges x20", "amount": 1800.00},
            {"item": "Stationery Bundle", "amount": 800.00},
            {"item": "Desk Organizers x5", "amount": 600.00},
        ],
        "raw_text": "INVOICE\nVendor: Acme Office Supplies\nInvoice #: INV-ACME-20240115\nPO: PO-2024-0001\nAmount: $3,200.00\nDate: January 15, 2024\nDue: February 14, 2024",
        "status": "pending",
    },
    {
        "invoice_number": "INV-TECH-20240201",
        "vendor_code": "TECH-002",
        "po_number": "PO-2024-0002",
        "amount": 44500.00,
        "invoice_date": "2024-02-01",
        "due_date": "2024-03-17",
        "description": "Enterprise software licenses Q1 2024 — 50 seat ERP license renewal",
        "line_items": [
            {"item": "ERP License 50 seats x 12mo", "amount": 40000.00},
            {"item": "Implementation support 20hrs", "amount": 4500.00},
        ],
        "raw_text": "INVOICE\nVendor: TechPro Solutions Inc.\nInvoice #: INV-TECH-20240201\nPO: PO-2024-0002\nAmount: $44,500.00\nDate: February 1, 2024",
        "status": "pending",
    },

    # --- Anomaly: Amount exceeds vendor limit ---
    {
        "invoice_number": "INV-ACME-20240220",
        "vendor_code": "ACME-001",
        "po_number": "PO-2024-0001",
        "amount": 8500.00,  # Exceeds ACME max of $5,000
        "invoice_date": "2024-02-20",
        "due_date": "2024-03-21",
        "description": "Emergency office equipment procurement",
        "line_items": [
            {"item": "Standing Desks x10", "amount": 6000.00},
            {"item": "Monitor Arms x10", "amount": 2500.00},
        ],
        "raw_text": "INVOICE\nVendor: Acme Office Supplies\nInvoice #: INV-ACME-20240220\nPO: PO-2024-0001\nAmount: $8,500.00\nDate: February 20, 2024",
        "status": "pending",
    },

    # --- Anomaly: Duplicate invoice number ---
    {
        "invoice_number": "INV-ACME-20240115",  # Same as first invoice
        "vendor_code": "ACME-001",
        "po_number": "PO-2024-0001",
        "amount": 3200.00,
        "invoice_date": "2024-01-15",
        "due_date": "2024-02-14",
        "description": "Duplicate submission attempt — office supplies",
        "raw_text": "INVOICE\nVendor: Acme Office Supplies\nInvoice #: INV-ACME-20240115\nPO: PO-2024-0001\nAmount: $3,200.00",
        "status": "pending",
    },

    # --- Anomaly: Unknown vendor ---
    {
        "invoice_number": "INV-SHDY-20240301",
        "vendor_code": "SHDY-007",  # Unapproved vendor
        "po_number": None,  # No PO
        "amount": 15000.00,
        "invoice_date": "2024-03-01",
        "due_date": "2024-03-08",
        "description": "Consulting services — unspecified",
        "raw_text": "INVOICE\nVendor: Shady Deals LLC\nInvoice #: INV-SHDY-20240301\nAmount: $15,000.00\nDate: March 1, 2024",
        "status": "pending",
    },

    # --- Anomaly: Large statistical outlier ---
    {
        "invoice_number": "INV-GCON-20240310",
        "vendor_code": "GCON-004",
        "po_number": None,  # Missing PO
        "amount": 195000.00,  # Very high amount
        "invoice_date": "2024-03-10",
        "due_date": "2024-05-09",
        "description": "Strategic transformation consulting services Q1 2024",
        "raw_text": "INVOICE\nVendor: Global Consulting Group\nInvoice #: INV-GCON-20240310\nAmount: $195,000.00\nDate: March 10, 2024",
        "status": "pending",
    },
]


async def seed(reset: bool = False) -> None:
    """Populate the database with test data."""
    if reset:
        await drop_db()
    await init_db()

    async with get_session() as session:
        # Check if already seeded
        existing = await session.scalar(select(Vendor).limit(1))
        if existing and not reset:
            print("Database already seeded — skipping.")
            return

        # Seed vendors
        vendor_map: dict[str, Vendor] = {}
        for v in VENDORS:
            vendor = Vendor(
                name=v["name"],
                code=v["code"],
                is_approved=v.get("is_approved", True),
                payment_terms=v.get("payment_terms", 30),
                max_invoice_amount=v.get("max_invoice_amount", 50000.0),
            )
            session.add(vendor)
            vendor_map[v["code"]] = vendor
        await session.flush()  # get IDs

        # Seed purchase orders
        po_map: dict[str, PurchaseOrder] = {}
        for po_data in PURCHASE_ORDERS:
            vendor = vendor_map[po_data["vendor_code"]]
            po = PurchaseOrder(
                po_number=po_data["po_number"],
                vendor_id=vendor.id,
                amount=po_data["amount"],
            )
            session.add(po)
            po_map[po_data["po_number"]] = po
        await session.flush()

        # Seed sample invoices
        for inv_data in SAMPLE_INVOICES:
            vendor = vendor_map.get(inv_data["vendor_code"])
            po = po_map.get(inv_data.get("po_number", "")) if inv_data.get("po_number") else None
            invoice = Invoice(
                invoice_number=inv_data["invoice_number"],
                vendor_id=vendor.id if vendor else None,
                po_id=po.id if po else None,
                amount=inv_data["amount"],
                invoice_date=inv_data["invoice_date"],
                due_date=inv_data.get("due_date"),
                description=inv_data["description"],
                line_items=inv_data.get("line_items"),
                raw_text=inv_data.get("raw_text"),
                vendor_name_raw=vendor.name if vendor else "Unknown",
                po_number_raw=inv_data.get("po_number"),
                status=InvoiceStatus.PENDING,
            )
            session.add(invoice)

    print(f"Seeded {len(VENDORS)} vendors, {len(PURCHASE_ORDERS)} POs, {len(SAMPLE_INVOICES)} invoices.")


if __name__ == "__main__":
    import typer

    app = typer.Typer()

    @app.command()
    def main(reset: bool = typer.Option(False, help="Drop and recreate all tables")):
        asyncio.run(seed(reset=reset))

    app()
