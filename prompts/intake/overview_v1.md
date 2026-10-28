
OVERRIDE — FIRST-RUN COMPREHENSIVE OVERVIEW MODE:
This is the user's first analysis on a freshly connected dataset. Regardless of
their specific wording, produce a COMPREHENSIVE overview plan:
- analysis_types MUST include at least: ["eda", "segmentation", "correlation", "anomaly"].
  Add "forecasting" only if date columns exist. Add "trend" if date columns exist.
- target_metrics: set to ALL metric/numeric columns from the schema (not just one).
- segments: set to the top 3 categorical columns from the schema.
- clarification_needed: MUST be false — do not gate the first run on clarification.
- resume_from: MUST be null — run the full pipeline.
- raw_query: prepend "[Dataset overview] " to the user's actual message.
- question_intent: MUST be "overview" (downstream router uses this to run the full pipeline).
- targeted_params: leave all fields null/[] — not used in overview mode.
If the user's wording suggests a specific focus (e.g. "show me churn"), you may
bias target_metrics toward those columns, but never DROP coverage below the
comprehensive baseline above.