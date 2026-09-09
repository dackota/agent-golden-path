"""A container agent that audits Renovate pull requests.

Contract with the platform, all from the environment, none of it typed by a
developer:

  LLM_BASE_URL, LLM_API_KEY, LLM_MODEL   the model, through the LLM gateway
  GITHUB_TOKEN, GITHUB_REPOS             minted because values.yaml declared repos
  AGENT_SYSTEM_PROMPT                    the audit method, from values.yaml

An audit takes minutes, so a run is asynchronous:

  POST /audit  {"task": "..."}   -> 202 {"run_id": "..."}
  GET  /runs/<id>                -> state, log, answer
  GET  /healthz                  -> always answers, even mid-audit
"""
import json
import os
import re
import subprocess
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import agent
import shell

WORKSPACE = os.environ.get("WORKSPACE", "/data/work")
TOKEN = os.environ.get("GITHUB_TOKEN", "")
REPOS = [r.strip() for r in os.environ.get("GITHUB_REPOS", "").split(",") if r.strip()]
SYSTEM = os.environ.get("AGENT_SYSTEM_PROMPT", "You audit dependency pull requests.")
REPO_PATTERN = re.compile(r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$")

runs = {}
runs_lock = threading.Lock()


def repo_dir(repo):
    return os.path.join(WORKSPACE, repo.split("/")[1])


def clone(repo):
    """Clone once, then fetch. The token goes in the remote, never in a log line."""
    if not REPO_PATTERN.match(repo):
        raise ValueError(f"not a repository name: {repo!r}")
    target = repo_dir(repo)
    url = f"https://x-access-token:{TOKEN}@github.com/{repo}.git"
    if os.path.isdir(os.path.join(target, ".git")):
        cmd = ["git", "-C", target, "fetch", "--all", "--prune", "--quiet"]
    else:
        os.makedirs(WORKSPACE, exist_ok=True)
        cmd = ["git", "clone", "--quiet", url, target]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
    if p.returncode != 0:
        raise RuntimeError(f"git failed for {repo}: {p.stderr.strip()[:300].replace(TOKEN, '***')}")
    return target


def prepare():
    """Get every declared repository on disk. Returns the first one's path."""
    paths = [clone(r) for r in REPOS]
    if not paths:
        raise RuntimeError("no repositories declared. Set github.repos in values.yaml")
    return paths[0]


def start_run(task):
    run_id = str(uuid.uuid4())
    with runs_lock:
        runs[run_id] = {"state": "running", "log": [], "answer": None, "error": None}

    def log(line):
        with runs_lock:
            runs[run_id]["log"].append(line)

    def work():
        try:
            workspace = prepare()
            log(f"workspace ready: {workspace}")
            answer = agent.audit(SYSTEM, task, workspace, log)
            with runs_lock:
                runs[run_id].update(state="done", answer=answer)
        except Exception as e:                      # a run must never take the server down
            with runs_lock:
                runs[run_id].update(state="failed", error=str(e)[:600])

    threading.Thread(target=work, daemon=True).start()
    return run_id


class H(BaseHTTPRequestHandler):
    def _send(self, code, obj):
        data = json.dumps(obj).encode()
        self.send_response(code); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)

    def do_GET(self):
        if self.path in ("/healthz", "/"):
            return self._send(200, {"ok": True, "model": agent.MODEL, "repos": REPOS,
                                    "tools": sorted(shell.ALLOWED)})
        if self.path.startswith("/runs/"):
            with runs_lock:
                run = runs.get(self.path.removeprefix("/runs/"))
            return self._send(200, run) if run else self._send(404, {"error": "no such run"})
        self._send(404, {"error": "not found"})

    def do_POST(self):
        if self.path not in ("/audit", "/chat"):
            return self._send(404, {"error": "not found"})
        n = int(self.headers.get("Content-Length", 0))
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            return self._send(400, {"error": "body must be JSON"})
        task = body.get("task") or body.get("message")
        if not isinstance(task, str) or not task.strip():
            return self._send(400, {"error": "give a task, e.g. {\"task\": \"audit PR 1596\"}"})
        self._send(202, {"run_id": start_run(task.strip()), "poll": "/runs/<run_id>"})

    def log_message(self, fmt, *args):
        pass


if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("GITHUB_TOKEN is not set. Declare github.repos in values.yaml")
    ThreadingHTTPServer(("", int(os.environ.get("PORT", 8080))), H).serve_forever()
