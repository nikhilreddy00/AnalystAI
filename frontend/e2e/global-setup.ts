// Every run starts from CI's state (retro 5.7b-3: CI's frontend job went red twice on a run that
// passed locally, where a git-ignored scenario file and a hand-built fixture hid both bugs). The
// one place the git-ignored fixtures are built, for CI, the pre-push hook and a local run alike.
import { execFileSync } from "node:child_process";
import { rmSync } from "node:fs";

const FIXTURES = "scripts/ui_check/fixtures";

export default function globalSetup() {
  rmSync(`../${FIXTURES}/scenario`, { force: true });
  for (const args of [[FIXTURES, "--dashboard-only"],
    [`${FIXTURES}/saas_mrr_snapshots`, "--dataset", "saas_mrr_snapshots"],
    [`${FIXTURES}/shift_production`, "--dataset", "shift_production"],
    [`${FIXTURES}/tms_shipments`, "--dataset", "tms_shipments"],
    [`${FIXTURES}/order_lines`, "--dataset", "order_lines"],   // logistics_chat.spec.ts
    [`${FIXTURES}/dock_appointments`, "--dataset", "dock_appointments"]]) {
    execFileSync("uv", ["run", "python", "scripts/ui_check/make_fixture.py", ...args],
      { cwd: "..", stdio: ["ignore", "ignore", "inherit"] });
  }
}
