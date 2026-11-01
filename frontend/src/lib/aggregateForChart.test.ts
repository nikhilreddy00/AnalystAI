// Diff review (5.7): only a row-count chart is recounted under filters; a sum or KPI chart
// stays as the backend computed it (pill), and a scatter still shows the filtered rows.
import assert from "node:assert/strict";
import { test } from "node:test";
import { aggregateForChart } from "./aggregateForChart.ts";

const rows = [{ country: "DE", x: 1, y: 2 }, { country: "DE", x: 3, y: 4 }, { country: "FR", x: 5, y: 6 }];
const chart = (chart_type: string, aggregation: string) =>
  ({ chart_type, x_key: chart_type === "ScatterChart" ? "x" : "country", y_keys: ["v"], data: [{ country: "DE", v: 99 }],
     rationale: { aggregation } }) as never;

test("a count chart is recounted from the filtered rows", () => {
  assert.equal(aggregateForChart(rows, chart("BarChart", "count")).mode, "count");
});

test("a sum or KPI chart is never shown as row counts", () => {
  assert.equal(aggregateForChart(rows, chart("BarChart", "sum")).mode, "unfiltered");
  assert.equal(aggregateForChart(rows, chart("BarChart", "none")).mode, "unfiltered");
});

test("a scatter passes the filtered rows through, whatever its aggregation", () => {
  const got = aggregateForChart(rows, chart("ScatterChart", "none"));
  assert.equal(got.mode, "passthrough");
  assert.equal(got.data.length, 3);
});
