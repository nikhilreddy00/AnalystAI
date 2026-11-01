// `node --experimental-strip-types --test src/lib/*.test.ts`: no framework needed.
// The strings below are the backend's (agents/visualization_agent.py _format_value, pinned in
// tests/test_semantic_hints.py): a dashboard tile recomputed here must read exactly the same.
import assert from "node:assert/strict";
import { test } from "node:test";
import { evaluateKPI } from "./evaluateKPI.ts";
import { formatMoney, formatWithUnit, isCurrency } from "./formatUnit.ts";

const NB = " ";

test("money and units read as the backend writes them", () => {
  assert.equal(formatWithUnit(1234.5, "EUR"), "€1,234.50");
  assert.equal(formatWithUnit(1234.5, "JPY"), "¥1,235");
  assert.equal(formatWithUnit(1234.5, "CHF"), `CHF${NB}1,234.50`);
  assert.equal(formatWithUnit(1234.5, "CAD"), "CA$1,234.50");
  assert.equal(formatWithUnit(-3, "GBP"), "-£3.00");
  assert.equal(formatWithUnit(70.617, "°C"), `70.62${NB}°C`);
  assert.equal(formatWithUnit(2.675, null), "2.68");
});

test("an unknown code is a unit, and nothing throws or prints NaN", () => {
  assert.equal(isCurrency("RPM"), false);
  assert.equal(formatWithUnit(5, "RPM"), `5.00${NB}RPM`);
  assert.equal(formatMoney(5, ""), "$5.00");
  assert.equal(formatMoney(5, null), "$5.00");
  assert.equal(formatWithUnit(Number.NaN, "USD"), "—");
  assert.equal(formatWithUnit(Number.POSITIVE_INFINITY, "°C"), "—");
});

test("a filtered KPI tile keeps its currency and unit", () => {
  const rows = [{ fee: 10 }, { fee: 20 }];
  assert.equal(evaluateKPI({ kind: "metric_avg", column: "fee", format_kind: "currency",
                             currency: "EUR" }, rows).value, "€15.00");
  assert.equal(evaluateKPI({ kind: "metric_avg", column: "fee", format_kind: "number",
                             unit: "°C" }, rows).value, `15.00${NB}°C`);
  assert.equal(evaluateKPI({ kind: "metric_avg", column: "fee", format_kind: "currency" },
                           rows).value, "$15.00");   // a report from before Phase 5.5
});
