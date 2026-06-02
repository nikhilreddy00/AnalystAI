# Query Intent Routing — Design (Spec 1 of Gap 1)

**Status:** Approved for plan-writing
**Date:** 2026-06-02
**Scope:** Gap 1 (Query Intent Routing), Spec 1 of 2. This spec covers the new lightweight intents, the `DEEP_DIVE` → `DRILL_DOWN` behavior change, and the "rerun previous overview?" approval gate. `FILTER_REQUEST` is deferred to Spec 2.

---

## 1. Goal & Non-Goals

### Goal
Recognize four new question intents (`CHART_REQUEST`, `EXPORT_REQUEST`, `META_QUESTION`, `DRILL_DOWN`) so the chat pipeline can route to the minimal handler instead of always running the full 7-agent flow. Refine an existing intent (`DEEP_DIVE` → `DRILL_DOWN`) so follow-up questions on a completed analysis return a compact text+chart answer rather than rebuilding the dashboard. Prompt the user before re-running a full `OVERVIEW` on a session that already has one.

### Non-goals (deferred or out of scope)
- `FILTER_REQUEST` intent + persistent filter state on the session — handled in a separate spec (Spec 2 of Gap 1).
- Pre-intake classifier as a second LLM call. The existing `intake_agent` LLM call remains the single classifier.
- PDF / CSV export. Only PPTX is wired today; other formats surface a polite text response.
- Voice / audio responses, conversation summarization, etc.
- Any change to the 7 existing fine-grained targeted intents (`CORRELATION`, `DISTRIBUTION`, `TOP_N`, `TIME_SERIES`, `SEGMENT_COMPARE`, `HYPOTHESIS_TEST`, `AB_SIGNIFICANCE`, `OUTLIER_SCAN`).

---

## 2. Architecture Overview

The intake agent stays the single classifier. We do **not** add a second LLM call before intake — that would duplicate work intake already does, double per-turn latency, and double token spend.

Two structural changes to the graph:

1. The existing `targeted_analysis_node` gets three new deterministic branches — one each for `CHART_REQUEST`, `EXPORT_REQUEST`, `META_QUESTION`. None of them require an LLM call beyond intake; META reads only from `session.data_contract`; EXPORT just emits an SSE event; CHART builds one `ChartConfig` deterministically from `TargetedParams`.
2. A new `drill_down_node` (and a new `agents/drill_down_agent.py`) is added for `DRILL_DOWN`. This is the only new LLM call introduced by the spec — Claude is given the prior `insight_report` and the cached DataFrame summary and asked to answer a follow-up in 2-3 sentences with an optional chart spec.

Routing constant `_FULL_PIPELINE_INTENTS` shrinks from `{OVERVIEW, DEEP_DIVE}` to `{OVERVIEW}`. Only `OVERVIEW` triggers the full ingestion → cleaning → analysis → viz → insight → quality → senior_expert pipeline.

A new approval-gate context, `rerun_overview`, is emitted by `intake_node` when the user requests `OVERVIEW` on a session that already has a completed analysis. The frontend reuses `ApprovalModal.tsx` with three buttons (Load previous / Run fresh / Cancel).

### Graph topology after this spec

```
                    ┌─→ targeted_analysis ─→ END   (META, EXPORT, CHART, + 7 existing targeted)
ingestion → intake ─┤
                    ├─→ drill_down ─→ END         (DRILL_DOWN; NEW)
                    ├─→ cleaning → … → END         (OVERVIEW only)
                    └─→ human_approval → END       (clarification, high_null, rerun_overview)
```

Topology of the cleaning → … → END branch is unchanged.

---

## 3. Schema Changes

### 3.1 `QuestionIntent` (orchestrator/state.py)

