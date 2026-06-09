# Invoice Agent System

A production-intent multi-agent pipeline for end-to-end invoice processing, built with LangGraph, MCP, FastAPI, and OpenTelemetry. Designed to demonstrate ML Engineer capabilities for SAP BTP and enterprise AI roles.

---

## Architecture

```
Invoice Input
     │
     ▼
┌─────────────────────────────────────────────────────────────┐
│                    LangGraph StateGraph                      │
│                                                              │
│  📥 Ingestion  →  🔍 Extraction  →  ✅ Validation           │
│                                           │                  │
│                                    🚨 Anomaly Detection      │
│                                   ┌──────┴──────┐           │
│                                   │             │           │
│                              👤 Human        🏁 Auto        │
│                                Review        Approve        │
└─────────────────────────────────────────────────────────────┘
         │                                │
     MCP Tools                    OpenTelemetry
  (vendor lookup,                  (traces every
   PO validation,                   agent decision)
   duplicate check)
         │
     FastAPI REST API
         │
     SQLite Database
```

### Agent Responsibilities

| Agent | Technology | What It Does |
|---|---|---|
| **Ingestion** | Python async | Receives invoice, loads raw text, marks pipeline start |
| **Extraction** | LangGraph + Groq LLM | Structured entity extraction with JSON output + confidence scoring |
| **Validation** | SQLAlchemy + Rules Engine | 6 deterministic business rules (vendor approval, PO matching, amount limits) |
| **Anomaly Detection** | NumPy (z-score) + LLM | Hybrid: statistical outlier detection + LLM contextual reasoning |
| **Human Review (HITL)** | LangGraph interrupt/resume | Pauses pipeline, awaits human decision, resumes execution |

---

## Key Technical Decisions

**Why LangGraph over CrewAI?**
LangGraph's `interrupt_before` pattern gives precise control over the human-in-the-loop pause point. CrewAI is excellent for autonomous agent swarms; LangGraph fits better when you need deterministic routing and stateful resumption — exactly what invoice processing requires.

**Why hybrid anomaly detection?**
Statistical z-score checks catch numeric outliers (5x vendor average = flag) cheaply and deterministically. The LLM pass catches *contextual* anomalies — e.g., a vendor description that doesn't match the PO category — that statistics can't see. Neither alone is sufficient.

**Why SQLite for demo vs PostgreSQL for production?**
SQLite requires zero setup and makes this project instantly runnable. The schema is identical to what you'd run on PostgreSQL — swap the `DATABASE_URL` in `.env` and it works. The `docker-compose.yml` in `/infra` wires up the full production stack.

---

## Tech Stack

- **Agent Framework**: LangGraph (stateful graphs, HITL interrupt/resume)
- **LLM**: Groq API — Llama 3.3 70B (free tier, no API cost)
- **Tool Protocol**: MCP (Model Context Protocol) — 6 business tools
- **Backend**: FastAPI + Pydantic v2 + async SQLAlchemy
- **Database**: SQLite (dev) / PostgreSQL (prod via Docker)
- **Caching**: Redis (optional, falls back to in-memory)
- **Observability**: OpenTelemetry — traces every agent decision path
- **ML**: NumPy (statistical anomaly detection), scikit-learn ready
- **Infrastructure**: Docker Compose + Kubernetes manifests in `/infra`

---

## Quick Start

### 1. Clone and install

```bash
git clone <this-repo>
cd invoice-agent-system
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Get your free Groq API key

1. Go to [console.groq.com/keys](https://console.groq.com/keys)
2. Sign up (free, no credit card)
3. Create an API key

### 3. Configure environment

```bash
cp .env.example .env
# Edit .env and set GROQ_API_KEY=your_key_here
```

### 4. Run the CLI demo (no browser needed)

```bash
python demo.py
```

Expected output:
```
Invoice: INV-ACME-20240115 | Amount: $3,200.00
  Vendor: Acme Office Supplies | Amount: $3,200.00 | Confidence: 94%
  Validation: 6 passed · 0 failed · 1 warning
  Anomaly Detection: risk=NONE | 0 anomalies
  → AUTO-APPROVED

Invoice: INV-ACME-20240220 | Amount: $8,500.00
  Validation: 4 passed · 1 failed · 1 warning
  ▲ AMOUNT_OUTLIER: $8,500 exceeds vendor limit of $5,000
  ▲ RULE_VIOLATION: Amount exceeds vendor maximum
  → REQUIRES HUMAN REVIEW
```

### 5. Run the web dashboard

```bash
python main.py
# Open http://localhost:8000
```

---

## API Reference

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/invoices/process` | Submit invoice for agent pipeline |
| `GET` | `/invoices` | List all invoices (filterable by status) |
| `GET` | `/invoices/{id}` | Full invoice detail: extraction, validation, anomalies |
| `POST` | `/invoices/{id}/review` | Submit human review decision (HITL resume) |
| `GET` | `/invoices/{id}/audit` | Complete agent audit trail |
| `GET` | `/metrics` | Pipeline performance metrics |
| `GET` | `/docs` | Interactive API docs (Swagger UI) |

