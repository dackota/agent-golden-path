"""PROTOTYPE 'vibe-coded' agent. Stdlib only. Talks to whatever LLM_BASE_URL says."""
import json
import os
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

BASE = os.environ["LLM_BASE_URL"].rstrip("/")
MODEL = os.environ.get("LLM_MODEL", "default-chat")
KEY = os.environ.get("LLM_API_KEY", "")
SYSTEM = os.environ.get("AGENT_SYSTEM_PROMPT", "You are helpful.")


def ask(message):
    body = json.dumps({"model": MODEL, "messages": [
        {"role": "system", "content": SYSTEM}, {"role": "user", "content": message}]}).encode()
    req = urllib.request.Request(f"{BASE}/chat/completions", data=body, headers={
        "Content-Type": "application/json", "Authorization": f"Bearer {KEY}"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return 200, json.load(r)["choices"][0]["message"]["content"]
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:500]


class H(BaseHTTPRequestHandler):
    def _send(self, code, obj):
        data = json.dumps(obj).encode()
        self.send_response(code); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)

    def do_GET(self):
        self._send(200, {"ok": True, "model": MODEL, "gateway": BASE})

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        msg = json.loads(self.rfile.read(n) or b"{}").get("message", "hi")
        code, reply = ask(msg)
        self._send(200 if code == 200 else 502, {"upstream_status": code, "reply": reply})


HTTPServer(("", int(os.environ.get("PORT", 8080))), H).serve_forever()
