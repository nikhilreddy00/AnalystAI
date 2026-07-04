# AnalystAI — Design Spec & Implementation Plan

## Context

Small startups and cost-conscious companies spend $80K–$120K/year on a data analyst whose core workflow is repeatable: ingest data, clean it, analyze it, visualize it, and communicate findings. This project replaces that workflow entirely with a multi-agent AI system. The target user is a non-technical founder OR a technical-but-not-analyst developer at an early-stage startup. The system ships as open source + self-hosted (Docker Compose), making it accessible to any startup regardless of budget.

The system must think like a senior data analyst — not just run queries, but reason about what analysis is appropriate, flag anomalies proactively, explain its decisions, and support iterative follow-up questions across sessions.

---

## Decisions Made

| Decision | Choice | Reason |
|----------|--------|--------|
| Orchestration | Hybrid: CrewAI agents + LangGraph routing | CrewAI for fast agent definition; LangGraph for stateful conditional routing and multi-turn |
| Primary LLM | Claude Sonnet 4.6 | Best structured reasoning for analytical tasks; long context |
| Speed-tier LLM | Groq / Llama 3.3-70B | Schema inference, type detection — fast structured tasks |
| Backend | FastAPI + Python | Async-native (SSE streaming), Pydantic contracts between agents |
| Code execution | E2B sandbox | Isolates LLM-generated pandas/scipy code from main process |
| Frontend | Next.js 15 + shadcn/ui + Recharts | Production-quality product, not a demo; React-native charts |
| Vector memory | Qdrant (self-hosted) | Semantic search across historical analyses (institutional memory) |
| Session storage | PostgreSQL + SQLAlchemy | Structured session state, multi-turn conversation history |
| Data sources v1 | CSV/Excel, SQL (PG/MySQL), Google Sheets, REST APIs | All confirmed at launch |
| Output | Chat (ad-hoc Q&A) + Persistent Dashboard (monitoring) | Full analyst replacement model |
| Deployment | Open source, Docker Compose | Self-hosted; no cloud lock-in |

---

## Architecture Overview

```
User (browser)
    │
    ├── Chat Interface (Next.js)        ← ad-hoc Q&A, streaming agent progress
    └── Dashboard (Next.js)             ← pinned charts, auto-refresh on cron

FastAPI Backend
    │
    ├── SSE /chat endpoint              ← streams agent execution events
    ├── /dashboard CRUD                 ← save/load pinned charts
    └── /connectors                     ← data source management

LangGraph Orchestrator (graph.py)
    │ owns session state, conditional routing, multi-turn re-entry
    │
    ├── [Intake Agent]       CrewAI     ← NL → AnalysisTask (Claude Sonnet)
    ├── [Ingestion Agent]    CrewAI     ← source → DataFrame (Groq)
    ├── [Cleaning Agent]     CrewAI     ← dirty → clean + audit (Claude Sonnet)
    ├── [Analysis Agent]     CrewAI     ← clean → findings via E2B (Claude Sonnet)
    ├── [Visualization Agent] CrewAI    ← findings → Recharts JSON (Groq)
    └── [Insight Agent]      CrewAI     ← all → narrative + actions (Claude Sonnet)

Persistence
    ├── PostgreSQL           ← AnalysisSession, conversation history, dashboards
    └── Qdrant               ← semantic embeddings of past insights (institutional memory)
```

---

## The Six Agents

### Agent 1 — Stakeholder Intake Agent
**Responsibility:** Translate natural language into a structured `AnalysisTask`. Handle ambiguity by asking one clarifying question. On follow-up messages, amend the existing task (not create a new one).

**LLM:** Claude Sonnet 4.6 — best disambiguation and instruction-following.

**Input:** raw user message + conversation history + existing AnalysisTask (if multi-turn)
**Output:** `AnalysisTask` — metrics, timeframe, comparisons, anomaly flags, output format preference

**Tools:** conversation history reader, task schema validator, clarification question generator

