import { test } from "node:test";
import assert from "node:assert/strict";
import { formatDay, parseDay } from "./localDate.ts";

test("a picked day round-trips as the same calendar day in any timezone", () => {
  assert.equal(formatDay(parseDay("2026-03-01")), "2026-03-01");
  assert.equal(formatDay(new Date(2026, 0, 31)), "2026-01-31");   // local midnight, never UTC
  assert.equal(parseDay("2026-12-05").getDate(), 5);
});
