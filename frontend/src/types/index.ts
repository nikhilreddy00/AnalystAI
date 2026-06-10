// Mirror of Python Pydantic models — kept in sync with orchestrator/state.py

export type ChartType =
  | "LineChart"
  | "BarChart"
  | "HorizontalBarChart"
  | "ScatterChart"
  | "PieChart"
  | "DonutChart"
  | "HistogramChart"
  | "HeatmapChart"
  | "ComposedChart";

export interface ChartConfig {
  id: string;
  chart_type: ChartType;
  title: string;
  data: Record<string, unknown>[];
  x_key: string;
  y_keys: string[];
  colors: string[];
  x_label?: string;
  y_label?: string;
  show_legend: boolean;
  show_grid: boolean;
  source_finding: string;
  value_format?: "number" | "percent";  // PROMPT-2: y-axis tick/tooltip formatter
  layout_hint?: "half_width" | "full_width";  // PROMPT-6: dashboard grid hint
}

export interface RecommendedAction {
  priority: number;
  action: string;
  reasoning: string;
  metric_impact?: string;
}

export interface AnomalyAlert {
  title: string;
  severity: "low" | "medium" | "high" | "critical";
  description: string;
  affected_metric: string;
  suggested_investigation: string;
}

// PROMPT-3: structured executive-tab content. Plain English, no stats notation,
// no raw column names — backend enforces via prompt + sanitizer.
export interface ActionItem {
  action: string;            // verb-led, specific
  expected_impact: string;   // business impact in one line
}

export interface ExecutiveSummary {
  headline: string;
  paragraphs: string[];      // max 4 sentences combined
  findings: string[];        // plain-language bullets
  action_items: ActionItem[];
}

// PROMPT-3: structured analyst-tab content. Keeps statistical notation, exact
// thresholds, raw column names, methodology, caveats.
export interface AnalystDetail {
  methodology: string;
  statistical_notes: string[];
  caveats: string[];
  code_references: string[];
}

export interface InsightReport {
  executive_summary: string;             // legacy: flattened from executive_view
  technical_findings: string[];          // legacy: from analyst_view.statistical_notes
  recommended_actions: RecommendedAction[];  // legacy: from executive_view.action_items
  anomaly_alerts: AnomalyAlert[];
  proactive_questions: string[];
  methodology_notes: string;             // legacy: from analyst_view.methodology
  executive_view?: ExecutiveSummary | null;  // PROMPT-3
  analyst_view?: AnalystDetail | null;       // PROMPT-3
  additional_issues_note?: string;           // PROMPT-4: "N additional minor issues..."
  generated_at: string;
}

// Phase 6: senior-expert critique from the 7th agent
export interface ExpertReview {
  review_summary: string;
  strengths: string[];
  gaps: string[];
  next_investigations: string[];
  methodology_concerns: string[];
  generated_at: string;
}

// Phase A: targeted-question intent classifier (mirrors orchestrator/state.py)
export type QuestionIntent =
  | "overview"
  | "deep_dive"
  | "correlation"
  | "distribution"
  | "top_n"
  | "time_series"
  | "segment_compare"
  | "hypothesis_test"
  | "ab_significance"
  | "outlier_scan";

// Phase A: compact response for a targeted question (not the full report)
export interface TargetedResponse {
  intent: QuestionIntent;
  answer_text: string;
  chart_configs: ChartConfig[];
  table_data: Record<string, unknown>[];
  table_columns: string[];
  suggestions: string[];
  unanswerable_reason?: string | null;
  generated_at: string;
}

// Discriminated union over `type` — mirrors orchestrator/graph.py SSE emissions.
// Switching on `event.type` narrows to one variant; the compiler then enforces
// that only that variant's fields are read.

export interface SessionIdEvent {
  type: "session_id";
  value: string;
}

export interface AgentStartEvent {
  type: "agent_start";
  agent: string;
  message: string;
}

