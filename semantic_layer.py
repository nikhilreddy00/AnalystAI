"""
Semantic layer (ADR-0003): one YAML file per data source, describing its tables, their grain,
join cardinalities and metric definitions. The SQL agent reads it so a metric means the same
thing in every query, and the fan-out check (guardrails.sql_fanout) reads the grain and
cardinalities. An invalid file is an error at load time, never a silent half-layer.
"""
from __future__ import annotations

from functools import cache, cached_property
from pathlib import Path
from typing import Literal

import sqlglot
import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlglot import exp

from guardrails import check_sql_readonly


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class Column(_Strict):
    name: str
    type: Literal["id", "dimension", "fact", "time"]
    description: str = ""
    synonyms: list[str] = Field(default_factory=list)
    sample_values: list[str | int | float | bool] = Field(default_factory=list)
    unit: str | None = None           # ISO currency (USD, EUR) or a unit (°C, days, kg)


class Table(_Strict):
    name: str
    source: str                       # physical `schema.table`
    primary_key: list[str]            # the grain; YAML may give a single name
    description: str = ""
    columns: list[Column]

    @field_validator("primary_key", mode="before")
    @classmethod
    def _one_or_many(cls, v):
        return [v] if isinstance(v, str) else v

    def column(self, name: str) -> Column | None:
        return next((c for c in self.columns if c.name == name), None)


class NonAdditive(_Strict):
    """A semi-additive metric (a balance): sum it across other dimensions, but over `name`
    take only the `window` row of the period — never a sum across dates."""
    name: str
    window: Literal["last", "first"]


class Metric(_Strict):
    name: str
    table: str                        # the metric's grain
    joins: list[str] = Field(default_factory=list)
    expr: str
    description: str = ""
    synonyms: list[str] = Field(default_factory=list)
    non_additive_dimension: NonAdditive | None = None

    @cached_property
    def parsed(self) -> exp.Expression:
        return sqlglot.parse_one(self.expr, read="postgres")

    def terms(self) -> list[str]:
        return [self.name.replace("_", " "), *self.synonyms]


class Relationship(_Strict):
    from_: str = Field(alias="from")  # `table.column` on the many side
    to: str                           # `table.column` on the one side
    cardinality: Literal["many_to_one", "one_to_one"]


class VerifiedQuery(_Strict):
    question: str
    sql: str


class SemanticLayer(_Strict):
    dialect: str = "postgres"
    tables: list[Table]
    metrics: list[Metric] = Field(default_factory=list)
    relationships: list[Relationship] = Field(default_factory=list)
    verified_queries: list[VerifiedQuery] = Field(default_factory=list)

    def table(self, name: str) -> Table | None:
        return next((t for t in self.tables if t.name == name), None)

    def metric(self, name: str) -> Metric | None:
        return next((m for m in self.metrics if m.name == name), None)

    @model_validator(mode="after")
    def _check(self) -> SemanticLayer:
        errors = [*_duplicates("table", [t.name for t in self.tables]),
                  *_duplicates("metric name or synonym",
                               [s.lower() for m in self.metrics for s in m.terms()])]
        for t in self.tables:
            errors += [f"table {t.name}: primary key {k!r} is not a column"
                       for k in t.primary_key if not t.column(k)]
        for r in self.relationships:
            errors += [f"relationship {end}: unknown table.column"
                       for end in (r.from_, r.to) if not self._resolves(end)]
        for m in self.metrics:
            errors += self._metric_errors(m)
        for q in self.verified_queries:
            if reason := check_sql_readonly(q.sql, dialect=self.dialect):
                errors.append(f"verified query {q.question!r}: {reason}")
        if errors:
            raise ValueError("invalid semantic layer:\n- " + "\n- ".join(errors))
        return self

    def _resolves(self, ref: str) -> bool:
        table, _, col = ref.partition(".")
        t = self.table(table)
        return bool(t and t.column(col))

    def _metric_errors(self, m: Metric) -> list[str]:
        scope = [m.table, *m.joins]
        errors = [f"metric {m.name}: unknown table {t}" for t in scope if not self.table(t)]
        try:
            parsed = m.parsed
        except sqlglot.errors.ParseError as exc:
            return errors + [f"metric {m.name}: expr does not parse: {str(exc).splitlines()[0]}"]
        for c in parsed.find_all(exp.Column):
            if c.table not in scope:
                errors.append(f"metric {m.name}: column {c.sql()} must be qualified with one of "
                              f"{scope}")
            elif not self._resolves(f"{c.table}.{c.name}"):
                errors.append(f"metric {m.name}: unknown column {c.sql()}")
        nad = m.non_additive_dimension
        if nad and not any((t := self.table(s)) and (c := t.column(nad.name)) and c.type == "time"
                           for s in scope):
            errors.append(f"metric {m.name}: non_additive_dimension {nad.name} is not a time "
                          "column of its tables")
        elif nad and (t := self.table(m.table)) and not [k for k in t.primary_key if k != nad.name]:
            errors.append(f"metric {m.name}: a level needs an entity: {m.table}'s primary key "
                          f"must name a column besides {nad.name} (e.g. [account_id, {nad.name}])")
        return errors


