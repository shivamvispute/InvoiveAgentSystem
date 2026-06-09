"""
CLI demo script — runs the full invoice pipeline in your terminal.
Shows colorized agent output without needing a browser.
Usage: python demo.py
"""
import asyncio
import sys
import os

# Force UTF-8 output on Windows so Rich can render box-drawing / check chars
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# Make sure we can import from the project root
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich import box

# force=True prevents Rich from falling back to the legacy Win32 renderer
console = Console(force_terminal=True, highlight=False)


async def run_demo():
    from db.connection import init_db
    from db.seed import seed
    from db.models import InvoiceStatus
    from db.connection import get_session
    from db.models import Invoice
    from sqlalchemy import select
    from workflow.runner import run_invoice_pipeline, resume_pipeline_with_decision

    console.print(Panel.fit(
        "[bold blue]Invoice Agent System[/bold blue]\n"
        "[dim]LangGraph + MCP + OpenTelemetry + Groq (Free)[/dim]",
        border_style="blue",
    ))

    # Init DB + seed
    console.print("\n[yellow]Initializing database...[/yellow]")
    await init_db()
    await seed()
    console.print("[green]OK Database ready[/green]")

    # Fetch pending invoices
    async with get_session() as session:
        result = await session.execute(
            select(Invoice).where(Invoice.status == InvoiceStatus.PENDING).limit(6)
        )
        invoices = result.scalars().all()

    if not invoices:
        console.print("[red]No pending invoices found. Run: python -m db.seed --reset[/red]")
        return

    console.print(f"\n[cyan]Found {len(invoices)} pending invoices[/cyan]\n")

    # Show invoice table
    table = Table(box=box.ROUNDED, show_header=True, header_style="bold cyan")
    table.add_column("ID", style="dim", width=5)
    table.add_column("Invoice #")
    table.add_column("Vendor")
    table.add_column("Amount", justify="right")
    for inv in invoices:
        table.add_row(
            str(inv.id),
            inv.invoice_number,
            inv.vendor_name_raw or "-",
            f"${inv.amount:,.2f}" if inv.amount else "-",
        )
    console.print(table)

    # Process first 3 invoices
    console.print("\n[bold]Processing invoices through agent pipeline...[/bold]\n")

    for inv in invoices[:3]:
        console.print(f"\n[bold cyan]{'='*60}[/bold cyan]")
        console.print(f"[bold]Invoice:[/bold] {inv.invoice_number} | Amount: ${inv.amount:,.2f}")

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            agents = [
                ("[>] Ingestion Agent", "Receiving invoice..."),
                ("[?] Extraction Agent", "Extracting entities with LLM..."),
                ("[+] Validation Agent", "Running 6 business rules..."),
                ("[!] Anomaly Agent", "Statistical + LLM anomaly detection..."),
            ]
            task = progress.add_task(agents[0][1], total=None)

            final_state, thread_id = await run_invoice_pipeline(
                invoice_id=inv.id,
                invoice_number=inv.invoice_number,
                raw_text=inv.raw_text or "",
                with_hitl=True,
            )
            progress.update(task, description="[green]Pipeline complete[/green]")

        # Print results
        _print_results(inv.invoice_number, final_state, thread_id)

        # If awaiting review, simulate human decision
        if final_state.get("requires_human_review"):
            console.print("\n[yellow]>> Invoice requires human review[/yellow]")
            console.print("[dim]Simulating reviewer decision: 'override' (approve despite anomalies)[/dim]")
            final_state = await resume_pipeline_with_decision(
                thread_id=thread_id,
                human_decision="override",
                human_notes="Demo: reviewed and approved by system administrator",
            )
            console.print("[green]OK Review submitted -- pipeline resumed[/green]")

    console.print("\n[bold green]Demo complete![/bold green]")
    console.print("\nTo run the full web dashboard:")
    console.print("[bold cyan]  python main.py[/bold cyan]")
    console.print("Then open: [bold]http://localhost:8000[/bold]\n")


def _print_results(invoice_number: str, state: dict, thread_id: str):
    console.print()

    # Extraction results
    console.print("[bold]Extracted Data:[/bold]")
    console.print(f"  Vendor: [cyan]{state.get('extracted_vendor_name', '-')}[/cyan]")
    console.print(f"  Amount: [cyan]${state.get('extracted_amount', 0):,.2f}[/cyan]")
    console.print(f"  Date:   [cyan]{state.get('extracted_invoice_date', '-')}[/cyan]")
    console.print(f"  PO:     [cyan]{state.get('extracted_po_number', '-')}[/cyan]")
    conf = state.get('extraction_confidence', 0)
    conf_color = "green" if conf > 0.8 else "yellow" if conf > 0.5 else "red"
    console.print(f"  Confidence: [{conf_color}]{conf:.0%}[/{conf_color}]")

    # Validation
    val_results = state.get("validation_results", [])
    passes = sum(1 for r in val_results if r.get("result") == "pass")
    fails  = sum(1 for r in val_results if r.get("result") == "fail")
    warns  = sum(1 for r in val_results if r.get("result") == "warn")
    console.print(f"\n[bold]Validation:[/bold] [green]{passes} passed[/green] · [red]{fails} failed[/red] · [yellow]{warns} warnings[/yellow]")
    for r in val_results:
        icon = {"pass": "[green]PASS[/green]", "fail": "[red]FAIL[/red]", "warn": "[yellow]WARN[/yellow]"}.get(r["result"], "?")
        console.print(f"  {icon} {r.get('rule', '').replace('_', ' ')}: [dim]{r.get('message', '')}[/dim]")

    # Anomalies
    anomalies = state.get("anomalies_found", [])
    risk = state.get("anomaly_risk_level", "none")
    risk_color = {"none": "green", "low": "blue", "medium": "yellow", "high": "orange1", "critical": "red"}.get(risk, "white")
    console.print(f"\n[bold]Anomaly Detection:[/bold] risk=[{risk_color}]{risk.upper()}[/{risk_color}] | {len(anomalies)} anomalies")
    for a in anomalies:
        sev_color = {"critical": "red", "high": "orange1", "medium": "yellow", "low": "dim"}.get(a.get("severity"), "white")
        console.print(f"  [{sev_color}]^ {a.get('type','').replace('_',' ').upper()}[/{sev_color}]: {a.get('description','')[:80]}")

    # LLM usage
    console.print(f"\n[dim]LLM calls: {state.get('total_llm_calls', 0)} | "
                  f"Tokens: {state.get('total_tokens_used', 0)} | "
                  f"Thread: {thread_id[:8]}...[/dim]")


if __name__ == "__main__":
    asyncio.run(run_demo())