**Key prompt behaviors:**
- If user says "how are we doing?" → ask: "What would you like to focus on — revenue, retention, or growth?"
- If user says "drill into Q3" → amend existing task scope, don't restart
- Always decompose compound requests ("show me revenue AND churn") into ordered subtasks

---

### Agent 2 — Data Ingestion Agent
**Responsibility:** Connect to the configured data source and produce a `DataContract`.

**LLM:** Groq / Llama 3.3-70B — fast structured inference for schema detection.

**Input:** connector config (type, credentials, target table/sheet/endpoint) + AnalysisTask
**Output:** `DataContract` — schema, row count, column types, sample rows, detected dates, detected IDs, detected metrics

**Connectors (each is a separate class with unified `.load() → DataFrame`):**
- `CSVConnector` — pandas read_csv/read_excel, encoding detection
- `SQLConnector` — SQLAlchemy, parameterized queries only (no raw SQL injection risk)
- `SheetsConnector` — gspread + OAuth2 service account
- `APIConnector` — httpx, configurable auth (Bearer, API key, Basic), pagination handling

**Why Vanna.ai for SQL:** For the SQL connector's NL→SQL capability, integrate `vanna-ai/vanna` rather than building NL→SQL from scratch. Vanna handles model fine-tuning on the user's schema automatically.

---

### Agent 3 — Data Cleaning Agent
**Responsibility:** Clean the DataFrame and produce a `CleaningAudit` explaining every decision.

**LLM:** Claude Sonnet 4.6 — reasoning about which imputation strategy fits the column's distribution.

**Input:** DataFrame + DataContract + AnalysisTask
**Output:** cleaned DataFrame + `CleaningAudit` (list of decisions with justifications)

**Operations (in order):**
1. Type coercion — cast columns to inferred types from DataContract
2. Date standardization — normalize all date formats to ISO 8601
3. Whitespace normalization — strip, lowercase categoricals
4. Duplicate removal — exact row deduplication, log count removed
5. Missing value imputation — per-column strategy: mean (normal dist), median (skewed), mode (categorical), forward-fill (time series)
6. Outlier detection — IQR method (flag), Z-score >3 (flag), Isolation Forest (severe outliers)

**Human approval gate (LangGraph):** If >40% of any column is null, LangGraph routes to a user confirmation step before proceeding. The Cleaning Agent explains what it found and proposes options.

**Explainability output example:**
> "Column `revenue_usd` had 12 nulls (3.2%). Distribution is right-skewed (skewness=2.1), so imputed with median ($4,230) rather than mean to avoid distortion."

---

### Agent 4 — Analysis Agent
**Responsibility:** Run statistical analysis in an E2B sandbox and produce structured `AnalysisFindings`.

**LLM:** Claude Sonnet 4.6 — generates Python code, reasons about which tests are appropriate.

**Input:** cleaned DataFrame + CleaningAudit + AnalysisTask
**Output:** `AnalysisFindings` — EDA summary, trends, correlations, anomalies, segments, statistical test results

**Analysis types performed (selected based on AnalysisTask):**
- **EDA:** describe(), value_counts(), correlation matrix, distribution shapes
- **Trend analysis:** rolling averages, MoM/YoY comparisons, linear regression on time series
- **Segmentation:** group-by breakdowns on categorical columns with metric aggregation
- **Anomaly detection:** statistical threshold alerting + Isolation Forest for multivariate
- **Hypothesis testing:** t-test, chi-square, Mann-Whitney U — chosen based on data types
- **Forecasting (v2):** Prophet for time series; v1 uses `numpy.polyfit(degree=1)` on the time series index for trend extrapolation

**Code execution flow:**
1. Claude generates pandas/scipy/statsmodels code as a string
2. E2B spins up isolated Python environment (~300ms)
3. Code runs on the actual DataFrame (serialized via parquet)
4. E2B returns stdout, stderr, and any matplotlib figures as base64
5. Findings are parsed from structured output and stored in AnalysisFindings

