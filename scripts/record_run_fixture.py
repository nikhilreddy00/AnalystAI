"""Record one E2E case's model replies and sandbox outputs as a replay fixture, so the durable-run
tests (tests/test_durable_runs.py) replay a real overview run offline at $0.

    PYTHONPATH=. uv run python scripts/record_run_fixture.py e2e-01 tests/fixtures/runs/e2e-01.json

Real API + E2B spend (about $0.11 for e2e-01). The data is synthetic (evals/datagen.py).
"""
import asyncio
import dataclasses
import json
import sys

from agents import analysis_agent
from evals.run import load_cases, run_e2e_case
from llm.client import ClaudeClient, set_client


class Recorder:
    def __init__(self, inner):
        self.inner, self.replies = inner, []

    async def complete(self, **kw):
        result = await self.inner.complete(**kw)
        self.replies.append(dataclasses.asdict(result))
        return result


async def main(case_id: str, out: str) -> None:
    llm, outputs = Recorder(ClaudeClient()), []
    run_code = analysis_agent.run_code

    async def recorded_run_code(code, df_parquet=None):
        result = await run_code(code, df_parquet)
        outputs.append(dataclasses.asdict(result))
        return result

    analysis_agent.run_code = recorded_run_code
    set_client(llm)
    result = await run_e2e_case(load_cases("e2e", case_id)[0])
    cost = sum(r["cost_usd"] for r in llm.replies)
    with open(out, "w") as f:
        json.dump({"case": case_id, "replies": llm.replies, "sandbox": outputs,
                   "approvals": result["approvals"], "error": result["error"],
                   "cost_usd": round(cost, 4)}, f, indent=1)
    print(f"{case_id}: {len(llm.replies)} replies, {len(outputs)} sandbox runs, ${cost:.4f}, "
          f"approvals={result['approvals']}, error={result['error']}, "
          f"checks={[c['ok'] for c in result['checks']]}, hard={[h['ok'] for h in result['hard']]}")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1], sys.argv[2]))
