"""
All inter-agent data contracts. Every agent reads and writes these schemas.
LangGraph uses AnalysisSession as its shared state object.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


# ── Enums ──────────────────────────────────────────────────────────────────────

class ConnectorType(str, Enum):
    CSV = "csv"
    SQL = "sql"
    SHEETS = "sheets"
    API = "api"


class AnalysisType(str, Enum):
    EDA = "eda"
    TREND = "trend"
    SEGMENTATION = "segmentation"
    ANOMALY = "anomaly"
    CORRELATION = "correlation"
    HYPOTHESIS = "hypothesis"
    FORECASTING = "forecasting"


class QuestionIntent(str, Enum):
    """Phase A: classifier output that drives pipeline routing.

    OVERVIEW runs the full 7-agent pipeline.
    DRILL_DOWN runs a compact follow-up via drill_down_node.
    All other intents route through targeted_analysis_node for a compact answer.
    """
    OVERVIEW = "overview"
    DRILL_DOWN = "drill_down"
    CORRELATION = "correlation"
    DISTRIBUTION = "distribution"
    TOP_N = "top_n"
    TIME_SERIES = "time_series"
    SEGMENT_COMPARE = "segment_compare"
    HYPOTHESIS_TEST = "hypothesis_test"
    AB_SIGNIFICANCE = "ab_significance"
    OUTLIER_SCAN = "outlier_scan"
    CHART_REQUEST = "chart_request"
    EXPORT_REQUEST = "export_request"
    META_QUESTION = "meta_question"


class ChartType(str, Enum):
    LINE = "LineChart"
    BAR = "BarChart"
    HORIZONTAL_BAR = "HorizontalBarChart"
    SCATTER = "ScatterChart"
    PIE = "PieChart"
    DONUT = "DonutChart"
    HISTOGRAM = "HistogramChart"
    HEATMAP = "HeatmapChart"
    COMPOSED = "ComposedChart"


class MessageRole(str, Enum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


class Severity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


# ── Building blocks ────────────────────────────────────────────────────────────

class Message(BaseModel):
    role: MessageRole
    content: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ColumnInfo(BaseModel):
    name: str
    dtype: str
    nullable: bool
    sample_values: list[Any]
    is_date: bool = False
    is_id: bool = False
    is_metric: bool = False
    null_pct: float = 0.0


class ConnectorConfig(BaseModel):
    type: ConnectorType
    # CSV / Excel
    file_path: str | None = None
    # SQL
    connection_string: str | None = None
    table_name: str | None = None
    # Google Sheets
    spreadsheet_id: str | None = None
    sheet_name: str | None = None
    service_account_json: str | None = None
    # REST API
    url: str | None = None
    auth_type: str | None = None           # bearer | api_key | basic
    auth_value: str | None = None
    pagination_key: str | None = None


# ── Agent 1 output: AnalysisTask ───────────────────────────────────────────────

class TargetedParams(BaseModel):
    """Intent-specific parameters extracted by the intake agent for targeted queries.

    All fields optional — the classifier fills only what the intent needs. Downstream
    handlers in `targeted_analysis_node` read whichever subset matches their intent.
    """
    target_columns: list[str] = Field(default_factory=list)  # CORRELATION/OUTLIER_SCAN
    metric_column: str | None = None        # TOP_N, TIME_SERIES, SEGMENT_COMPARE, AB
    date_column: str | None = None          # TIME_SERIES
    segment_column: str | None = None       # SEGMENT_COMPARE
    group_by: str | None = None             # TOP_N grouping dimension
    variant_column: str | None = None       # AB_SIGNIFICANCE (e.g., "variant" / "group")
    n: int | None = None                    # TOP_N
    order: str | None = None                # TOP_N: "asc" | "desc"
    frequency: str | None = None            # TIME_SERIES: "D" | "W" | "M" | "Q" | "Y"
    test_type: str | None = None            # HYPOTHESIS_TEST: "ttest" | "chi2" | "anova"
    aggregation: str | None = None          # TOP_N / SEGMENT_COMPARE: sum/mean/count/median
    chart_type: ChartType | None = None     # CHART_REQUEST: bar/line/scatter/etc.


class AnalysisTask(BaseModel):
    """Produced by the Stakeholder Intake Agent from natural language input."""
    raw_query: str
    target_metrics: list[str]
    timeframe: str | None = None           # e.g. "last 90 days", "2025-Q3"
    segments: list[str] = Field(default_factory=list)   # group-by columns
    analysis_types: list[AnalysisType]
    output_format: str = "both"            # executive | analyst | both
    resume_from: str | None = None         # for multi-turn: skip to this node
    clarification_needed: bool = False
    clarification_question: str | None = None
    # Phase A: depth-aware routing. Default OVERVIEW is safe for first-message uploads
    # and any code path that hasn't been updated to set this explicitly.
    question_intent: QuestionIntent = QuestionIntent.OVERVIEW
    targeted_params: TargetedParams = Field(default_factory=TargetedParams)

    @model_validator(mode="before")
    @classmethod
    def _coerce_deprecated_intents(cls, data):
        # Persisted sessions from before the DEEP_DIVE → DRILL_DOWN rename will
        # carry "deep_dive" in their JSON. Silently upgrade so model_validate_json
        # never blows up loading them.
        if isinstance(data, dict) and data.get("question_intent") == "deep_dive":
            data = {**data, "question_intent": "drill_down"}
        return data


# ── Agent 2 output: DataContract ──────────────────────────────────────────────

class DataContract(BaseModel):
    """Produced by the Data Ingestion Agent. All downstream agents consume this."""
    source_type: ConnectorType
    row_count: int
    column_count: int
    columns: list[ColumnInfo]
    sample_rows: list[dict[str, Any]]      # first 5 rows
    date_columns: list[str]
    id_columns: list[str]
    metric_columns: list[str]
    schema_summary: str                    # human-readable description for LLM prompts


# ── Agent 3 output: CleaningAudit ─────────────────────────────────────────────

class CleaningDecision(BaseModel):
    column: str
    operation: str                         # impute | drop | coerce | normalize | flag
    strategy: str                          # median | mean | mode | forward_fill | drop_rows
    reason: str                            # human-readable justification
    rows_affected: int
    before_null_pct: float
    after_null_pct: float


class AnomalyFlag(BaseModel):
    """PROMPT-5: structured per-column outlier summary. The detector produces
    these and the insight agent renders them as scannable alerts.
    `heavy_tailed=True` means the column was *not* flagged because its
    distribution is naturally wide (>5% of values past the outlier bound)."""
    column: str
    extreme_count: int = 0                # |z|>3 in IQR space (Q±3·IQR)
    mild_count: int = 0                   # 1.5·IQR ≤ deviation ≤ 3·IQR (analyst tab only)
    extreme_pct: float = 0.0              # 0-100, of N
    lower_bound: float = 0.0              # extreme boundary in original scale (post-inverse-transform if log1p applied)
    upper_bound: float = 0.0
    mild_lower_bound: float = 0.0         # mild boundary in original scale
    mild_upper_bound: float = 0.0
    used_log_transform: bool = False      # True if |skew|>2 triggered log1p before IQR
    heavy_tailed: bool = False            # True → suppressed under the 5% guard
    message: str                          # one-line scannable description with threshold value


class AnomalyDetectionReport(BaseModel):
    """Bundles per-column flags with a global methodology note. The note is
    set when the 10% cap re-ran detection in extreme-only mode."""
    flags: list[AnomalyFlag] = Field(default_factory=list)
    methodology_note: str = ""            # populated when extreme-only fallback kicked in
    eligible_columns: list[str] = Field(default_factory=list)
    skipped_columns: dict[str, str] = Field(default_factory=dict)  # column → reason skipped


class CleaningAudit(BaseModel):
    """Produced by the Data Cleaning Agent. Shows every decision made."""
    decisions: list[CleaningDecision]
    rows_before: int
    rows_after: int
    columns_coerced: list[str]
    duplicates_removed: int
    outliers_flagged: list[str]
    high_null_columns: list[str]           # columns >40% null (triggers approval gate)
    summary: str                           # paragraph for the UI
    anomaly_report: AnomalyDetectionReport | None = None  # PROMPT-5: structured detector output
    skipped_columns: list[str] = Field(default_factory=list)


# ── Agent 4 output: AnalysisFindings ──────────────────────────────────────────

class AgentCard(BaseModel):
    """Self-knowledge of one agent (ADR-0001): injected into its system prompt, and what the UI
    shows as the agent's role and limits. `version` is the prompt version the agent runs."""
    name: str
    version: str
    scope: str
    inputs: list[str]
    outputs: list[str]
    limits: list[str]
    refusal_rules: list[str]

    def render(self) -> str:
        def bullets(items: list[str]) -> str:
            return "\n".join(f"- {i}" for i in items)
        return (f"# Agent card: {self.name} ({self.version})\n{self.scope}\n\n"
                f"Inputs:\n{bullets(self.inputs)}\nOutputs:\n{bullets(self.outputs)}\n"
                f"Known limits:\n{bullets(self.limits)}\n"
                f"Refuse or say so when:\n{bullets(self.refusal_rules)}")