**Why E2B over subprocess:** LLM-generated code + user data = two untrusted inputs. E2B fully isolates execution. A prompt injection or malformed dataset cannot touch the FastAPI process.

---

### Agent 5 — Visualization Agent
**Responsibility:** Select the right chart type and generate Recharts-compatible JSON config.

**LLM:** Groq / Llama 3.3-70B — fast structured generation of chart configs.

**Input:** AnalysisFindings + AnalysisTask + DataContract
**Output:** `list[ChartConfig]` — Recharts JSON configs, each with type, data, axes, colors, title

**Chart selection rules (enforced by LLM reasoning, not hardcoded):**
- Time series metric → LineChart
- Categorical comparison (≤12 categories) → BarChart
- Categorical comparison (>12 categories) → HorizontalBarChart (top 10)
- Distribution → HistogramChart (via ComposedChart)
- Two numeric variables → ScatterChart with regression line
- Part-of-whole (≤6 segments) → PieChart / DonutChart
- Correlation matrix → HeatmapChart (custom Recharts)
- Multiple metrics over time → ComposedChart (bars + line)

**Why Recharts over Plotly:** Recharts generates React component configs — they live in the same React tree as the dashboard. You get reactive filtering, drill-down click handlers, and TanStack Query-powered refresh for free. Plotly generates self-contained HTML blobs that are hard to wire into React state.

---

### Agent 6 — Insight Delivery Agent
**Responsibility:** Synthesize all upstream outputs into a stakeholder-ready report with two views.

**LLM:** Claude Sonnet 4.6 — narrative synthesis is Claude's strongest capability.

**Input:** AnalysisTask + CleaningAudit + AnalysisFindings + ChartConfigs + conversation history
**Output:** `InsightReport` — executive summary, technical findings, recommended actions, anomaly alerts, proactive follow-up questions

**Output structure:**
```
InsightReport
├── executive_summary        # 3-5 sentences for CEO (Executive View)
├── technical_findings       # bulleted statistical results for developer (Analyst View)
├── recommended_actions      # prioritized list with reasoning
├── anomaly_alerts           # severity-scored flags with explanation
├── proactive_questions      # 3 follow-up questions the system suggests
└── methodology_notes        # what was done and why (Analyst View only)
```

**UI toggle:** "Executive View" (summary + actions) vs "Analyst View" (full findings + methodology). Same InsightReport object, different presentation.

---

## LangGraph Routing Layer

### State Graph

```python
# orchestrator/graph.py
nodes = [
    "intake",
    "ingestion",
    "cleaning",
    "analysis",
    "visualization",
    "insight",
    "human_approval",   # gates: null threshold, data quality warnings
    "error",            # terminal error state with user-readable message
]

conditional_edges = {
    "intake": {
        "ambiguous": "human_approval",   # ask clarifying question
        "clear": "ingestion",
    },
    "ingestion": {
        "success": "cleaning",
        "connection_error": "error",
    },
    "cleaning": {
        "high_null_rate": "human_approval",  # >40% nulls → warn user
        "ok": "analysis",
    },
    "analysis": {
        "insufficient_data": "error",
        "success": "visualization",
    },
    "visualization": "insight",
    "insight": "END",
    "human_approval": "intake",   # re-enter with user response
}
```

### Session State Schema

```python
# orchestrator/state.py
class AnalysisSession(BaseModel):
    session_id: str
    user_id: str
    conversation_history: list[Message]     # full multi-turn dialogue
    data_contract: DataContract | None
    cleaning_audit: CleaningAudit | None
    analysis_findings: AnalysisFindings | None
    chart_configs: list[ChartConfig]
    insight_report: InsightReport | None
    task_plan: AnalysisTask | None
    iteration: int                           # which turn (1, 2, 3...)
    connector_config: ConnectorConfig
    created_at: datetime
    updated_at: datetime
```

### Multi-Turn Re-Entry