```python
class QuestionIntent(str, Enum):
    OVERVIEW = "overview"
    DRILL_DOWN = "drill_down"           # renamed from DEEP_DIVE
    CORRELATION = "correlation"
    DISTRIBUTION = "distribution"
    TOP_N = "top_n"
    TIME_SERIES = "time_series"
    SEGMENT_COMPARE = "segment_compare"
    HYPOTHESIS_TEST = "hypothesis_test"
    AB_SIGNIFICANCE = "ab_significance"
    OUTLIER_SCAN = "outlier_scan"
    CHART_REQUEST = "chart_request"      # NEW
    EXPORT_REQUEST = "export_request"    # NEW
    META_QUESTION = "meta_question"      # NEW
```

### 3.2 `TargetedParams`

One new optional field:

```python
chart_type: ChartType | None = None       # populated for CHART_REQUEST
```

`ChartType` already exists in `state.py`. No other fields change — `target_columns` / `metric_column` / `group_by` are reused for CHART_REQUEST's column extraction.

### 3.3 `AnalysisTask` — backward-compat coercion

Persisted sessions whose `task_plan.question_intent == "deep_dive"` would otherwise fail to deserialize after the rename. Add a Pydantic v2 `model_validator(mode="before")` on `AnalysisTask`:

```python
@model_validator(mode="before")
@classmethod
def _coerce_deprecated_intents(cls, data):
    if isinstance(data, dict) and data.get("question_intent") == "deep_dive":
        data["question_intent"] = "drill_down"
    return data
```

This silently upgrades any old `"deep_dive"` value in the wild — including JSON blobs replayed from `SessionStore.load()` — without leaking the legacy name into the enum.

### 3.4 `TargetedResponse`

No field changes. Existing `intent`, `answer_text`, `chart_configs`, `table_data`, `table_columns` cover all four new/refined intents.

### 3.5 SSE protocol

One new event type, `export`:

```jsonc
{"type": "export", "format": "pptx", "url": "/dashboard/latest/report.pptx"}
```

Emitted by `targeted_analysis_node` when the `EXPORT_REQUEST` branch runs and an `insight_report` exists. Frontend handler invokes the existing `downloadLatestReportPptx()` from `lib/api.ts`.

One new value for the existing `approval_required` event's `context` field: `"rerun_overview"`. Existing values (`clarification`, `high_null`) are unchanged.

The `agent_start` / `agent_complete` events for the new `drill_down` node mirror the existing pattern — `{type: "agent_start", agent: "drill_down", message: "Investigating..."}`.

### 3.6 `ChatRequest` (api/routes/chat.py)

```python
approval_context: str | None = Field(default=None, max_length=64)
```

Frontend sets this to `"rerun_overview"` when the user clicks Run fresh on the rerun modal. It stays `None` for clarification and high_null approvals (no breakage to existing flows).

---

## 4. Intake Prompt + Routing

### 4.1 Intake system prompt updates

In `agents/intake_agent.py`, the system prompt is extended with examples for the four new/renamed intents. The existing examples for `OVERVIEW`, `CORRELATION`, `TOP_N`, etc. are unchanged. The `DEEP_DIVE` example is removed.

```
- DRILL_DOWN: follow-up that builds on a prior finding. Examples:
  "tell me more about why revenue dropped"
  "what's driving the spike in churn"
  "break down the anomaly you found in March"

- CHART_REQUEST: user wants a specific chart, not a full analysis. Examples:
  "show me a bar chart of revenue by region"
  "plot price vs square footage"
  "scatter plot of age and income"
  Extract chart_type into TargetedParams.chart_type (BarChart, LineChart,
  ScatterChart, PieChart, etc.). Also fill target_columns / metric_column / group_by.

- EXPORT_REQUEST: user wants a download. Examples:
  "download as pptx", "export the report", "save this analysis",
  "give me a powerpoint"
  No additional parameter extraction needed.

- META_QUESTION: user asks ABOUT the data structure, not about its content. Examples:
  "how many columns", "what are the column names", "what's the schema",
  "what data types", "how many rows", "show me a preview"
  Leave TargetedParams empty unless the user named specific columns.
```

No JSON-schema change — the LLM still returns an `AnalysisTask` whose `question_intent` and `targeted_params` populate the existing fields.