class StatisticalResult(BaseModel):
    test_name: str
    statistic: float
    p_value: float | None = None
    interpretation: str


class AnomalyRecord(BaseModel):
    column: str
    row_index: int | None = None
    value: Any
    expected_range: str
    severity: Severity
    explanation: str


class TrendResult(BaseModel):
    column: str
    period: str                            # MoM | YoY | 7d_rolling
    direction: str                         # up | down | flat
    pct_change: float = 0.0


class SegmentResult(BaseModel):
    segment_column: str
    metric_column: str
    aggregation: str                       # sum | mean | count | pct
    results: list[dict[str, Any]]         # [{segment: "West", value: 42000}, ...]


class KPIRecipe(BaseModel):
    """Structured recipe describing how a KPI is computed.
    Lets the frontend recompute the filtered value deterministically from raw rows."""
    kind: Literal[
        "row_count",
        "target_rate",
        "metric_avg",
        "top_cat",
        "comparison_split",
        "highest_cv",
    ]
    column: str | None = None
    target_column: str | None = None
    segment_column: str | None = None
    format_kind: str | None = None  # "currency" | "percent" | "count" | "number"


class KPI(BaseModel):
    """A single hero metric card. Backend computes formatted display strings so the
    frontend never has to guess whether a value is currency, percent, or count."""
    label: str                            # Title-cased card label (e.g. "Survival Rate")
    value: str                            # pre-formatted display string ("62.5%", "$12,450.00")
    context: str = ""                     # one-line sublabel (range, share, comparison)
    recipe: KPIRecipe | None = None


