"""A small example agent with tools. Stdlib only.

LLM  -> LLM_BASE_URL   (OpenAI-compatible, the platform LLM gateway)
Tools-> TOOLS_URL      (MCP streamable HTTP, the platform tool gateway)
The app never holds a model key or a tool credential of its own.
"""
import json
import os
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

BASE = os.environ["LLM_BASE_URL"].rstrip("/")
MODEL = os.environ.get("LLM_MODEL", "default-chat")
KEY = os.environ.get("LLM_API_KEY", "")
SYSTEM = os.environ.get("AGENT_SYSTEM_PROMPT", "You are helpful.")
TOOLS_URL = os.environ.get("TOOLS_URL", "")
TOOLS_TOKEN = os.environ.get("TOOLS_TOKEN", "")
MAX_STEPS = 4


def http_json(url, body, headers, timeout=120):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw, hdrs = r.read().decode(), r.headers
    except urllib.error.HTTPError as e:
        return e.code, {"error": e.read().decode()[:400]}, {}
    if raw.startswith("event:") or raw.startswith("data:"):  # SSE framed
        raw = "".join(l[5:].strip() for l in raw.splitlines() if l.startswith("data:"))
    return 200, (json.loads(raw) if raw else {}), hdrs


class MCP:
    """Minimal MCP streamable-HTTP client. Enough for tools/list and tools/call."""

    def __init__(self):
        self.session, self.n = None, 0

    def _hdr(self):
        h = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream",
             "MCP-Protocol-Version": "2025-06-18", "Authorization": f"Bearer {TOOLS_TOKEN}"}
        if self.session:
            h["Mcp-Session-Id"] = self.session
        return h

    def rpc(self, method, params=None):
        self.n += 1
        body = {"jsonrpc": "2.0", "id": self.n, "method": method, "params": params or {}}
        code, res, hdrs = http_json(TOOLS_URL, body, self._hdr(), timeout=60)
        if code != 200:
            raise RuntimeError(f"mcp {method} -> HTTP {code}: {res.get('error')}")
        if hdrs.get("Mcp-Session-Id"):
            self.session = hdrs["Mcp-Session-Id"]
        if "error" in res:
            raise RuntimeError(f"mcp {method} -> {res['error']}")
        return res.get("result", {})

    def start(self):
        self.rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                "clientInfo": {"name": "vibe-app", "version": "0.2.0"}})
        req = urllib.request.Request(TOOLS_URL, data=json.dumps(
            {"jsonrpc": "2.0", "method": "notifications/initialized"}).encode(), headers=self._hdr())
        try:
            urllib.request.urlopen(req, timeout=10).read()
        except urllib.error.HTTPError:
            pass
        return self

    def tools_openai(self):
        return [{"type": "function", "function": {"name": t["name"], "description": t.get("description", "")[:300],
                 "parameters": t.get("inputSchema", {"type": "object", "properties": {}})}}
                for t in self.rpc("tools/list").get("tools", [])]

    def call(self, name, args):
        r = self.rpc("tools/call", {"name": name, "arguments": args})
        return "\n".join(c.get("text", "") for c in r.get("content", []))[:4000]


def llm(messages, tools):
    body = {"model": MODEL, "messages": messages}
    if tools:
        body["tools"] = tools
    code, res, _ = http_json(f"{BASE}/chat/completions", body,
                             {"Content-Type": "application/json", "Authorization": f"Bearer {KEY}"})
    if code != 200:
        raise RuntimeError(f"llm -> HTTP {code}: {res.get('error')}")
    return res["choices"][0]["message"]


def agent(message):
    trace, tools, mcp = [], [], None
    if TOOLS_URL:
        try:
            mcp = MCP().start()
            tools = mcp.tools_openai()
            trace.append(f"tools visible: {[t['function']['name'] for t in tools]}")
        except Exception as e:  # tools are optional; the gateway decides
            trace.append(f"tools unavailable: {e}")
    msgs = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": message}]
    for _ in range(MAX_STEPS):
        m = llm(msgs, tools)
        calls = m.get("tool_calls") or []
        if not calls:
            return m.get("content", ""), trace
        msgs.append(m)
        for c in calls:
            name, args = c["function"]["name"], json.loads(c["function"].get("arguments") or "{}")
            try:
                out = mcp.call(name, args)
                trace.append(f"tool {name}({args}) -> ok {len(out)} chars")
            except Exception as e:
                out = f"tool error: {e}"
                trace.append(f"tool {name}({args}) -> {e}")
            msgs.append({"role": "tool", "tool_call_id": c["id"], "content": out})
    return "(stopped after max tool steps)", trace


class H(BaseHTTPRequestHandler):
    def _send(self, code, obj):
        data = json.dumps(obj).encode()
        self.send_response(code); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)

    def do_GET(self):
        self._send(200, {"ok": True, "model": MODEL, "gateway": BASE, "tools": bool(TOOLS_URL)})

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        msg = json.loads(self.rfile.read(n) or b"{}").get("message", "hi")
        try:
            reply, trace = agent(msg)
            self._send(200, {"reply": reply, "trace": trace})
        except Exception as e:
            self._send(502, {"error": str(e)[:500]})


HTTPServer(("", int(os.environ.get("PORT", 8080))), H).serve_forever()