### 4.2 Routing constants & functions (`orchestrator/routing.py`)

```python
_FULL_PIPELINE_INTENTS = {QuestionIntent.OVERVIEW}   # was {OVERVIEW, DEEP_DIVE}
```

`route_after_intake` gains a DRILL_DOWN branch:

```python
def route_after_intake(state) -> str:
    session = state["session"]
    task = session.task_plan
    if not task:
        return "error"
    if session.error_message:
        return "error"
    if task.clarification_needed:
        return "human_approval"
    if task.question_intent == QuestionIntent.DRILL_DOWN:
        return "drill_down"
    if _is_targeted(task):
        return "targeted_analysis"
    return "cleaning"
```

`route_after_ingestion` also gains the DRILL_DOWN branch for the multi-turn resume case. Today it returns `"targeted_analysis"` for any non-full-pipeline intent on resume; we add the DRILL_DOWN check there too.

### 4.3 Graph assembly (`orchestrator/graph.py`)

```python
graph.add_node("drill_down", drill_down_node)
graph.add_conditional_edges("intake", route_after_intake, {
    "cleaning": "cleaning",
    "targeted_analysis": "targeted_analysis",
    "drill_down": "drill_down",        # NEW
    "human_approval": "human_approval",
    "error": "error",
})
graph.add_edge("drill_down", END)
```

`route_after_ingestion`'s mapping in `build_graph` also gets a `"drill_down": "drill_down"` entry.

---

## 5. New Intent Handlers

### 5.1 META_QUESTION (in `targeted_analysis_node`)

Keyword-matched aspect detection on the lowercased query — no LLM call:

| Trigger keywords | Output |
|---|---|
| `"how many"` + (`"column"` or `"row"`) / `"shape"` / `"size"` | `answer_text` like `"This dataset has N rows × M columns."` from `data_contract` |
| `"column name"` / `"what columns"` / `"schema"` | `table_columns=["name","dtype","nullable"]`; `table_data` built from `data_contract.columns`; concise `answer_text` |
| `"data type"` / `"dtype"` | Same table as schema, `answer_text` framed around types |
| `"preview"` / `"first rows"` / `"sample"` / `"head"` | `analysis_findings.head_preview` if present; else `answer_text="Run an analysis first to see a preview."` |

Returns under 50ms. Adds the assistant message to `conversation_history` so the chat thread stays consistent.

### 5.2 EXPORT_REQUEST (in `targeted_analysis_node`)

Format detection by keyword match:

- `"pptx" / "powerpoint" / "ppt" / "slide"` → `fmt = "pptx"`
- Anything else → `fmt = None`

Behavior:

| Condition | Emit | answer_text |
|---|---|---|
| `fmt == "pptx"` and `session.insight_report` is set | `{"type":"export","format":"pptx","url":"/dashboard/latest/report.pptx"}` | `"Generating the PPTX report. The download will start in a moment."` |
| `fmt == "pptx"` and no `insight_report` | — | `"Nothing to export — run an analysis first."` |
| `fmt is None` | — | `"Only PowerPoint (PPTX) export is supported right now."` |

### 5.3 CHART_REQUEST (in `targeted_analysis_node`)

Reads the cached parquet that `targeted_analysis_node` already loads, then dispatches:

```python
def _handle_chart_request(session, df) -> TargetedResponse:
    p = session.task_plan.targeted_params
    if not p.chart_type or not (p.target_columns or p.metric_column):
        return TargetedResponse(
            intent=QuestionIntent.CHART_REQUEST,
            answer_text='I couldn\'t tell which columns to chart. Try: "bar chart of revenue by region".',
        )
    chart = build_single_chart(df, p)
    return TargetedResponse(
        intent=QuestionIntent.CHART_REQUEST,
        answer_text=f"Here's the {chart.chart_type} you asked for.",
        chart_configs=[chart],
    )
```

