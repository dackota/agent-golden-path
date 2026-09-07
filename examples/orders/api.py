"""Fake internal Orders REST API. Stands in for any business API. Stdlib only.

Every route except /healthz needs the X-API-Key header. The agent never holds
that key. agentgateway does, and injects it on the way out.

GET  /orders?customer=&status=   list orders
GET  /orders/<id>                one order
POST /orders/<id>/cancel         cancel it
"""
import json
import os
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

ORDERS = {
    "1001": {"id": "1001", "customer": "acme", "status": "shipped", "total": 240.0, "items": ["beam-10ft", "bolt-kit"]},
    "1002": {"id": "1002", "customer": "acme", "status": "processing", "total": 1280.5, "items": ["column-20ft"]},
    "1003": {"id": "1003", "customer": "globex", "status": "delivered", "total": 75.0, "items": ["bolt-kit"]},
    "1004": {"id": "1004", "customer": "initech", "status": "processing", "total": 560.0, "items": ["plate-4x8", "weld-rod"]},
}


API_KEY = os.environ.get("ORDERS_API_KEY", "")


class H(BaseHTTPRequestHandler):
    def _authorized(self):
        if self.path.startswith("/healthz"):
            return True
        return bool(API_KEY) and self.headers.get("X-API-Key") == API_KEY

    def _send(self, code, obj):
        data = json.dumps(obj).encode()
        self.send_response(code); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)

    def do_GET(self):
        if not self._authorized():
            return self._send(401, {"error": "X-API-Key header missing or wrong"})
        u = urlparse(self.path)
        if u.path == "/healthz":
            return self._send(200, {"ok": True})
        if u.path == "/orders":
            q = {k: v[0] for k, v in parse_qs(u.query).items()}
            rows = [o for o in ORDERS.values()
                    if (not q.get("customer") or o["customer"] == q["customer"])
                    and (not q.get("status") or o["status"] == q["status"])]
            return self._send(200, {"orders": rows})
        if u.path.startswith("/orders/"):
            o = ORDERS.get(u.path.split("/")[2])
            return self._send(200, o) if o else self._send(404, {"error": "no such order"})
        self._send(404, {"error": "not found"})

    def do_POST(self):
        if not self._authorized():
            return self._send(401, {"error": "X-API-Key header missing or wrong"})
        parts = self.path.strip("/").split("/")
        if len(parts) == 3 and parts[0] == "orders" and parts[2] == "cancel":
            o = ORDERS.get(parts[1])
            if not o:
                return self._send(404, {"error": "no such order"})
            if o["status"] in ("shipped", "delivered"):
                return self._send(409, {"error": f"order {o['id']} is {o['status']} and cannot be cancelled"})
            o["status"] = "cancelled"
            return self._send(200, o)
        self._send(404, {"error": "not found"})

    def log_message(self, fmt, *args):
        print(f"orders-api {self.command} {self.path}")


HTTPServer(("", int(os.environ.get("PORT", 8000))), H).serve_forever()
