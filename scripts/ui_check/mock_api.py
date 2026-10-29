"""Mock AnalystAI API + Supabase auth for UI screenshots (no keys, no API spend).

    python3 scripts/ui_check/mock_api.py scripts/ui_check/fixtures 8100    # API
    python3 scripts/ui_check/mock_api.py scripts/ui_check/fixtures 54321   # Supabase

/chat streams fixtures/<scenario>.sse, where fixtures/scenario holds one of
full | error | approval | slow | insight (slow = full, paused after the analysis step; insight =
the Phase 5 e2e-01 gate output: claims with evidence chips).
"""

import json
import sys
import time
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

D = sys.argv[1]
PORT = int(sys.argv[2])
SCEN = D + "/scenario"  # file holding full|error|approval|slow


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
            return self._json({"status": "ok"})
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
            s = open(SCEN).read().strip()
            self.send_response(200)
            self._cors()
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            body = open(f"{D}/{'full' if s == 'slow' else s}.sse").read().split("\n\n")
            for i, chunk in enumerate(body):
                if not chunk.strip():
                    continue
                if s == "slow" and i > 8:
                    time.sleep(600)
                self.wfile.write((chunk + "\n\n").encode())
                self.wfile.flush()
            return
        if p.startswith("/auth/v1/token"):
            return self._json({})
        return self._json({})

    def log_message(self, *a):
        pass


ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