`build_single_chart` is a new helper in `agents/visualization_agent.py` that reuses existing primitives (`classify_columns` for role inference, the existing chart-data assembly path) to produce one `ChartConfig` from `TargetedParams`. No new LLM call. The DataFrame is the cached parquet loaded by `ingestion_node` on resume.

### 5.4 DRILL_DOWN (new node + new agent — the only new LLM call)

**`agents/drill_down_agent.py`** — async Claude call. Inputs:
- `session.insight_report` (executive_summary, technical_findings, anomaly_alerts) — the prior context.
- `session.task_plan.raw_query` — the follow-up question.
- A compact data summary built from `session.data_contract` + `session.analysis_findings.eda_summary`.

Output: structured JSON parsed into a `TargetedResponse` with:
- `intent = QuestionIntent.DRILL_DOWN`
- `answer_text`: 2-3 sentences directly addressing the question.
- `chart_configs`: optional single chart, built deterministically from the chart spec the LLM emits (column names + chart_type) using `build_single_chart`.

Same response-parsing pattern as `agents/intake_agent.py`: strip markdown fences, `model_validate_json`, error-handling on parse failure.

**`drill_down_node` in `orchestrator/graph.py`:**

```python
async def drill_down_node(state):
    _emit(state, {"type": "agent_start", "agent": "drill_down", "message": "Investigating..."})
    session = state["session"]
    if not session.insight_report:
        session.error_message = "Drill-down requires a prior analysis. Ask for an overview first."
        return {"session": session}
    df_parquet = state.get("df_parquet")
    try:
        response = await run_drill_down_agent(session, df_parquet)
        session.targeted_response = response
        _emit(state, {"type": "targeted_response", "data": response.model_dump()})
        _emit(state, {"type": "agent_complete", "agent": "drill_down", "message": "Answer ready"})
        if response.answer_text:
            session.add_message(MessageRole.ASSISTANT, response.answer_text)
        session.iteration += 1
    except Exception as exc:
        session.error_message = f"Drill-down failed: {exc}"
    ss = state.get("session_store")
    if ss:
        try:
            await ss.save(session)
        except Exception:
            pass
    _emit(state, {"type": "done"})
    return {"session": session}
```

The node never updates `overview_done` and never runs the senior_expert review — drill-down is always a follow-up.

### 5.5 Intent dispatch inside `targeted_analysis_node`

The existing `targeted_analysis_node` calls `run_targeted_analysis(session)` for any non-full-pipeline intent. Replace that single call with a dispatch:

```python
def _route_intent(session, df, emit) -> TargetedResponse:
    intent = session.task_plan.question_intent
    if intent == QuestionIntent.META_QUESTION:
        return _handle_meta_question(session)
    if intent == QuestionIntent.EXPORT_REQUEST:
        return _handle_export_request(session, emit)
    if intent == QuestionIntent.CHART_REQUEST:
        return _handle_chart_request(session, df)
    return run_targeted_analysis(session)   # existing path for the 7 deterministic intents
```

`run_targeted_analysis` (and its 540-line implementation in `targeted_analysis_agent.py`) is untouched. The three new handlers are sibling functions colocated with the dispatch or extracted to a small helpers module — implementer's call during planning.

---

## 6. OVERVIEW-on-Completed Approval Gate

### 6.1 Backend trigger (in `intake_node`)

The existing `intake_node` body has a single `if task.clarification_needed: _emit(...) else: _emit(agent_complete)` block. Restructure as an `if/elif/else` so the rerun gate slots in cleanly:

```python
if task.clarification_needed:
    _emit(state, {
        "type": "approval_required",
        "context": "clarification",
        "question": task.clarification_question,
    })
elif (task.question_intent == QuestionIntent.OVERVIEW
      and session.overview_done
      and session.status == "completed"
      and not state.get("is_approval")):
    _emit(state, {
        "type": "approval_required",
        "context": "rerun_overview",
        "question": "You already have a complete analysis for this dataset. Load the previous one, or run fresh?",
    })
    task.clarification_needed = True   # routes to human_approval via existing route_after_intake
else:
    _emit(state, {"type": "agent_complete", "agent": "intake", "message": "Analysis plan ready"})
```

