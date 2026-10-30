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
            return self._json(
                [
                    {
                        "session_id": "ui-check",
                        "dataset_name": "sales_nov.csv",
                        "original_query": "Give me an overview of this sales data",
                        "row_count": 3000,
                        "column_count": 11,
                        "status": "completed",
                        "created_at": "2026-10-27T11:00:00Z",
                        "updated_at": "2026-10-27T12:00:00Z",
                    }
                ]
            )
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
            return self._json(
                {
                    "filename": "sales_nov.csv",
                    "status": "ok",
                    "file_path": "/tmp/x.csv",
                    "file_hash": "h",
                    "dataset_name": "sales_nov.csv",
                    "existing_session": None,
                    "connector_config": {"type": "csv", "file_path": "/tmp/x.csv"},
                }
            )
        if p == "/chat":
            with lock:
                state["posts"] += 1
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
