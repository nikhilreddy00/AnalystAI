"""Mock AnalystAI API + Supabase auth for UI screenshots (no keys, no API spend).

    python3 scripts/ui_check/mock_api.py scripts/ui_check/fixtures 8100    # API
    python3 scripts/ui_check/mock_api.py scripts/ui_check/fixtures 54321   # Supabase

POST /chat stores a run (Phase 5.6a) and GET /runs/run-1/events streams fixtures/<scenario>.sse
with SSE ids, honouring Last-Event-ID. fixtures/scenario holds one of
full | error | approval | slow | insight (slow = full, paused after the analysis step; insight =
the Phase 5 e2e-01 gate output: claims with evidence chips), or for the reconnect test
(frontend/e2e/reconnect.spec.ts): drop = full, the first connection cut after 8 events;
trickle = full, one event every 0.4 s. GET /__served lists every seq sent, per connection;
GET /__posts counts POST /chat calls since the mock started.

Phase 5.6b workspace (frontend/e2e/workspace.spec.ts): threads live in memory (list, PATCH,
DELETE, /thread built from the same SSE fixture the live run streams, so a reopened report must
render as the live one did), one dataset, and test hooks: POST /__reset, POST /__seed?n=200
(threads), POST /__stale (a newer dataset version).
"""

import json
import sys
import threading
import time
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

D = sys.argv[1]
PORT = int(sys.argv[2])
SCEN = D + "/scenario"  # file holding full|error|approval|slow|insight|drop|trickle
QUESTION = "Give me an overview of this sales data"
lock = threading.Lock()
state = {"connections": [], "served": set(), "posts": 0}   # seqs sent, per connection
REPORT = ("briefing", "evidence", "charts", "insight", "expert_review", "targeted_response")
DATASET = {"id": "ds-1", "name": "sales_nov", "kind": "upload", "source": "csv",
           "created_at": "2026-10-27T11:00:00Z", "analyses": 1}
VERSION = {"id": "v-1", "version": 1, "content_hash": "ab12cd34ef56" * 5 + "abcd", "rows": 3000,
           "columns": ["date", "region", "segment", "revenue"], "created_at": "2026-10-27T11:00:00Z"}


def thread(sid="ui-check", title="sales_nov · " + QUESTION, updated="2026-10-27T12:00:00Z",
           run_status="completed") -> dict:
    return {"session_id": sid, "title": title, "dataset_id": "ds-1", "dataset_name": "sales_nov",
            "questions": QUESTION, "pinned": False, "archived": False, "status": "completed",
            "run_status": run_status, "created_at": updated, "updated_at": updated}


def reset() -> None:
    state.update(threads={"ui-check": thread()}, stale=False, started=False)


def thread_status(sid: str) -> str:
    """The thread view's run: running only on the thread POST /chat started it in (the mock has
    one run, run-1, in ui-check), else the fixture's finished run."""
    with lock:
        started = state.get("started", False)
    events = run_events()
    if sid == "ui-check" and started and run_status() == "running":
        return "running"
    return "waiting_approval" if events[-1]["type"] == "approval_required" else "completed"


reset()


def scenario() -> str:
    return open(SCEN).read().strip()


def run_events() -> list[dict]:
    s = scenario()
    name = "full" if s in ("slow", "drop", "trickle") else s
    events = [json.loads(c[6:]) for c in open(f"{D}/{name}.sse").read().split("\n\n")
              if c.strip().startswith("data: ")]
    if events and events[0].get("type") == "session_id":
        events[0] = {**events[0], "message": QUESTION, "is_approval": False}
    return events


def run_status() -> str:
    events = run_events()
    with lock:
        if len(state["served"]) < len(events):
            return "running"
    return "waiting_approval" if events[-1]["type"] == "approval_required" else "completed"