When the user sends a follow-up ("why did that spike happen in week 3?"):
1. Load existing `AnalysisSession` from PostgreSQL
2. Append new user message to `conversation_history`
3. Re-enter graph at `intake` node with full session state
4. Intake Agent produces amended `AnalysisTask` with `resume_from="analysis"` field set
5. LangGraph reads `resume_from` and skips Ingestion + Cleaning → routes directly to Analysis
6. New findings merged into existing `InsightReport` with diff tracking

**Human Approval Gate response mechanism:** When LangGraph routes to `human_approval`, the SSE stream emits an `approval_required` event with the question payload. The frontend renders a modal. The user's response is POSTed to `POST /chat` with `{ session_id, user_response, is_approval: true }`. FastAPI resumes the LangGraph graph execution with the response injected into session state, then re-enters at `intake`.

---

## Memory System

### Session Memory (PostgreSQL)
- Stores full `AnalysisSession` serialized as JSONB
- Retrieved by `session_id` on every multi-turn message
- Enables: "compare to last month's analysis"

### Institutional Memory (Qdrant)
- On every completed `InsightReport`, embed the summary + findings → store in Qdrant
- On each new analysis, retrieve top-3 semantically similar past reports
- Injected into Insight Agent's context: "8 months ago there was a similar revenue dip tied to Q4 seasonality"
- Enables: "have we seen this pattern before?"

### Embedding model: `nomic-embed-text` via Ollama (self-hosted, no API key required — fits the self-hosted ethos of the project)

---

## Frontend

### Two Views

**Chat Interface** (`/app/chat/`)
- `useChat` hook from Vercel AI SDK for streaming
- `AgentProgressStream` component shows current agent: "Cleaning data... Analyzing trends..."
- Charts rendered inline in chat bubbles via `ChartRenderer`
- "Pin to Dashboard" button on each chart

**Dashboard** (`/app/dashboard/`)
- Grid of pinned `ChartConfig`s rendered by `DashboardCanvas`
- TanStack Query polls live sources on configurable interval
- Drag-to-reorder, resize panels (react-grid-layout)
- Export to PDF (react-pdf)

### Persona Toggle
Rendered in `InsightPanel` component:
- **Executive View:** `insight_report.executive_summary` + `insight_report.recommended_actions`
- **Analyst View:** all fields including `technical_findings` + `methodology_notes`

---

## Repository Structure

```
analystai/
├── agents/
│   ├── intake_agent.py
│   ├── ingestion_agent.py
│   ├── cleaning_agent.py
│   ├── analysis_agent.py
│   ├── visualization_agent.py
│   └── insight_agent.py
├── connectors/
│   ├── base.py                  # DataConnector ABC with .load() → DataFrame
│   ├── csv_connector.py
│   ├── sql_connector.py         # SQLAlchemy + Vanna.ai NL→SQL
│   ├── sheets_connector.py      # gspread + OAuth2
│   └── api_connector.py         # httpx, pagination, auth strategies
├── orchestrator/
│   ├── graph.py                 # LangGraph StateGraph definition
│   ├── state.py                 # AnalysisSession + all sub-schemas
│   └── routing.py               # conditional edge functions
├── memory/
│   ├── session_store.py         # PostgreSQL CRUD for AnalysisSession
│   └── vector_store.py          # Qdrant upsert + similarity search
├── sandbox/
│   └── executor.py              # E2B sandbox wrapper: run_code(code, df) → findings
├── api/
│   ├── main.py                  # FastAPI app, CORS, lifespan
│   ├── routes/
│   │   ├── chat.py              # POST /chat → SSE stream
│   │   ├── dashboard.py         # GET/POST/DELETE /dashboard/charts
│   │   └── connectors.py        # POST /connectors/test, /connectors/save
│   └── models.py                # Pydantic request/response models
├── frontend/
│   ├── app/
│   │   ├── chat/page.tsx
│   │   └── dashboard/page.tsx
│   ├── components/
│   │   ├── ChatInterface.tsx
│   │   ├── AgentProgressStream.tsx
│   │   ├── ChartRenderer.tsx       # Recharts wrapper, chart-type router
│   │   ├── DashboardCanvas.tsx     # react-grid-layout grid
│   │   ├── InsightPanel.tsx        # Executive/Analyst toggle
│   │   └── ConnectorSetup.tsx      # data source config UI
│   └── lib/
│       └── api.ts                  # typed fetch wrappers
├── docker-compose.yml           # FastAPI + PostgreSQL + Qdrant
├── pyproject.toml               # uv-managed dependencies
└── README.md
```