class DataNarrative(BaseModel):
    """LLM-generated rendering hints for a dataset. Built once per dataset
    connection and reused by every downstream rendering agent (KPI, chart titles,
    alert text). Generating it at ingestion keeps render paths deterministic — no
    LLM calls in chart/KPI code."""
    column_display_names: dict[str, str] = Field(default_factory=dict)  # raw col → "Passenger Class"
    entity_singular: str = "record"        # what one row represents — "passenger", "order"
    entity_plural: str = "records"         # plural form — "passengers", "orders"


class AnalysisFindings(BaseModel):
    """Produced by the Analysis Agent from E2B sandbox execution."""
    eda_summary: dict[str, Any]           # describe() output per metric column
    correlations: dict[str, float]        # column_pair → correlation coefficient
    trends: list[TrendResult | dict[str, Any]]  # rolling avg, MoM/YoY
    segments: list[SegmentResult]
    anomalies: list[AnomalyRecord]
    statistical_tests: list[StatisticalResult]
    forecast_points: list[dict[str, Any]] = Field(default_factory=list)
    distributions: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)  # chart-ready histogram/value_counts data
    code_executed: str                    # the Python code that ran in E2B
    figures_base64: list[str] = Field(default_factory=list)  # matplotlib outputs
    # Phase 2 — briefing payload rendered as tables in the UI
    head_preview: list[dict[str, Any]] = Field(default_factory=list)       # df.head(5).to_dict("records")
    dtypes_summary: dict[str, str] = Field(default_factory=dict)           # column → dtype string
    missing_pct: dict[str, float] = Field(default_factory=dict)            # column → null % (post-cleaning)
    kpis: list[KPI] = Field(default_factory=list)                          # PROMPT-2: hero KPI cards
    # PROMPT-10: briefing-table extras computed from df + roles + contract.
    pre_cleaning_missing_pct: dict[str, float] = Field(default_factory=dict)   # null % BEFORE cleaning (from contract)
    unique_counts: dict[str, int] = Field(default_factory=dict)                # nunique per column
    sample_values_per_col: dict[str, list[Any]] = Field(default_factory=dict)  # 3 representative values per column
    skewness: dict[str, float] = Field(default_factory=dict)                   # Fisher-Pearson skew per numeric column
    target_correlations: dict[str, float] = Field(default_factory=dict)        # Pearson r vs target_col (when one exists)
    target_col: str | None = None                                              # auto-detected target column name
    column_roles_summary: dict[str, str] = Field(default_factory=dict)         # column → role (id/binary/continuous/...)


