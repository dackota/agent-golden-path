"""MCP server that exposes the Orders API as tools. Streamable HTTP, stdlib only.

This is the pattern for "wrap an internal API as agent tools": one tool per
API call, a JSON schema per tool, and the HTTP call inside. kagent deploys it
with an MCPServer resource. agentgateway and kagent both talk to it over /mcp.
"""
import json
import os
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

API = os.environ.get("ORDERS_API_URL", "http://orders-api.demo-apis.svc.cluster.local:8000").rstrip("/")

TOOLS = [
    {"name": "list_orders", "description": "List orders. Optional filters: customer name, status (processing, shipped, delivered, cancelled).",
     "inputSchema": {"type": "object", "properties": {"customer": {"type": "string"}, "status": {"type": "string"}}}},
    {"name": "get_order", "description": "Get one order by id, e.g. 1002.",
     "inputSchema": {"type": "object", "properties": {"order_id": {"type": "string"}}, "required": ["order_id"]}},
    {"name": "cancel_order", "description": "Cancel an order by id. Only orders still processing can be cancelled. This changes data.",
     "inputSchema": {"type": "object", "properties": {"order_id": {"type": "string"}}, "required": ["order_id"]}},
]


def api(method, path):
    req = urllib.request.Request(f"{API}{path}", method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def call_tool(name, args):
    if name == "list_orders":
        q = "&".join(f"{k}={v}" for k, v in args.items() if v)
        return api("GET", f"/orders{'?' + q if q else ''}")
    if name == "get_order":
        return api("GET", f"/orders/{args['order_id']}")
    if name == "cancel_order":
        return api("POST", f"/orders/{args['order_id']}/cancel")
    return 404, json.dumps({"error": f"unknown tool {name}"})


def handle(req):
    m, i, p = req.get("method"), req.get("id"), req.get("params") or {}
    if m == "initialize":
        return {"jsonrpc": "2.0", "id": i, "result": {"protocolVersion": p.get("protocolVersion", "2025-06-18"),
                "capabilities": {"tools": {}}, "serverInfo": {"name": "orders-mcp", "version": "0.1.0"}}}
    if m == "ping":
        return {"jsonrpc": "2.0", "id": i, "result": {}}
    if m == "tools/list":
        return {"jsonrpc": "2.0", "id": i, "result": {"tools": TOOLS}}
    if m == "tools/call":
        code, body = call_tool(p.get("name"), p.get("arguments") or {})
        return {"jsonrpc": "2.0", "id": i, "result": {"content": [{"type": "text", "text": body}], "isError": code >= 400}}
    if i is None:  # notification
        return None
    return {"jsonrpc": "2.0", "id": i, "error": {"code": -32601, "message": f"method not found: {m}"}}


class H(BaseHTTPRequestHandler):
    def _send(self, code, obj=None):
        data = json.dumps(obj).encode() if obj is not None else b""
        self.send_response(code)
        if data:
            self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)

    def do_GET(self):
        if self.path == "/healthz":
            return self._send(200, {"ok": True})
        self._send(405, {"error": "SSE stream not supported; POST JSON-RPC to /mcp"})

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        try:
            req = json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            return self._send(400, {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}})
        res = handle(req)
        self._send(202) if res is None else self._send(200, res)

    def log_message(self, fmt, *args):
        pass


HTTPServer(("", int(os.environ.get("PORT", 8080))), H).serve_forever()