Setting `clarification_needed = True` reuses the established `route_after_intake → human_approval` path without adding a new routing branch. The `elif` placement ensures we never double-emit `approval_required` (the existing clarification branch would otherwise fire on the next pass through the same code).

### 6.2 Chat-route handling of "Run fresh"

```python
if body.is_approval and body.approval_context == "rerun_overview":
    session.overview_done = False
    session.status = "in_progress"
    session.task_plan = None                       # force intake to re-classify on the fresh run
```

After this reset, the graph runs as a normal OVERVIEW pipeline.

### 6.3 Edge cases

- Brand-new session, OVERVIEW asked → `overview_done == False`, bypass.
- `failed` session, OVERVIEW asked → `status != "completed"`, bypass (gives a fresh run).
- Completed session, non-OVERVIEW intent → check requires `intent == OVERVIEW`, bypass.
- Completed session, OVERVIEW asked, user picks Load previous → frontend hits `/api/sessions/{id}/results` (Gap 6 endpoint) — no `/chat` call, no graph re-entry, no resource consumption.
- Completed session, OVERVIEW asked, user picks Cancel → no `/chat` call. The pending empty assistant bubble in `messages` is left as-is; existing UX behavior for unanswered approval prompts.

---

## 7. Frontend

### 7.1 New SSE event handler ([useAnalysis.ts](../../../frontend/src/hooks/useAnalysis.ts))

Add one case to the existing `switch (event.type)` in `parseSSEStream`:

```ts
case "export":
  if (event.format === "pptx") {
    void downloadLatestReportPptx();
  }
  break;
```

`downloadLatestReportPptx` is already exported from `lib/api.ts`. The `AgentEvent` discriminated union in `types/index.ts` gets one new variant: `{ type: "export"; format: string; url: string }`.

### 7.2 `useAnalysis.sendMessage` — optional `approvalContext`

Signature widens:

```ts
sendMessage(content: string, isApproval = false, approvalContext: string | null = null)
```

The fetch body includes `approval_context: approvalContext`. All existing call sites work unchanged because of the default value.

### 7.3 `ApprovalModal.tsx` — `rerun_overview` branch

The existing modal renders a free-text response area for `clarification` and `high_null`. Add a context-conditional branch:

```tsx
{pendingApproval.context === "rerun_overview" ? (
  <div className="flex gap-2 justify-end">
    <button onClick={onCancel} className="…">Cancel</button>
    <button onClick={onLoadPrevious} className="…">Load previous</button>
    <button onClick={onRunFresh} className="…">Run fresh</button>
  </div>
) : (
  /* existing clarification / high_null free-text response form */
)}
```

Three new optional callback props: `onLoadPrevious`, `onRunFresh`, `onCancel`. They are only invoked for the `rerun_overview` context.

### 7.4 `ChatInterface.tsx` — wire the three callbacks

```ts
const handleLoadPrevious = async () => {
  if (sessionId) {
    const snapshot = await getSessionResults(sessionId);
    if (snapshot) hydrateFromSnapshot(snapshot);
  }
  setPendingApproval(null);
};

const handleRunFresh = () => {
  const lastUserMessage = [...messages].reverse().find(m => m.role === "user")?.content ?? "";
  setPendingApproval(null);
  void sendMessage(lastUserMessage, true, "rerun_overview");
};

const handleCancelApproval = () => {
  setPendingApproval(null);
};
```

`getSessionResults` and `hydrateFromSnapshot` already exist from Gap 6 and need no changes.

### 7.5 Unaffected

`SessionSidebar`, `ConnectorSetup` dedup modal, chart rendering, insight panel, expert review panel — all reused unchanged. DRILL_DOWN and CHART_REQUEST responses flow through the existing `TargetedResponse` rendering in `TargetedResponsePanel.tsx`.

---

## 8. Phasing & Verification

