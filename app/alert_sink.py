"""Tiny Alertmanager webhook receiver for demos: logs every notification, GET /alerts lists them."""
import json
import threading
from collections import deque
from http.server import BaseHTTPRequestHandler, HTTPServer

RECEIVED = deque(maxlen=100)
LOCK = threading.Lock()


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802
        body = self.rfile.read(int(self.headers.get("Content-Length", 0) or 0))
        try:
            payload = json.loads(body)
        except ValueError:
            self.send_response(400)
            self.end_headers()
            return
        for al in payload.get("alerts", []):
            lb = al.get("labels", {})
            entry = {
                "receiver": self.path.strip("/") or "default",
                "status": al.get("status"),
                "alertname": lb.get("alertname"),
                "severity": lb.get("severity"),
                "slo": lb.get("slo"),
                "startsAt": al.get("startsAt"),
            }
            with LOCK:
                RECEIVED.append(entry)
            print("NOTIFY", json.dumps(entry), flush=True)
        self.send_response(200)
        self.end_headers()

    def do_GET(self):  # noqa: N802
        with LOCK:
            data = json.dumps(list(RECEIVED)).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


def serve(port=8080):
    HTTPServer(("0.0.0.0", port), Handler).serve_forever()


if __name__ == "__main__":
    serve()