### Example: Process an invoice

```bash
curl -X POST http://localhost:8000/invoices/process \
  -H "Content-Type: application/json" \
  -d '{"invoice_id": 1}'
```

### Example: Submit human review decision

```bash
curl -X POST http://localhost:8000/invoices/1/review \
  -H "Content-Type: application/json" \
  -d '{"decision": "approved", "notes": "Verified with vendor directly"}'
```

---

## MCP Tool Server

The MCP server exposes 6 business tools that agents call via the Model Context Protocol:

```
lookup_vendor          — query vendor approval registry
lookup_purchase_order  — validate PO references
check_duplicate        — detect duplicate invoice submissions
get_vendor_history     — statistical baseline for anomaly detection
get_business_rules     — current approval thresholds and config
flag_for_review        — escalate to human review queue
```

Run standalone:
```bash
python -m tools.mcp_server
```

---

## Anomaly Detection Logic

The anomaly agent uses a two-pass hybrid approach:

**Pass 1 — Statistical (deterministic, fast)**
- Duplicate invoice number detection
- Z-score outlier detection vs vendor invoice history (configurable threshold: 1.5–3.0σ)

**Pass 2 — LLM reasoning (contextual, catches edge cases)**
- Reviews extracted data + validation failures together
- Identifies: unknown vendors, missing PO references, date inconsistencies, suspicious patterns
- Returns: anomaly type, severity (low/medium/high/critical), confidence score

**De-duplication**: LLM pass skips anomaly types already caught statistically to avoid double-reporting.

---

## Evaluation Metrics

Built-in metrics tracked per pipeline run:

| Metric | Description |
|---|---|
| Extraction confidence | LLM self-reported confidence 0.0–1.0 |
| Validation pass rate | Rules passed / total rules |
| Anomaly false positive rate | Tracked via human override decisions |
| Pipeline latency | Per-agent timing in audit log |
| LLM token usage | Cost tracking per invoice |

---

## Production Upgrade Path

| Component | Dev (this repo) | Production |
|---|---|---|
| LLM | Groq free tier | SAP AI Core / Azure OpenAI |
| Database | SQLite | PostgreSQL (see `/infra/docker-compose.yml`) |
| Caching | fakeredis | Redis 7 |
| Orchestration | `python main.py` | Kubernetes (see `/infra/k8s/`) |
| Tracing | Console exporter | Jaeger / Grafana Tempo |
| Workflow triggers | REST API | n8n webhook integration |

---

## Project Structure

```
invoice-agent-system/
├── agents/
│   ├── state.py              # LangGraph TypedDict shared state
│   ├── llm.py                # Groq LLM client (free)
│   ├── ingestion_agent.py    # Agent 1: document intake
│   ├── extraction_agent.py   # Agent 2: LLM entity extraction
│   ├── validation_agent.py   # Agent 3: business rule engine
│   ├── anomaly_agent.py      # Agent 4: hybrid anomaly detection
│   └── hitl_agent.py         # Agent 5: human-in-the-loop
├── workflow/
│   ├── graph.py              # LangGraph StateGraph + routing logic
│   └── runner.py             # Pipeline runner + HITL resume
├── tools/
│   └── mcp_server.py         # MCP tool server (6 business tools)
├── api/
│   ├── app.py                # FastAPI routes + CORS + lifespan
│   └── tracing.py            # OpenTelemetry setup
├── db/
│   ├── models.py             # SQLAlchemy ORM (Invoice, Vendor, PO, Anomaly, AuditLog)
│   ├── connection.py         # Async engine + session factory
│   └── seed.py               # Test data: 7 vendors, 6 POs, 6 invoices
├── static/
│   └── index.html            # Dashboard UI (vanilla JS, no framework)
├── infra/
│   ├── docker-compose.yml    # PostgreSQL + Redis + n8n
│   └── k8s/                  # Kubernetes manifests
├── tests/                    # pytest test suite
├── config.py                 # Pydantic settings (reads .env)
├── main.py                   # uvicorn entrypoint
├── demo.py                   # CLI demo with Rich output
└── requirements.txt
```

---

## Interview Talking Points

1. **HITL design**: "I used LangGraph's `interrupt_before` pattern rather than a polling loop — the graph state is checkpointed so the pipeline resumes from exactly where it paused, preserving all extracted data and audit history."

2. **Hybrid anomaly detection**: "Statistical z-score catches numeric outliers cheaply. The LLM pass runs second and only flags anomaly types not already detected — so there's no double-reporting and the LLM cost is bounded."

3. **MCP integration**: "Agents don't directly import database functions — they call tools via MCP. This means you can swap out the tool implementation (e.g., replace SQLite lookups with SAP S/4HANA RFC calls) without touching agent logic."

4. **Production path**: "The schema runs identically on PostgreSQL. The `DATABASE_URL` env var is the only change. Docker Compose in `/infra` wires up the full production stack including n8n for workflow triggers — which is specifically called out in the SAP RIG JD."
