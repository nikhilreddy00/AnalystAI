"""Ask the live stack a question about a file, as the browser does: upload, POST /chat, stream
the run's events to `done`. Prints each agent step, the tiles, the review flags and the cost;
writes every event to .live/<name>.jsonl. Waits on events, never on sleeps.

    uv run python scripts/live_check/ask.py <file|-> "<question>" [--session <id>]
`-` asks a follow-up on --session's data."""
import argparse
import json
import pathlib

import httpx

from scripts.live_check.session_token import token

API = "http://127.0.0.1:8000"


def main() -> None:
    a = argparse.ArgumentParser()
    a.add_argument("file")
    a.add_argument("question")
    a.add_argument("--session")
    args = a.parse_args()
    h = {"Authorization": f"Bearer {token()}"}
    with httpx.Client(timeout=60) as c:
        body = {"message": args.question, "session_id": args.session}
        if args.file != "-":
            with open(args.file, "rb") as f:
                up = c.post(f"{API}/connectors/upload", headers=h, files={"file": f}).raise_for_status().json()
            body["dataset_version_id"] = up.get("dataset_version_id")
        run = c.post(f"{API}/chat", headers=h, json=body).raise_for_status().json()
        events = []
        with c.stream("GET", f"{API}/runs/{run['run_id']}/events", headers=h, timeout=900) as r:
            for line in r.iter_lines():
                if not line.startswith("data:"):
                    continue
                e = json.loads(line[5:])
                events.append(e)
                if e["type"] in ("agent_complete", "error", "approval_required"):
                    print(e["type"], e.get("agent", ""), str(e.get("message", ""))[:140])
                if e["type"] in ("done", "error", "approval_required"):
                    break
    name = pathlib.Path(args.file).stem if args.file != "-" else "followup"
    pathlib.Path(".live").mkdir(exist_ok=True)
    pathlib.Path(f".live/{name}.jsonl").write_text("\n".join(json.dumps(e) for e in events))
    for e in events:
        if e["type"] == "insight":
            for k in e["data"].get("kpis") or []:
                print("tile:", k.get("label"), k.get("value"))
            print("headline:", (e["data"].get("executive_view") or {}).get("headline"))
        if e["type"] == "expert_review":
            for f in e["data"].get("flags", []):
                print("flag:", f.get("label"), f.get("reason", "")[:120])
    cost = sum(x.get("cost_usd", 0) for e in events if e["type"] == "usage" for x in e.get("calls", []))
    print(f"session {run['session_id']}  events {len(events)}  cost ${cost:.3f}")


if __name__ == "__main__":
    main()