class H(BaseHTTPRequestHandler):
    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", self.headers.get("Origin") or "*")
        self.send_header("Access-Control-Allow-Credentials", "true")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Access-Control-Allow-Methods", "*")

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def _json(self, obj, code=200):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self._cors()
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        p = self.path.split("?")[0]
        if p.startswith("/auth/v1/user"):
            return self._json(
                {
                    "id": "u1",
                    "email": "demo@example.com",
                    "user_metadata": {"full_name": "Demo User"},
                    "aud": "authenticated",
                }
            )
        if p == "/health":
            return self._json({"status": "ok", "persistent": True})
        if p == "/__served":
            with lock:
                return self._json(state["connections"])
        if p == "/__posts":
            with lock:
                return self._json(state["posts"])
        if p == "/runs/run-1":
            return self._json({"run_id": "run-1", "session_id": "ui-check",
                               "status": run_status(), "error": None})
        if p == "/runs/run-1/events":
            return self._stream()
        if p == "/api/sessions":
            with lock:
                items = sorted(state["threads"].values(), key=lambda t: t["updated_at"],
                               reverse=True)
            return self._json(items)
        if p.startswith("/api/sessions/") and p.endswith("/thread"):
            sid = p.split("/")[3]
            with lock:
                t = state["threads"].get(sid)
            if not t:
                return self._json({"detail": "Session not found"}, 404)
            events = run_events()
            # As memory/run_store.report_events: the report starts at the last gate answer.
            start = max((i for i, e in enumerate(events) if e.get("type") == "approval_answered"),
                        default=0)
            report = [e for e in events[start:] if e.get("type") in REPORT]
            return self._json({**t, "turns": [{
                "run_id": "run-1", "question": QUESTION, "status": thread_status(sid), "error": None,
                "last_seq": len(events),
                "report": report, "report_sha256": "f" * 64, "intact": True,
                "stale": state["stale"],
                "provenance": {"dataset_version_id": "v-1", "model": "claude-sonnet-4-6",
                               "fast_model": "claude-haiku-4-5", "cards": {}, "prompts": [],
                               "llm_calls": 7, "cost_usd": 0.1085, "input_tokens": 0,
                               "output_tokens": 0, "started_at": "2026-10-27T11:58:00Z",
                               "finished_at": "2026-10-27T12:00:00Z"}}]})
        if p == "/api/datasets":
            return self._json([{**DATASET, "latest": VERSION, "versions": [VERSION]}])
        if p.startswith("/api/datasets/"):
            with lock:
                threads = list(state["threads"].values())
            return self._json({**DATASET, "latest": VERSION, "versions": [VERSION],
                               "threads": threads, "charts": []})
        if p == "/dashboard/latest":
            return self._json(json.load(open(D + "/dash_latest.json")))
        if p == "/dashboard/latest/data":
            return self._json(json.load(open(D + "/dash_data.json")))
        if p.startswith("/dashboard"):
            return self._json([])
        return self._json({}, 200)

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(n)
        p = self.path.split("?")[0]
        if p == "/connectors/upload":
            return self._json({
                "status": "new_dataset", "filename": "sales_nov.csv",
                "dataset": {**DATASET, "analyses": 0, "latest": VERSION, "versions": [VERSION]},
                "dataset_id": "ds-1", "dataset_version_id": "v-1", "version": 1,
                "dataset_name": "sales_nov", "file_hash": VERSION["content_hash"]})
        if p == "/__reset":
            with lock:
                reset()
            return self._json({})
        if p == "/__stale":
            with lock:
                state["stale"] = True
            return self._json({})
        if p == "/__seed":
            n = int(self.path.split("n=")[1]) if "n=" in self.path else 200
            with lock:
                for i in range(n):
                    day = f"2026-{10 - i // 60:02d}-{28 - (i % 28):02d}T10:00:00Z"
                    state["threads"][f"t-{i}"] = thread(f"t-{i}", f"sales_nov · question {i}",
                                                        day, run_status=None)
            return self._json({})
        if p == "/chat":
            with lock:
                state["posts"] += 1
                state["started"] = True
                state["connections"].clear()
                state["served"].clear()
            return self._json({"run_id": "run-1", "session_id": "ui-check", "status": "queued",
                               "error": None})
        if p.startswith("/runs/run-1/"):
            return self._json({"run_id": "run-1", "session_id": "ui-check", "status": "queued",
                               "error": None})
        if p.startswith("/auth/v1/token"):
            return self._json({})
        return self._json({})

    def do_PATCH(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        sid = self.path.split("?")[0].split("/")[3]
        with lock:
            t = state["threads"].get(sid)
            if t:
                t.update(body)
        return self._json(t, 200) if t else self._json({"detail": "Session not found"}, 404)

    def do_DELETE(self):
        sid = self.path.split("?")[0].split("/")[3]
        with lock:
            gone = state["threads"].pop(sid, None)
        return self._json({"deleted": True}, 202) if gone else self._json({}, 404)

    def _stream(self):
        s, events = scenario(), run_events()
        last = self.headers.get("Last-Event-ID")
        after = int(last) if last and last.isdigit() else 0
        with lock:
            sent = []
            state["connections"].append(sent)
            first = len(state["connections"]) == 1
        self.send_response(200)
        self._cors()
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        for seq, event in enumerate(events, 1):
            if seq <= after:
                continue
            if s == "slow" and seq > 9:
                time.sleep(600)
            if s == "drop" and first and seq > 8:
                return   # the connection dies mid-run
            if s == "trickle":
                time.sleep(0.4)
            try:
                self.wfile.write(f"id: {seq}\ndata: {json.dumps(event)}\n\n".encode())
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                return   # the tab closed
            with lock:
                sent.append(seq)
                state["served"].add(seq)

    def log_message(self, *a):
        pass


ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
