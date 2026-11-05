"""What the live stack has spent on model calls since a time: the `usage` events of every run
(browser and ask.py alike), summed per run. Retro Domain 5: the browser check ran 65% over its
estimate and only a hand-written psql query showed it; check the stop rule with this instead.

    uv run python scripts/live_check/spend.py [--since "2026-11-05 13:00"] [--budget 0.35]
--since defaults to the last 3 hours. --budget: exit 1 when the total passes it."""
import argparse
import json
import os
import subprocess
import sys

DB = os.environ.get("LIVE_PG", "postgresql://postgres@localhost:54329/analystai_live")


def spend(since: str) -> dict[str, float]:
    sql = ("select run_id, payload from run_events where type = 'usage' and created_at >= "
           f"{since} order by created_at")
    out = subprocess.run(["psql", DB, "-AtF", "\t", "-c", sql], capture_output=True, text=True, check=True)
    runs: dict[str, float] = {}
    for line in out.stdout.splitlines():
        run, payload = line.split("\t", 1)
        runs[run] = runs.get(run, 0.0) + sum(c.get("cost_usd", 0) for c in json.loads(payload)["calls"])
    return runs


def main() -> None:
    a = argparse.ArgumentParser()
    a.add_argument("--since", help="a timestamp; default: the last 3 hours")
    a.add_argument("--budget", type=float, help="exit 1 when the total passes it")
    args = a.parse_args()
    from datetime import datetime
    # a real timestamp only (it goes into the SQL text)
    since = f"'{datetime.fromisoformat(args.since).isoformat()}'::timestamptz" if args.since \
        else "now() - interval '3 hours'"
    runs = spend(since)
    for run, usd in runs.items():
        print(f"{run}  ${usd:.3f}")
    total = sum(runs.values())
    print(f"{len(runs)} runs  total ${total:.3f}" + (f" of ${args.budget:.2f}" if args.budget else ""))
    if args.budget is not None and total > args.budget:
        sys.exit(1)


if __name__ == "__main__":
    main()
