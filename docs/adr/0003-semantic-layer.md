# ADR-0003: A minimal, self-owned YAML semantic layer shaped like Cortex semantic views

- Status: Accepted (Phase 4, 2026-10-27) — built as `semantic_layer.py`; see the amendments below
- Gaps addressed: G10, and partly G2 and G6

## Context
- Adding a semantic model raised the accuracy of the same LLM from 57% to 78% on BIRD [P4].
- Genie relies on metric views, value dictionaries and commented example SQL [P6].
- Airbnb's Minerva exists because the same metric was being computed differently in different
  places [S3].
- The most common error in production is wrong filters and literals: 24% at LinkedIn [P2].
- Today's "semantic layer" is DDL fed to Vanna, and Vanna is configured with the Groq key, so it
  never works.

## Decision
- One file, `semantic_layer.yaml`, loaded at startup and validated with Pydantic. An invalid file
  stops the app from booting.
- Shape (a strict subset of Snowflake's semantic view concepts [P5], so users can recognize it and
  port it):

```yaml
tables:
  - name: orders
    source: public.orders            # physical table
    description: One row per order.
    columns:
      - {name: status, type: dimension, synonyms: [state], sample_values: [completed, refunded]}
      - {name: amount, type: fact, description: Gross order value in USD}
      - {name: created_at, type: time}
metrics:
  - name: net_revenue
    expr: SUM(amount) FILTER (WHERE status = 'completed')
    description: Revenue after refunds.
    synonyms: [revenue, sales]
relationships:
  - {from: orders.customer_id, to: customers.id, cardinality: many_to_one}   # catches fan-out
verified_queries:
  - {question: "Net revenue last month", sql: "..."}
```

- `sample_values` and `synonyms` target the filter-literal error (F2). `cardinality` lets the SQL
  guard detect fan-out joins.
- Retrieval is through the `get_metric_definition` and `get_schema` tools, not by dumping the whole
  file into the prompt.
- **Not adopted**:
  - dbt MetricFlow or Cube: they add a service or a dbt dependency many startups don't have. An
    importer can come later.
  - Vanna: dropped.

## Consequences
- The operator writes the YAML. A `scaffold` command that drafts it from the warehouse schema is a
  Phase 4 nice-to-have, not required.
- Verified queries are kept separate from the eval golden set ([ADR-0002](0002-eval-method.md)).

## Amendments (Phase 4)
- Per data source, not one global file: `SEMANTIC_LAYER_PATH` names the layer for the SQL
  connector; the eval ships two (`evals/sql/retail.yaml`, `evals/sql/banking.yaml`).
- Added fields: `primary_key` per table (the grain), `table` + `joins` per metric (the metric's
  grain and join path), `non_additive_dimension` for balance-like metrics (MetricFlow's name),
  `one_to_one` cardinality, and a top-level `dialect`.
- The fan-out guard is `guardrails.sql_fanout` (grain + cardinality); metric use is checked by
  `guardrails.sql_uses_metric` (AST after alias resolution, not string match).
- Layer text is operator configuration, so it goes into the prompt as trusted guidance; database
  introspection, query results and database errors are delimited as untrusted.
- The connection test reports tables/columns the layer names that the database lacks.

---
Source keys ([P1], [S6], …) refer to the Sources list in `docs/research/2026-10-27-data-agents.md`.
