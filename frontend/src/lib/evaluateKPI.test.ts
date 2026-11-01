import { test } from "node:test";
import assert from "node:assert/strict";
import { evaluateKPI } from "./evaluateKPI.ts";

// Phase 5.7: a domain-pack tile (net sales, AOV) is never recomputed in the browser, and an
// unknown kind from a newer backend never crashes the strip (it read `.ok` of undefined).
test("a pack KPI and an unknown kind return a result, never undefined", () => {
  assert.deepEqual(evaluateKPI({ kind: "pack", kpi: "aov" }, [{ a: 1 }]), { value: "—", ok: false });
  const unknown = { kind: "from_the_future" } as unknown as Parameters<typeof evaluateKPI>[0];
  assert.deepEqual(evaluateKPI(unknown, [{ a: 1 }]), { value: "—", ok: false });
});
