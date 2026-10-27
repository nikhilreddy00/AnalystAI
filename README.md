# AnalystAI

> **A multi-agent AI system that replaces a data analyst — from raw data to boardroom-ready insights.**

Upload a CSV, or connect a SQL database, Google Sheet, or REST API, then ask questions in plain English. A pipeline of specialized AI agents handles the full analyst workflow — intake, ingestion, cleaning, statistical analysis, visualization, and stakeholder-ready narrative — and streams the results into a chat UI with a pinnable dashboard.

**Self-hosted · Open source · No vendor lock-in.**

<p>
  <img alt="Python" src="https://img.shields.io/badge/python-3.11+-3776AB?logo=python&logoColor=white">
  <img alt="FastAPI" src="https://img.shields.io/badge/FastAPI-async-009688?logo=fastapi&logoColor=white">
  <img alt="Next.js" src="https://img.shields.io/badge/Next.js-16-000000?logo=nextdotjs&logoColor=white">
  <img alt="LangGraph" src="https://img.shields.io/badge/LangGraph-orchestration-1C3C3C">
  <img alt="Claude" src="https://img.shields.io/badge/Claude-Sonnet%204.6-D97757?logo=anthropic&logoColor=white">
  <img alt="License" src="https://img.shields.io/badge/license-MIT-green">
</p>

**Keywords:** `ai-agents` · `multi-agent` · `data-analysis` · `llm` · `langgraph` · `claude` · `anthropic` · `fastapi` · `nextjs` · `data-visualization` · `rag` · `qdrant` · `e2b` · `self-hosted` · `analytics`

---

## How it works

```
Your question
    │
    ▼
[1. Intake Agent]         Claude Sonnet 4.6  — translates plain English into a structured analysis plan
    │
    ▼
[2. Ingestion Agent]      Groq / Llama 3.3-70B — loads data, infers schema, builds a data contract
    │
    ▼
[3. Cleaning Agent]       Claude Sonnet 4.6  — imputes nulls, removes dupes, coerces types, flags outliers
    │
    ▼
[4. Analysis Agent]       Claude Sonnet 4.6  — generates Python, runs it in an E2B sandbox (EDA, trends, segmentation, anomalies)
    │
    ▼
[5. Visualization Agent]  Pure Python (no LLM) — deterministic chart-type selection → Recharts configs
    │
    ▼
[6. Insight Agent]        Claude Sonnet 4.6  — synthesizes an executive summary + technical findings
    │
    ▼
[Quality Check]           No LLM — validates the report; retries analysis once if it's thin
    │
    ▼
[7. Senior Expert Agent]  Claude Sonnet 4.6  — principal-analyst critique of the first-run analysis
    │
    ▼
Streaming chat UI  +  pinnable dashboard
```

**LangGraph** handles stateful routing, conditional gates (human approval on ambiguous questions or poor data quality), a self-correcting quality-check retry loop, and multi-turn conversation re-entry. **CrewAI** defines the agents. **E2B** isolates every line of LLM-generated Python from the host process.

### Multi-turn follow-ups

After the first analysis, keep asking questions in the same session. Each turn is re-classified by intent:

- **Drill-down** — *"why did revenue drop in March?"* — reuses prior findings for a fast, focused answer.
- **Targeted Q&A** — correlation, top-N, distribution, time-series, hypothesis tests, A/B significance, outlier scans — answered with deterministic pandas/scipy, no full pipeline re-run.
- **Meta** — *"how many rows?"*, *"what's the schema?"* — answered instantly from the data contract.
- **Chart / export requests** — a single chart, or a PowerPoint export of the report.

---

## Tech stack

| Layer            | Technology                                                        |
| ---------------- | ----------------------------------------------------------------- |
| Orchestration    | **LangGraph** (stateful routing) + **CrewAI** (agent definition)  |
| Primary LLM      | **Claude Sonnet 4.6** via the `anthropic` SDK                     |
| Speed-tier LLM   | **Groq / Llama 3.3-70B** for schema inference                     |
| Code sandbox     | **E2B** — isolates LLM-generated Python                           |
| Backend          | **FastAPI** (Python 3.11+, `uv`-managed, fully async)             |
| Frontend         | **Next.js 16** + Tailwind v4 + shadcn/ui + Recharts + TanStack Query |
| Session store    | **PostgreSQL** (JSONB via async SQLAlchemy)                       |
| Vector store     | **Qdrant** — institutional memory of past insights               |
| Embeddings       | **nomic-embed-text** via **Ollama** (self-hosted)                |

---

## Prerequisites