def _duplicates(what: str, names: list[str]) -> list[str]:
    seen, dup = set(), set()
    for n in names:
        (dup if n in seen else seen).add(n)
    return [f"duplicate {what}: {n}" for n in sorted(dup)]


def load(path: str | Path) -> SemanticLayer:
    return SemanticLayer.model_validate(yaml.safe_load(Path(path).read_text()))


# ── Flat files (Phase 5.5): a table whose columns an upload has gives its columns' meaning ────

SAMPLE_LAYERS = Path(__file__).parent / "semantic_layers"


@cache
def file_layers() -> tuple[SemanticLayer, ...]:
    """The sample layers shipped in semantic_layers/ and the configured one: loaded once, at
    boot by the API and the worker (an invalid file stops either from starting)."""
    paths = sorted(SAMPLE_LAYERS.glob("*.yaml"))
    layers = [load(p) for p in paths]
    configured = configured_layer()
    from api.config import settings   # a sample named as the configured layer counts once
    same = configured and any(Path(settings.semantic_layer_path).resolve() == p.resolve() for p in paths)
    return tuple(layers + ([configured] if configured and not same else []))


def column_hints(columns: list[str]) -> tuple[dict[str, dict], str | None]:
    """({column: {aggregation, by, time, unit}}, note) for an upload's columns. A layer table
    matches when the upload has all its columns; one match gives the hints, several give none
    (and a note: the meaning would be a guess). Aggregations come from the table's metrics:
    SUM(col) → sum, AVG(col) → mean, a non_additive_dimension → each entity's last value per
    period, then summed (a balance, MRR)."""
    have = set(columns)
    tables = [(layer, t) for layer in file_layers() for t in layer.tables
              if {c.name for c in t.columns} <= have]
    if len(tables) != 1:
        return {}, (f"Several semantic-layer tables fit these columns "
                    f"({', '.join(t.name for _, t in tables)}); none was applied."
                    if tables else None)
    layer, table = tables[0]
    hints: dict[str, dict] = {c.name: {"unit": c.unit} for c in table.columns if c.unit}
    for m in layer.metrics:
        if m.table != table.name or m.joins:
            continue
        node = m.parsed
        if not isinstance(node, (exp.Sum, exp.Avg)) or not isinstance(node.this, exp.Column):
            continue
        col = node.this.name
        h = hints.setdefault(col, {})
        nad = m.non_additive_dimension
        if nad:   # a level: its window's row per entity (the key minus the time) per period
            h.update(aggregation=nad.window, time=nad.name,
                     by=[k for k in table.primary_key if k != nad.name])
        else:
            h["aggregation"] = "sum" if isinstance(node, exp.Sum) else "mean"
    return hints, f"Column meanings from the semantic layer table '{table.name}'."


@cache
def configured_layer() -> SemanticLayer | None:
    """The layer at `settings.semantic_layer_path`, loaded once; None when none is set."""
    from api.config import settings
    return load(settings.semantic_layer_path) if settings.semantic_layer_path else None


def check_against_db(layer: SemanticLayer, engine) -> list[str]:
    """Tables/columns the layer names that the live database doesn't have (a stale layer
    makes the agent write SQL against columns that no longer exist)."""
    from sqlalchemy import inspect

    insp = inspect(engine)
    problems = []
    for t in layer.tables:
        schema, _, name = t.source.rpartition(".")
        if not insp.has_table(name, schema=schema or None):
            problems.append(f"{t.name}: table {t.source} not found")
            continue
        live = {c["name"] for c in insp.get_columns(name, schema=schema or None)}
        problems += [f"{t.name}: column {c.name} not in {t.source}"
                     for c in t.columns if c.name not in live]
    return problems