# ── Agent 5 output: ChartConfig ───────────────────────────────────────────────

class ChartConfig(BaseModel):
    """Recharts-compatible config. Frontend renders this directly."""
    id: str                               # unique identifier for dashboard pinning
    chart_type: ChartType
    title: str
    data: list[dict[str, Any]]           # [{name: "Jan", value: 4200}, ...]
    x_key: str
    y_keys: list[str]
    colors: list[str] = Field(default_factory=list)  # empty = let the frontend palette (Tableau 10) decide
    x_label: str | None = None
    y_label: str | None = None
    show_legend: bool = True
    show_grid: bool = True
    source_finding: str                   # which finding generated this chart
    # PROMPT-2: how the y-axis values should be rendered. "percent" tells the
    # frontend to suffix ticks/tooltips with "%" (values are pre-multiplied 0..100).
    value_format: str = "number"          # "number" | "percent"
    # PROMPT-6: dashboard layout hint. "half_width" → 2 per row; "full_width" → spans.
    layout_hint: str = "half_width"       # "half_width" | "full_width"


# ── Agent 6 output: InsightReport ─────────────────────────────────────────────

class RecommendedAction(BaseModel):
    priority: int                         # 1 = highest
    action: str
    reasoning: str
    metric_impact: str | None = None


class AnomalyAlert(BaseModel):
    title: str
    severity: Severity
    description: str
    affected_metric: str
    suggested_investigation: str


class ActionItem(BaseModel):
    """PROMPT-3: executive-tab action item. Verb-led sentence with expected
    business impact. Separate from the legacy RecommendedAction (analyst view).
    """
    action: str                          # "Review all March transactions over $10,000"
    expected_impact: str = ""            # "12 flagged entries may indicate processing errors"


class ExecutiveSummary(BaseModel):
    """PROMPT-3: plain-English deliverable for non-technical stakeholders.
    No statistical notation, no raw column names, no method jargon — those go in
    AnalystDetail. Backend enforces this via the insight prompt + a sanitizer."""
    headline: str = ""                                   # 1-line top-of-page TL;DR
    paragraphs: list[str] = Field(default_factory=list)  # max 4 sentences combined
    findings: list[str] = Field(default_factory=list)    # plain-language bullets
    action_items: list[ActionItem] = Field(default_factory=list)


class AnalystDetail(BaseModel):
    """PROMPT-3: reproducibility-grade detail for analysts. Keeps statistical
    notation, exact thresholds, column names, methodology, and caveats."""
    methodology: str = ""                                # full method description
    statistical_notes: list[str] = Field(default_factory=list)  # bullets with stats notation
    caveats: list[str] = Field(default_factory=list)            # assumptions, limitations
    code_references: list[str] = Field(default_factory=list)    # how key metrics were derived