---

## Open Source Repos to Integrate / Study

| Repo | Role in this project |
|------|----------------------|
| `vanna-ai/vanna` | NL→SQL inside SQLConnector — don't rebuild |
| `langchain-ai/langgraph` | Core orchestration dependency |
| `crewAIInc/crewAI` | Agent definition and task execution |
| `e2b-dev/e2b` | Code sandbox for Analysis Agent |
| `qdrant/qdrant` | Self-hosted vector DB for institutional memory |
| `openbb-finance/openbb` | Study connector abstraction patterns |
| `pydantic/pydantic` | All inter-agent data contracts |
| `facebook/prophet` | Time series forecasting in Analysis Agent (v2) |
| `scikit-learn` | Isolation Forest anomaly detection |

---

## Implementation Phases

### Phase 1 — Foundation (Week 1-2)
**Goal:** Docker Compose running with database, Qdrant, and a working FastAPI skeleton.

- [ ] Initialize repo with `uv`, `pyproject.toml`, `.python-version`
- [ ] Write `docker-compose.yml` — FastAPI, PostgreSQL, Qdrant
- [ ] Define all Pydantic schemas in `orchestrator/state.py`
- [ ] Implement `session_store.py` — CRUD for AnalysisSession in PostgreSQL
- [ ] Implement `vector_store.py` — Qdrant collection setup + upsert + search
- [ ] FastAPI skeleton: `/health`, basic CORS, lifespan startup (DB + Qdrant init)
- [ ] Next.js 15 project init with shadcn/ui and Tailwind

### Phase 2 — Data Layer (Week 2-3)
**Goal:** All four connectors working and producing DataContracts.

- [ ] `connectors/base.py` — DataConnector ABC
- [ ] `CSVConnector` — pandas, encoding detection, Excel support
- [ ] `SQLConnector` — SQLAlchemy + Vanna.ai integration, parameterized queries
- [ ] `SheetsConnector` — gspread, OAuth2 service account flow
- [ ] `APIConnector` — httpx, Bearer/API key/Basic auth, pagination
- [ ] `ingestion_agent.py` — CrewAI agent wrapping connectors, Groq schema inference
- [ ] `/connectors` API routes — test connection, save config
- [ ] `ConnectorSetup.tsx` — frontend connector configuration UI

### Phase 3 — Agent Pipeline (Week 3-5)
**Goal:** All six agents working end-to-end with CSV input.

- [ ] `intake_agent.py` — Claude Sonnet, AnalysisTask output, clarification logic
- [ ] `cleaning_agent.py` — all cleaning operations, CleaningAudit, >40% null gate
- [ ] `sandbox/executor.py` — E2B wrapper, DataFrame serialization via parquet
- [ ] `analysis_agent.py` — EDA, trends, segmentation, anomaly detection via E2B
- [ ] `visualization_agent.py` — chart type selection, Recharts JSON generation
- [ ] `insight_agent.py` — InsightReport generation, Executive/Analyst split

### Phase 4 — Orchestration (Week 5-6)
**Goal:** LangGraph routing with conditional edges and multi-turn working.