- [Docker](https://docs.docker.com/get-docker/) and Docker Compose
- [Anthropic API key](https://console.anthropic.com) — for Claude Sonnet 4.6
- [Groq API key](https://console.groq.com) — for Llama 3.3-70B (free tier available)
- [E2B API key](https://e2b.dev/dashboard) — for the code sandbox (free tier available)

---

## Quick start

```bash
# 1. Clone
git clone https://github.com/nikhilreddy00/AnalystAI.git
cd AnalystAI

# 2. Set your API keys
cp .env.example .env
# Edit .env and fill in ANTHROPIC_API_KEY, E2B_API_KEY

# 3. Pull the embedding model (needed for institutional memory)
docker compose up ollama -d
docker compose exec ollama ollama pull nomic-embed-text

# 4. Start everything
docker compose up

# 5. Open the app
open http://localhost:3000
```

The API runs at `http://localhost:8000` — interactive docs at `http://localhost:8000/docs`.

---

## Data sources

| Source | What to provide |
|--------|----------------|
| **CSV / Excel** | Upload the file directly in the UI (max 50 MB) |
| **SQL database** | Connection string (`postgresql://user:pass@host:5432/db`) + table name |
| **Google Sheets** | Spreadsheet ID + sheet name + service-account JSON |
| **REST API** | Endpoint URL + auth type (Bearer / API key / Basic) + optional pagination key |

---

## Example queries

- *"Give me a complete overview of this dataset"* — full end-to-end briefing
- *"What factors most influenced survival rate?"* — segmentation + correlation
- *"Show me monthly revenue trends and flag any anomalies"* — trend + anomaly detection
- *"Compare conversion rate by marketing channel"* — segmentation breakdown
- *"Is the difference in churn between plan tiers statistically significant?"* — hypothesis testing
- *"Summarize the findings for non-technical stakeholders"* — plain-English drill-down

---

## Architecture

```
AnalystAI/
├── agents/              # Analyst agents — intake, ingestion, cleaning, analysis,
│                        #   visualization, insight, senior-expert, drill-down, targeted
├── connectors/          # CSV, SQL, Sheets, API connectors (unified .load() → DataFrame)
├── orchestrator/        # LangGraph StateGraph + routing + Pydantic state schemas
├── memory/              # PostgreSQL session store + Qdrant vector store
├── sandbox/             # E2B code-execution wrapper
├── api/                 # FastAPI app, routes, config, auth, rate limiting
└── frontend/            # Next.js 16, Recharts, TanStack Query
```

**LLM routing**
- **Claude Sonnet 4.6** — intake, cleaning strategy, code generation, insight narrative, expert critique
- **Groq / Llama 3.3-70B** — schema inference (fast + cheap)
- **nomic-embed-text** (Ollama, self-hosted) — institutional-memory embeddings
- **Visualization is pure Python** — deterministic chart selection, no LLM call

**Persistence**
- PostgreSQL — the full `AnalysisSession` as JSONB, plus pinned dashboard charts
- Qdrant — semantic embeddings of past `InsightReport`s for cross-session memory

---

## Configuration

All configuration is via environment variables (see `.env.example`):

| Variable | Description | Default |
|----------|-------------|---------|
| `ANTHROPIC_API_KEY` | Claude API key | required |
| `E2B_API_KEY` | E2B sandbox key | required |
| `DATABASE_URL` | PostgreSQL connection string | in-memory fallback |
| `QDRANT_HOST` / `QDRANT_PORT` | Vector store location | optional |
| `OLLAMA_HOST` | Embedding server | optional |
| `FRONTEND_URL` | Allowed CORS origin | `http://localhost:3000` |
| `APP_ENV` | `development` / `production` | `development` |

> Without PostgreSQL/Qdrant configured, AnalystAI falls back to an in-memory session store and skips institutional memory — handy for a quick local trial.

---

## Development setup

```bash
# Python backend
uv sync
source .venv/bin/activate
uvicorn api.main:app --reload

# Frontend
cd frontend
npm install
npm run dev
```

Run infrastructure only (without rebuilding the API container):

```bash
docker compose up postgres qdrant ollama -d
```

---

## Security

- **E2B sandbox** — LLM-generated Python runs in an isolated VM, never in the FastAPI process
- **SQL connector** — only `SELECT` statements are permitted; anything else is rejected before execution
- **File uploads** — limited to 50 MB; only `.csv`, `.xlsx`, `.xls` accepted
- **Rate limiting** — 30 requests/minute per IP on the chat endpoint (via slowapi)
- **No secrets in code** — all credentials are loaded from environment variables; `.env` is gitignored

---

## License

MIT — see [LICENSE](LICENSE).