class InsightReport(BaseModel):
    """Final output. Powers both Executive View and Analyst View in the UI.

    PROMPT-3: `executive_view` and `analyst_view` carry the new structured tab
    content. The legacy flat fields (`executive_summary`, `technical_findings`,
    `methodology_notes`, `recommended_actions`) stay populated — derived from
    the new blocks — so chat history, embeddings, and PPTX export keep working.
    """
    executive_summary: str               # legacy: derived from executive_view headline + paragraphs
    technical_findings: list[str]        # legacy: derived from analyst_view.statistical_notes
    recommended_actions: list[RecommendedAction]  # legacy: derived from executive_view.action_items
    anomaly_alerts: list[AnomalyAlert]
    proactive_questions: list[str]       # 3 follow-up questions the system suggests
    methodology_notes: str               # legacy: derived from analyst_view.methodology
    executive_view: ExecutiveSummary | None = None  # PROMPT-3: new structured executive tab
    analyst_view: AnalystDetail | None = None       # PROMPT-3: new structured analyst tab
    additional_issues_note: str = ""                # PROMPT-4: "N additional minor issues detected..."
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# ── Phase A output: TargetedResponse ──────────────────────────────────────────

class TargetedResponse(BaseModel):
    """
    Compact response for a targeted question (correlation, top-N, etc).
    Replaces the full InsightReport for non-overview/non-drill_down intents.
    Rendered as a single chat card in the UI; chart is not auto-pinned.
    """
    intent: QuestionIntent
    answer_text: str                            # 1-2 sentence direct answer
    chart_configs: list[ChartConfig] = Field(default_factory=list)
    table_data: list[dict[str, Any]] = Field(default_factory=list)
    table_columns: list[str] = Field(default_factory=list)
    suggestions: list[str] = Field(default_factory=list)  # Q11 fallback alternatives
    unanswerable_reason: str | None = None
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# ── Agent 7 output: ExpertReview (Phase 6) ────────────────────────────────────

class ExpertReview(BaseModel):
    """
    Senior-expert critique of the analysis. Only generated on the first-run
    auto-overview — follow-up Q&A skips this step.
    """
    review_summary: str                  # 2-3 sentences: overall assessment
    strengths: list[str]                 # what the analysis did well
    gaps: list[str]                      # what's missing or could go deeper
    next_investigations: list[str]       # specific recommended deeper dives
    methodology_concerns: list[str]      # caveats, assumptions to verify
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# ── Top-level session state (LangGraph shared state) ──────────────────────────

class SessionSummary(BaseModel):
    """Lightweight session metadata for the sidebar list and dedup lookup.
    Excludes insight_report / chart_configs to keep list responses cheap.
    """
    session_id: str
    dataset_name: str | None = None
    original_query: str | None = None
    row_count: int | None = None
    column_count: int | None = None
    status: Literal["in_progress", "completed", "failed"] = "in_progress"
    created_at: datetime
    updated_at: datetime


class AnalysisSession(BaseModel):
    """
    Single source of truth for a complete analysis session.
    Persisted to PostgreSQL as JSONB after every agent turn.
    LangGraph reads/writes this as its state object.
    """
    session_id: str
    user_id: str = "default"
    conversation_history: list[Message] = Field(default_factory=list)
    connector_config: ConnectorConfig | None = None
    task_plan: AnalysisTask | None = None
    data_contract: DataContract | None = None
    cleaning_audit: CleaningAudit | None = None
    analysis_findings: AnalysisFindings | None = None
    chart_configs: list[ChartConfig] = Field(default_factory=list)
    insight_report: InsightReport | None = None
    targeted_response: TargetedResponse | None = None  # Phase A: compact answer for targeted intents
    expert_review: ExpertReview | None = None     # Phase 6: senior-expert critique (first-run only)
    narrative: DataNarrative | None = None  # PROMPT-2 follow-up: LLM-generated display names + entity, built once per dataset
    iteration: int = 0
    quality_check_attempts: int = 0        # FEAT-02: entries into quality_check_node per graph run
    overview_done: bool = False            # Phase 3: True after first comprehensive analysis ships; resets when connector_config changes
    dataset_name: str | None = None
    file_hash: str | None = None
    status: Literal["in_progress", "completed", "failed"] = "in_progress"
    cleaned_data_path: str | None = None
    error_message: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def add_message(self, role: MessageRole, content: str) -> None:
        self.conversation_history.append(Message(role=role, content=content))
        self.updated_at = datetime.now(timezone.utc)