export interface AgentCompleteEvent {
  type: "agent_complete";
  agent: string;
  message: string;
}

export interface ApprovalRequiredEvent {
  type: "approval_required";
  context: "clarification" | "high_null";
  question?: string;
  high_null_columns?: string[];
}

export interface SuggestedQuestionsEvent {
  type: "suggested_questions";
  questions: string[];
}

// PROMPT-2: backend-computed hero KPI card. Values are pre-formatted display
// strings — the frontend renders them verbatim, no numeric guessing required.
export interface KPI {
  label: string;
  value: string;
  context: string;
}

export interface BriefingData {
  head_preview: Record<string, unknown>[];
  dtypes_summary: Record<string, string>;
  missing_pct: Record<string, number>;
  describe_stats: Record<string, Record<string, unknown>>;
  kpis?: KPI[];
  // PROMPT-10: briefing-table extras
  pre_cleaning_missing_pct?: Record<string, number>;
  unique_counts?: Record<string, number>;
  sample_values_per_col?: Record<string, unknown[]>;
  skewness?: Record<string, number>;
  target_correlations?: Record<string, number>;
  target_col?: string | null;
  column_roles_summary?: Record<string, string>;
}

export interface BriefingEvent {
  type: "briefing";
  data: BriefingData;
}

export interface ChartsEvent {
  type: "charts";
  data: ChartConfig[];
}

export interface InsightEvent {
  type: "insight";
  data: InsightReport;
}

export interface ExpertReviewEvent {
  type: "expert_review";
  data: ExpertReview;
}

export interface TargetedResponseEvent {
  type: "targeted_response";
  data: TargetedResponse;
}

export interface QualityRetryEvent {
  type: "quality_retry";
  message: string;
}

export interface ErrorEvent {
  type: "error";
  message: string;
}

export interface DoneEvent {
  type: "done";
}

export interface ExportEvent {
  type: "export";
  format: string;
  url: string;
}

export type AgentEvent =
  | SessionIdEvent
  | AgentStartEvent
  | AgentCompleteEvent
  | ApprovalRequiredEvent
  | SuggestedQuestionsEvent
  | BriefingEvent
  | ChartsEvent
  | InsightEvent
  | ExpertReviewEvent
  | TargetedResponseEvent
  | QualityRetryEvent
  | ErrorEvent
  | DoneEvent
  | ExportEvent;

export interface ChatMessage {
  role: "user" | "assistant";
  content: string;
  briefing?: BriefingData;     // Phase 2: dataset preview tables (head/schema/describe)
  charts?: ChartConfig[];
  insight?: InsightReport;
  expertReview?: ExpertReview; // Phase 6: senior-expert critique (first-run only)
  targetedResponse?: TargetedResponse; // Phase A: compact answer for targeted intents
  isStreaming?: boolean;
}

export type SessionStatus = "in_progress" | "completed" | "failed";

export interface SessionSummary {
  session_id: string;
  dataset_name: string | null;
  original_query: string | null;
  row_count: number | null;
  column_count: number | null;
  status: SessionStatus;
  created_at: string;
  updated_at: string;
}

export interface SessionResultsSnapshot {
  session_id: string;
  dataset_name: string | null;
  status: SessionStatus;
  original_query: string | null;
  row_count: number | null;
  column_count: number | null;
  insight_report: InsightReport | null;
  targeted_response: TargetedResponse | null;
  expert_review: ExpertReview | null;
  chart_configs: ChartConfig[];
  conversation_history: { role: "user" | "assistant" | "system"; content: string }[];
}

export interface ExistingSessionHint {
  session_id: string;
  dataset_name: string | null;
  updated_at: string;
  original_query: string | null;
}

export interface UploadResponse {
  status: string;
  filename: string;
  file_path: string;
  connector_config: Record<string, unknown>;
  file_hash: string;
  dataset_name: string;
  existing_session: ExistingSessionHint | null;
}
