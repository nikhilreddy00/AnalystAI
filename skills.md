# AnalystAI — Skills & Domain Expertise Reference

> **Purpose**: Codifies the domain expertise each agent must exhibit. Think of this as "how a senior data analyst thinks" — distilled into rules that survive across AI sessions. Reference this file when modifying any agent's prompt or logic.

---

## 1. Stakeholder Intake — "The Translator"

### What a senior analyst does:
- Reads between the lines: "how are we doing?" really means "show me the KPI dashboard"
- Decomposes compound requests: "revenue and churn by segment" = two separate analyses, ordered
- Remembers context: follow-up "drill into Q3" doesn't restart the analysis, it refines it
- Asks ONE smart clarifying question, not five — and makes it multiple-choice when possible

### Decision heuristics:
| User says | Analyst thinks | Result |
|-----------|---------------|--------|
| "how are we doing?" | Zero signal → clarify | `clarification_needed=true`, ask about metric focus |
| "show revenue by region" | Clear target + segment | `target_metrics=["revenue"], segments=["region"], analysis_types=["eda","segmentation"]` |
| "is there anything weird?" | Anomaly hunting | `analysis_types=["eda","anomaly"]` |
| "compare plan tiers" | Hypothesis test | `analysis_types=["eda","segmentation","hypothesis"]` |
| "predict next quarter" | Forecasting | `analysis_types=["eda","trend","forecasting"]` |

### Resume logic:
- If the user refines the same question → `resume_from="analysis"` (skip re-ingestion/cleaning)
- If the user asks about a completely different metric → `resume_from=null` (full pipeline)
- If data_contract already exists and user drills deeper → resume

---

## 2. Data Ingestion — "The Data Engineer"

### What a senior analyst does:
- Immediately checks: row count, column count, data types, sample values
- Classifies columns: ID (never aggregate), date (time axis), metric (aggregate), categorical (segment)
- Flags suspicious patterns: mixed types, encoded nulls ("N/A", "null", "-", ""), inconsistent date formats
- Produces a plain-English schema summary a business user can understand

### Column classification heuristics:
| Pattern | Classification |
|---------|---------------|
| Name contains "id", "uuid", "key", "code" | **ID** (never aggregate) |
| dtype is datetime OR name contains "date", "time", "at", "created", "updated" | **Date** |
| dtype is int64/float64 AND not ID | **Metric** (can aggregate) |
| Everything else | **Categorical** (segment/filter) |

### Data source robustness:
- CSV: detect encoding (chardet), handle BOM, mixed delimiters
- SQL: parameterized queries ONLY — never interpolate user input
- Sheets: handle empty rows, merged cells, numeric coercion
- API: follow pagination, normalize nested JSON with `pd.json_normalize`

---

## 3. Data Cleaning — "The Quality Inspector"

### What a senior analyst does:
- NEVER just drops all nulls — each column gets a strategy based on its distribution
- Documents every decision with reasoning (audit trail)
- Flags outliers but doesn't remove them (removing valid extreme values is dangerous)
- Coerces types gently — parse dates with `errors="coerce"`, not hard failures

### Imputation decision matrix:
| Column type | Distribution | Null % | Strategy | Why |
|-------------|-------------|--------|----------|-----|
| Numeric | Normal (|skew| < 1) | < 80% | **Mean** | Preserves distribution center |
| Numeric | Skewed (|skew| ≥ 1) | < 80% | **Median** | Resistant to outlier distortion |
| Categorical | Any | < 80% | **Mode** | Most common value is safest default |
| Time-series | Ordered | < 80% | **Forward fill** | Temporal continuity |
| Any | Any | > 80% | **Drop rows** | Column is unreliable |

### Outlier detection (IQR method):
- Lower bound: Q1 - 1.5 × IQR
- Upper bound: Q3 + 1.5 × IQR
- Flag but NEVER auto-remove (analyst reviews)
- Severity: >3σ = critical, >2.5σ = high, >2σ = medium, else = low

### Human approval trigger:
- Any column with >40% null → pause for user decision
- Options: continue with imputation / drop column / cancel

---

## 4. Analysis — "The Statistician"

### What a senior analyst does:
- Always starts with EDA (describe, distributions, correlations) regardless of the specific question
- Chooses the right statistical test based on data types, not a one-size-fits-all approach
- Wraps each analysis section in try/except — partial results are better than total failure
- Thinks about statistical significance, not just patterns

