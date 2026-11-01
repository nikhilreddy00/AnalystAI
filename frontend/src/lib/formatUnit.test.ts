// `node --test src/lib/formatUnit.test.ts` (Node runs TypeScript): no framework needed.
import assert from "node:assert/strict";
import { test } from "node:test";
import { formatMoney, formatWithUnit, isCurrency } from "./formatUnit.ts";

test("an ISO currency is money, anything else a suffix", () => {
  assert.equal(formatWithUnit(1234.5, "EUR"), "€1,234.50");
  assert.equal(formatWithUnit(70.617, "°C"), "70.62 °C");
  assert.equal(formatWithUnit(3, undefined), "3");
  assert.equal(isCurrency("days"), false);
});

test("a malformed or unknown code never throws", () => {
  assert.match(formatMoney(5, "ZZZ"), /^ZZZ\s5\.00$/);   // a well-formed unknown code: Intl prints it
  assert.doesNotThrow(() => formatMoney(5, "EURO"));
  assert.match(formatMoney(5, "EURO"), /5/);
});