- [ ] `orchestrator/graph.py` — full StateGraph with all 6 nodes
- [ ] `orchestrator/routing.py` — all conditional edge functions
- [ ] Human approval gate — SSE event triggers frontend modal, waits for response
- [ ] Multi-turn re-entry — load session, amend task, skip unchanged agents
- [ ] Session persistence — save/load AnalysisSession on every turn
- [ ] Qdrant embedding — embed InsightReport on completion, similarity search on new queries

### Phase 5 — Frontend (Week 6-7)
**Goal:** Chat interface with streaming and dashboard with pinning.

- [ ] `ChatInterface.tsx` — `useChat` hook, message thread, input
- [ ] `AgentProgressStream.tsx` — SSE event consumer, agent status display
- [ ] `ChartRenderer.tsx` — Recharts chart-type router from ChartConfig JSON
- [ ] `InsightPanel.tsx` — Executive/Analyst toggle
- [ ] "Pin to Dashboard" button — POST to /dashboard/charts
- [ ] `DashboardCanvas.tsx` — react-grid-layout, chart grid
- [ ] Dashboard auto-refresh — TanStack Query with configurable interval
- [ ] `/chat` and `/dashboard` page routing

### Phase 6 — Hardening + Open Source Release (Week 7-8)
**Goal:** Production-ready self-hosted deployment.

- [ ] Docker Compose with environment variable config (no secrets in code)
- [ ] Rate limiting on FastAPI (slowapi)
- [ ] Input validation — file size limits, SQL injection prevention, API key encryption
- [ ] Error handling — every agent failure produces user-readable message, not stack trace
- [ ] README — setup guide, connector configuration, architecture diagram
- [ ] `docs/superpowers/specs/2026-05-13-analyst-ai-design.md` — full spec committed

---

## Verification Plan

**Agent pipeline (end-to-end test):**
1. Upload the Titanic dataset (CSV) via the UI
2. Ask: "What factors most influenced survival rate?"
3. Verify: Intake Agent produces AnalysisTask with `target_metric=survival`, `analysis_type=segmentation+correlation`
4. Verify: Cleaning Agent audit shows null imputation for `age` and `embarked`
5. Verify: Analysis Agent produces correlation matrix + group-by survival by class/sex
6. Verify: Visualization Agent selects BarChart (categorical) + HeatmapChart (correlation)
7. Verify: InsightReport has executive summary + 3 recommended actions

**Multi-turn test:**
1. After Titanic analysis, ask: "Now focus only on first-class passengers"
2. Verify: session reloaded, Intake Agent amends task (no new ingestion/cleaning)
3. Verify: Analysis Agent re-runs with filtered DataFrame

**Connector test:**
1. Connect to a PostgreSQL test database
2. Ask: "Show me monthly revenue by product category for 2025"
3. Verify: SQLConnector generates correct parameterized SQL via Vanna.ai
4. Verify: full pipeline runs on query result

**Dashboard test:**
1. Pin a chart from a completed analysis
2. Disconnect and reconnect the data source
3. Verify: dashboard auto-refreshes and chart updates

**Institutional memory test:**
1. Run two analyses on datasets with similar revenue dip patterns
2. On the third analysis, verify Qdrant returns the prior two as context
3. Verify InsightReport references past patterns

---

## Key External Dependencies

```toml
# pyproject.toml (key deps)
crewai = ">=0.80"
langgraph = ">=0.2"
anthropic = ">=0.40"          # Claude Sonnet 4.6
groq = ">=0.9"
e2b-code-interpreter = ">=1.0"
qdrant-client = ">=1.9"
sqlalchemy = ">=2.0"
pandas = ">=2.2"
scipy = ">=1.13"
scikit-learn = ">=1.5"
gspread = ">=6.0"
httpx = ">=0.27"
fastapi = ">=0.115"
vanna = ">=0.7"
```

```json
// package.json (key deps)
"next": "15",
"ai": "^4.0",              // Vercel AI SDK for useChat streaming
"@tanstack/react-query": "^5",
"recharts": "^2.12",
"react-grid-layout": "^1.4",
"@radix-ui/react-*": "latest"  // via shadcn/ui
```
