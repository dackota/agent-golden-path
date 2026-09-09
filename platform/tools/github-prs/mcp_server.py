"""MCP server that exposes just enough of GitHub to survey Renovate pull requests.

The upstream GitHub MCP server returns whole pull request objects. A Renovate
body carries the full release notes, so ten of them measure about 120 kB. That
one tool result is larger than a whole agent key's per-minute token budget, and
the gateway refuses the call. This server answers the same questions in about
two kilobytes by returning only the fields an audit reads.

Streamable HTTP, stdlib only, read-only by construction: it makes GET requests
and offers no tool that writes.
"""
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

API = os.environ.get("GITHUB_API", "https://api.github.com").rstrip("/")
TOKEN = os.environ.get("GITHUB_TOKEN", "")
BOT_MARK = "renovate"
MAX_PRS = 50
MAX_CHECKS = 40

TOOLS = [
    {"name": "list_renovate_prs",
     "description": "List the open Renovate pull requests in a repository. Returns number, title, branch and dates only.",
     "inputSchema": {"type": "object", "properties": {"owner": {"type": "string"}, "repo": {"type": "string"}},
                     "required": ["owner", "repo"]}},
    {"name": "pr_status",
     "description": "Merge state, how far the branch is behind its base, and every check that is not passing, for one pull request.",
     "inputSchema": {"type": "object", "properties": {"owner": {"type": "string"}, "repo": {"type": "string"},
                                                      "number": {"type": "integer"}},
                     "required": ["owner", "repo", "number"]}},
    {"name": "release_published_at",
     "description": "The publish date of a release tag, to work out whether a Renovate soak has elapsed.",
     "inputSchema": {"type": "object", "properties": {"owner": {"type": "string"}, "repo": {"type": "string"},
                                                      "tag": {"type": "string"}},
                     "required": ["owner", "repo", "tag"]}},
]


class GitHubError(Exception):
    """A GitHub response the caller must be told about rather than a crash."""


def get(path):
    req = urllib.request.Request(f"{API}{path}", headers={
        "Authorization": f"Bearer {TOKEN}", "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "goldenpath-github-prs/0.1.0"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        raise GitHubError(f"GitHub {e.code} on {path}") from e
    except urllib.error.URLError as e:
        raise GitHubError(f"cannot reach GitHub: {e.reason}") from e


def require(args, *names):
    missing = [n for n in names if not args.get(n)]
    if missing:
        raise GitHubError(f"missing argument: {', '.join(missing)}")
    return [args[n] for n in names]


def is_bot(pr):
    return BOT_MARK in (pr.get("user") or {}).get("login", "").lower()


def list_renovate_prs(args):
    owner, repo = require(args, "owner", "repo")
    prs = get(f"/repos/{owner}/{repo}/pulls?state=open&per_page=100&sort=created&direction=asc")
    return {"pull_requests": [
        {"number": p["number"], "title": p["title"], "branch": p["head"]["ref"],
         "base": p["base"]["ref"], "draft": p.get("draft", False),
         "created_at": p["created_at"], "updated_at": p["updated_at"]}
        for p in prs if is_bot(p)][:MAX_PRS]}


def pr_status(args):
    owner, repo, number = require(args, "owner", "repo", "number")
    pr = get(f"/repos/{owner}/{repo}/pulls/{number}")
    head = pr["head"]["sha"]
    cmp_ = get(f"/repos/{owner}/{repo}/compare/{pr['base']['ref']}...{head}")
    runs = get(f"/repos/{owner}/{repo}/commits/{head}/check-runs?per_page=100").get("check_runs", [])
    not_passing = [{"name": c["name"], "status": c["status"], "conclusion": c.get("conclusion")}
                   for c in runs
                   if c.get("conclusion") not in ("success", "neutral", "skipped")][:MAX_CHECKS]
    return {"number": pr["number"], "head_sha": head[:12],
            "mergeable": pr.get("mergeable"), "mergeable_state": pr.get("mergeable_state"),
            "commits_behind_base": cmp_.get("behind_by"),
            "checks_total": len(runs), "checks_not_passing": not_passing}


def release_published_at(args):
    owner, repo, tag = require(args, "owner", "repo", "tag")
    rel = get(f"/repos/{owner}/{repo}/releases/tags/{urllib.parse.quote(tag)}")
    return {"tag": tag, "published_at": rel.get("published_at")}


HANDLERS = {"list_renovate_prs": list_renovate_prs, "pr_status": pr_status,
            "release_published_at": release_published_at}


def call_tool(name, args):
    fn = HANDLERS.get(name)
    if fn is None:
        return True, {"error": f"unknown tool {name}"}
    try:
        return False, fn(args)
    except GitHubError as e:
        return True, {"error": str(e)}


def handle(req):
    m, i, p = req.get("method"), req.get("id"), req.get("params") or {}
    if m == "initialize":
        return {"jsonrpc": "2.0", "id": i, "result": {"protocolVersion": p.get("protocolVersion", "2025-06-18"),
                "capabilities": {"tools": {}}, "serverInfo": {"name": "github-prs-mcp", "version": "0.1.0"}}}
    if m == "ping":
        return {"jsonrpc": "2.0", "id": i, "result": {}}
    if m == "tools/list":
        return {"jsonrpc": "2.0", "id": i, "result": {"tools": TOOLS}}
    if m == "tools/call":
        failed, body = call_tool(p.get("name"), p.get("arguments") or {})
        return {"jsonrpc": "2.0", "id": i,
                "result": {"content": [{"type": "text", "text": json.dumps(body)}], "isError": failed}}
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


if not TOKEN:
    raise SystemExit("GITHUB_TOKEN is not set")
HTTPServer(("", int(os.environ.get("PORT", 8080))), H).serve_forever()
