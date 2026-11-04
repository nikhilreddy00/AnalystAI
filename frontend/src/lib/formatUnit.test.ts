// `node --experimental-strip-types --test src/lib/*.test.ts`: no framework needed.
// The strings below are the backend's (agents/visualization_agent.py _format_value, pinned in
// tests/test_semantic_hints.py): a unit shown here must read exactly as the backend writes it.
import assert from "node:assert/strict";
import { test } from "node:test";
import { columnUnit, formatMoney, formatWithUnit, isCurrency } from "./formatUnit.ts";

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

test("the Data tab follows the backend's units on new reports, the name rule only on old ones", () => {
  // audit pass 3: `sales` was "$" in the table but a plain number on the KPI tile
  assert.deepEqual(columnUnit("sales", { revenue: "USD" }), { currency: false, unit: undefined });
  assert.deepEqual(columnUnit("revenue", { revenue: "USD" }), { currency: false, unit: "USD" });
  assert.deepEqual(columnUnit("sales", undefined), { currency: true });
  assert.deepEqual(columnUnit("spending_score", undefined), { currency: false });
  assert.deepEqual(columnUnit("constructor", {}), { currency: false, unit: undefined });
});