Three phases. Each is shippable on its own and has explicit manual checks.

### Phase 1 — Classifier + the three deterministic intents

Scope:
- `QuestionIntent` enum (rename + 3 new).
- `AnalysisTask.model_validator` coercing `"deep_dive"` → `"drill_down"`.
- `TargetedParams.chart_type` field.
- `intake_agent` system prompt updates.
- `_FULL_PIPELINE_INTENTS` shrinks to `{OVERVIEW}`.
- `targeted_analysis_node` dispatch + the three new handlers (META, EXPORT, CHART).
- `build_single_chart` helper in `agents/visualization_agent.py`.
- New `export` SSE event type.

Verification gate:
- `uv run pytest tests/` — all existing tests pass.
- Backend boots clean.
- Direct invocation of `run_intake_agent` on each phrase classifies into the expected new intent:
  - "show me a bar chart of revenue by region" → `CHART_REQUEST`
  - "download as pptx" → `EXPORT_REQUEST`
  - "how many columns are there" → `META_QUESTION`
- On a session with a populated `data_contract`, sending `"how many rows do we have"` returns a `TargetedResponse` mentioning the row count; no analysis/insight nodes traverse.
- `"download as pptx"` on a completed session: SSE stream contains `{"type":"export","format":"pptx","url":"/dashboard/latest/report.pptx"}`.
- `"bar chart of <col1> by <col2>"`: `TargetedResponse.chart_configs` contains exactly one `ChartConfig` with `chart_type == "BarChart"`.

### Phase 2 — DRILL_DOWN + OVERVIEW-on-completed gate

Scope:
- `agents/drill_down_agent.py` (new).
- `drill_down_node` in `graph.py` + routing additions in `routing.py` and `build_graph`.
- `intake_node` detects `OVERVIEW + completed + overview_done` and sets the `rerun_overview` sentinel.
- `ChatRequest.approval_context` field.
- Chat-route resets `overview_done`, `status`, `task_plan` on `approval_context == "rerun_overview"`.

Verification gate:
- Backend boots clean.
- On a session with an `insight_report`, asking `"why is X happening?"` → log shows `loaded cached parquet`, graph traverses `ingestion → intake → drill_down → END`, `TargetedResponse` returned with text + optional chart.
- DRILL_DOWN on a session that has NO `insight_report` → graceful `error_message` "Drill-down requires a prior analysis."
- On a session where `status == "completed"` and `overview_done == True`, sending `"analyze this dataset"` → SSE emits `{"type":"approval_required","context":"rerun_overview"}`, no full pipeline runs.
- Resending the same message with `is_approval=true, approval_context="rerun_overview"` triggers a full OVERVIEW run; afterward `session.insight_report` reflects the rerun.

### Phase 3 — Frontend

Scope:
- `AgentEvent` union extended; `useAnalysis` handles the `export` event.
- `sendMessage` accepts `approvalContext`.
- `ApprovalModal` renders Load previous / Run fresh / Cancel for `rerun_overview`.
- `ChatInterface` wires the three handlers + reuses Gap 6's `getSessionResults` + `hydrateFromSnapshot`.

Verification gate:
- `npm run build` succeeds.
- Manual UI smoke (real API keys + a real session needed):
  - `"how many columns?"` after a completed analysis → text reply only, no dashboard rebuild.
  - `"download as pptx"` → PPTX file downloads (browser save dialog).
  - `"show me a scatter of X vs Y"` → one chart renders in chat.
  - `"why is Z happening?"` → compact answer + optional chart, no dashboard rebuild.
  - `"analyze this data"` on a completed session → modal appears with three buttons.
  - Load previous → UI re-hydrates with the existing analysis; no `/chat` request in Network tab.
  - Run fresh → new pipeline run; fresh `insight_report` replaces the prior one.
  - Cancel → modal closes, workspace unchanged.

---

## 9. Open Questions

None. All design decisions ratified during brainstorming (approach, scope split, DEEP_DIVE rename, handler granularity, backward-compat coercion).