### Analysis type selection:
| Requested type | What to compute |
|---------------|----------------|
| **EDA** | df.describe(), value_counts, correlation matrix, distribution shapes |
| **Trend** | Rolling averages, MoM/YoY deltas, linear regression on time axis |
| **Segmentation** | groupby(segment_col)[metric_col].agg(), top 10 groups |
| **Anomaly** | IQR flagging on all metric columns, severity scoring |
| **Correlation** | Pairwise Pearson r for numeric columns, filter |r| > 0.2 |
| **Hypothesis** | t-test (numeric vs. 2 groups), chi-square (categorical vs. categorical) |
| **Forecasting** | numpy.polyfit(degree=1) extrapolation (v1), Prophet (v2) |

### Statistical test selection guide:
| Data scenario | Test |
|--------------|------|
| Compare 2 numeric group means | Independent t-test |
| Compare 2+ numeric group means | ANOVA F-test |
| Categorical vs. categorical association | Chi-square test |
| Numeric vs. ordinal relationship | Spearman rank correlation |
| Before/after on same subjects | Paired t-test |

### Code execution safety:
- All code runs in E2B sandbox (isolated VM)
- Data is sent as CSV (E2B lacks pyarrow)
- Output is JSON via `print(json.dumps(result, default=str))`
- Guaranteed EDA code runs AFTER Claude's code to ensure charts always have data

---

## 5. Visualization — "The Chart Designer"

### What a senior analyst does:
- Picks the chart type that best communicates the insight, not the fanciest one
- Limits to 5 charts max — quality over quantity
- Falls back gracefully: distributions → segments → EDA stats → contract metadata

### Chart selection rules:
| Data shape | Chart type |
|-----------|-----------|
| Time series metric | LineChart |
| Categorical ≤ 5 groups | PieChart |
| Categorical 6-8 groups | BarChart |
| Categorical > 8 groups | HorizontalBarChart (top 10) |
| Numeric distribution | HistogramChart (binned BarChart) |
| Two numeric variables | ScatterChart |
| Multiple metrics over time | ComposedChart (bars + lines) |

### Fallback cascade:
1. `findings.distributions` → real histogram/value_counts from E2B
2. `findings.segments` → real segmentation breakdowns from E2B
3. `findings.eda_summary` → descriptive stats bar chart
4. `contract.columns` → null-rate chart + column type pie (always available)

---

## 6. Insight Delivery — "The Storyteller"

### What a senior analyst does:
- ALWAYS answers the user's specific question first, then provides context
- Uses exact numbers, column names, and row counts — never vague language
- Prioritizes recommended actions by business impact
- Suggests 3 follow-up questions that a curious stakeholder would ask
- References past analyses when patterns repeat (institutional memory)

### Output structure:
```
executive_summary    — CEO-readable: answer the question, then context (3-5 sentences)
technical_findings   — Analyst-readable: 3-8 bullets with actual numbers
recommended_actions  — Prioritized 1→N with reasoning and metric impact
anomaly_alerts       — Severity-scored flags with investigation suggestions
proactive_questions  — 3 natural follow-up questions
methodology_notes    — What was done and why (Analyst View only)
```

### Institutional memory:
- After each analysis, the InsightReport is embedded and stored in Qdrant
- On new analyses, top-3 similar past reports are retrieved and injected as context
- Enables: "8 months ago we saw a similar revenue dip tied to Q4 seasonality"

---

## Feedback Loop

The system supports iterative refinement:
1. Insight Agent delivers results with 3 proactive follow-up questions
2. User clicks a follow-up or types a new question
3. Intake Agent amends the existing task (not creates new)
4. If same dataset → `resume_from="analysis"` (skip re-ingestion/cleaning)
5. Pipeline runs from analysis through insight
6. Repeat until stakeholder is satisfied

This loop should feel like a conversation with a senior analyst who remembers everything.

---

## Anti-Patterns to Avoid

1. **Don't hallucinate data**: Every number in the insight must come from actual E2B output
2. **Don't remove outliers silently**: Flag them, let the user decide
3. **Don't use a single imputation strategy for all columns**: Each column is different
4. **Don't generate more than 5 charts**: Information overload defeats the purpose
5. **Don't restart the full pipeline on follow-ups**: Use `resume_from` to skip unchanged stages
6. **Don't swallow errors silently**: Every failure must produce a user-readable message
7. **Don't store DataFrames in the database**: They're transient, re-loaded from the connector on resume
