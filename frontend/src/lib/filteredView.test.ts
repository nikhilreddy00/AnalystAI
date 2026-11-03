// User report (2026-11-03): filtering a dashboard changed nothing. A KPI or sum chart and a
// domain-pack tile now show the server's recomputation for the filters.
import assert from "node:assert/strict";
import { test } from "node:test";
import { chartView, tileView } from "./filteredView.ts";

const kpiChart = { id: "c1", chart_type: "BarChart", x_key: "dept", y_keys: ["rate"],
  data: [{ dept: "Cardiology", rate: 14.3 }, { dept: "Neurology", rate: 7.0 }],
  rationale: { aggregation: "none" } } as never;
const server = { charts: { c1: [{ dept: "Cardiology", rate: 14.3 }] }, tiles: { Discharges: "1,083" }, rows: 1297 };

test("a KPI chart shows the server's filtered data", () => {
  const v = chartView(kpiChart, true, server, []);
  assert.deepEqual(v.data, [{ dept: "Cardiology", rate: 14.3 }]);
  assert.equal(v.unfiltered, false);
});

test("a chart the server couldn't rebuild says it is unfiltered", () => {
  const v = chartView({ ...(kpiChart as object), id: "c9" } as never, true, server, []);
  assert.equal(v.unfiltered, true);
});

test("no filters: the chart as built", () => {
  assert.deepEqual(chartView(kpiChart, false, server, []).data, (kpiChart as { data: unknown }).data);
});

test("a pack tile shows the server's value under filters", () => {
  assert.equal(tileView("Discharges", true, server), "1,083");
  assert.equal(tileView("Discharges", false, server), null);
});
