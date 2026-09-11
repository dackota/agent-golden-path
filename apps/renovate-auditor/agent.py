"""The audit loop. One tool, many turns.

The model gets a shell in a cloned repository and is asked to work the audit
through. Everything it needs is a command: gh for the pull request, git for
the base, pnpm for the build, npm for a cross-version test.

Two limits keep a long loop inside the platform's guard rails. Tool output is
cut in shell.py, and the history is trimmed here, because the whole
conversation is re-sent on every turn and an agent key has a per-minute token
cap at the LLM gateway.
"""
import datetime
import json
import os
import re
import time
import urllib.error
import urllib.request

import otel
import shell

BASE = os.environ["LLM_BASE_URL"].rstrip("/")
MODEL = os.environ.get("LLM_MODEL", "default-chat")
KEY = os.environ.get("LLM_API_KEY", "")
MAX_STEPS = int(os.environ.get("MAX_STEPS", "60"))
HISTORY_BUDGET = int(os.environ.get("HISTORY_BUDGET_CHARS", "90000"))
ELIDED = "[earlier tool output removed to stay inside the token budget]"

# An agent key has a per-minute token cap at the LLM gateway. A long tool loop
# reaches it, and that is the guard rail working. The run waits rather than dies.
RESET_MARGIN = 2
DEFAULT_WAIT = 20
MAX_WAIT = 120
RATE_LIMIT_TRIES = 6
RESET_AT = re.compile(r"Limit resets at: (\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) UTC")

TOOLS = [{"type": "function", "function": {
    "name": "run",
    "description": ("Run one shell command in the repository working tree and return its exit "
                    "code and output. Output is cut at 4000 characters, so filter with grep or "
                    "head rather than printing whole files."),
    "parameters": {"type": "object", "required": ["command"],
                   "properties": {"command": {"type": "string"}}}}}]


def trim(messages):
    """Keep the newest tool output in full and elide the oldest.

    The system prompt and the task are never touched. Returns a new list; the
    caller's messages are left alone.
    """
    out = [dict(m) for m in messages]
    size = sum(len(json.dumps(m)) for m in out)
    for m in out:
        if size <= HISTORY_BUDGET:
            break
        if m.get("role") == "tool" and m.get("content") != ELIDED:
            size -= len(m["content"]) - len(ELIDED)
            m["content"] = ELIDED
    return out


def wait_seconds(error_body, now=None):
    """How long to hold off after a 429, from the reset time the gateway named."""
    found = RESET_AT.search(error_body or "")
    if not found:
        return DEFAULT_WAIT
    reset = datetime.datetime.strptime(found.group(1), "%Y-%m-%d %H:%M:%S").replace(
        tzinfo=datetime.timezone.utc)
    now = now or datetime.datetime.now(datetime.timezone.utc)
    return max(RESET_MARGIN, min(MAX_WAIT, int((reset - now).total_seconds()) + RESET_MARGIN))


def post(messages, span):
    """One call to the gateway. The traceparent lets the gateway's span join ours."""
    body = {"model": MODEL, "messages": trim(messages), "tools": TOOLS}
    req = urllib.request.Request(f"{BASE}/chat/completions", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json",
                                          "Authorization": f"Bearer {KEY}",
                                          "traceparent": span.traceparent()})
    with urllib.request.urlopen(req, timeout=300) as r:
        res, hdrs = json.load(r), r.headers
    usage, cost = res.get("usage") or {}, hdrs.get("x-litellm-response-cost")
    span.set({"gen_ai.response.model": res.get("model"),
              "gen_ai.usage.input_tokens": usage.get("prompt_tokens"),
              "gen_ai.usage.output_tokens": usage.get("completion_tokens"),
              "litellm.call_id": hdrs.get("x-litellm-call-id"),
              "litellm.response_cost": float(cost) if cost else None})
    return res


def llm(messages, parent, log=lambda _: None):
    for attempt in range(1, RATE_LIMIT_TRIES + 1):
        span = parent.child(f"chat {MODEL}").set({"gen_ai.operation.name": "chat",
                                                 "gen_ai.provider.name": "openai",
                                                 "gen_ai.request.model": MODEL})
        try:
            return post(messages, span)["choices"][0]["message"]
        except urllib.error.HTTPError as e:
            span.error(e)
            detail = e.read().decode()[:400]
            if e.code != 429 or attempt == RATE_LIMIT_TRIES:
                raise RuntimeError(f"LLM gateway {e.code}: {detail}") from e
            pause = wait_seconds(detail)
            log(f"token rate limit reached, waiting {pause}s (try {attempt})")
            time.sleep(pause)
        finally:
            span.finish()


def run_tool(call, workspace, parent):
    """One shell command as an execute_tool span. Returns the tool result dict."""
    command = json.loads(call["function"].get("arguments") or "{}").get("command", "")
    span = parent.child("execute_tool run", otel.INTERNAL)
    span.set({"gen_ai.operation.name": "execute_tool", "gen_ai.tool.name": "run",
              "gen_ai.tool.call.id": call["id"]})
    try:
        result = shell.run(command, cwd=workspace)
    except shell.Refused as e:
        result = {"exit_code": 126, "output": str(e)}
        span.error(e)
    span.set({"process.exit_code": result["exit_code"]}).finish()
    return command, result


def audit(system, task, workspace, log):
    """Work the task to an answer. `log` takes one line per step.

    The whole run is one trace. It is exported when the run ends, however it ends.
    """
    trace = otel.Trace()
    root = trace.span(f"invoke_agent {trace.service}", otel.SERVER)
    root.set({"gen_ai.operation.name": "invoke_agent", "gen_ai.agent.name": trace.service})
    try:
        return loop(system, task, workspace, log, root)
    except Exception as e:
        root.error(e)
        raise
    finally:
        root.finish()
        trace.export()


def loop(system, task, workspace, log, root):
    messages = [{"role": "system", "content": system}, {"role": "user", "content": task}]
    for step in range(1, MAX_STEPS + 1):
        message = llm(messages, root, log)
        calls = message.get("tool_calls") or []
        if not calls:
            log(f"step {step}: answered")
            return message.get("content") or "(the model returned nothing)"
        messages.append(message)
        for call in calls:
            command, result = run_tool(call, workspace, root)
            log(f"step {step}: {command[:120]} -> exit {result['exit_code']}")
            messages.append({"role": "tool", "tool_call_id": call["id"],
                             "content": json.dumps(result)})
    return f"(stopped after {MAX_STEPS} steps without an answer)"
