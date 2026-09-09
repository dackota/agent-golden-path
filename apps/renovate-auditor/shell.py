"""Run a command the model proposed, inside the agent's workspace.

The real containment is the pod: non-root, read-only root filesystem, one
writable volume, and a network policy that allows TLS out and nothing else.
This module is the second line. It refuses a command whose first word is not
a tool the audit needs, so a wrong turn fails loudly instead of quietly doing
something else.
"""
import subprocess

ALLOWED = frozenset("""
awk basename cat cd cut date diff dirname echo find git gh grep head jq ls mkdir
node npm npx pnpm printf python3 rg sed sort tail tee test tr true uniq wc
""".split())

# Shapes no audit needs and that a slip could make expensive.
DESTRUCTIVE = (":(){", "mkfs", "dd if=", "/dev/sd", "curl ", "wget ", "chmod 777")

# This agent reads. It never changes the pull request, the branch, or the repo.
# A fix is a person's decision, so the audit reports and stops.
WRITES = ("git push", "git commit", "gh pr merge", "gh pr close", "gh pr edit",
          "gh pr comment", "gh pr review", "gh pr create", "gh release", "gh workflow run",
          "gh api -X", "gh api --method", "npm publish", "pnpm publish")

DENIED = DESTRUCTIVE + WRITES

SEPARATORS = ("&&", "||", ";", "|")
MAX_OUTPUT = 4000
DEFAULT_TIMEOUT = 900


class Refused(Exception):
    """The command was not run. The message says why, and goes to the model."""


def commands_only(command):
    """Drop heredoc bodies. What a heredoc feeds a command is data, not commands.

    Everything from the first `<<` to the end is the marker and the body, so the
    commands all sit on the first line.
    """
    return command.split("\n", 1)[0] if "<<" in command else command


def pipeline_segments(command):
    """Split on separators that sit outside quotes.

    A blind split breaks `grep -E "a|b"` into two segments and refuses the
    second, which is what a real audit hits within a few steps.
    """
    parts, buf, quote, i = [], "", None, 0
    while i < len(command):
        char = command[i]
        if quote:
            buf += char
            if char == quote:
                quote = None
            i += 1
        elif char in "'\"":
            quote = char; buf += char; i += 1
        elif char == "\\" and i + 1 < len(command):
            buf += command[i:i + 2]; i += 2
        else:
            hit = next((sep for sep in SEPARATORS if command.startswith(sep, i)), None)
            if hit:
                parts.append(buf); buf = ""; i += len(hit)
            else:
                buf += char; i += 1
    parts.append(buf)
    return [p.strip() for p in parts if p.strip()]


def first_word(segment):
    """The tool a segment runs, past any VAR=value prefix or opening bracket."""
    for token in segment.split():
        word = token.lstrip("({").strip("'\"")
        if not word:
            continue
        if "=" in word.split("/")[0]:        # VAR=value prefix, keep looking
            continue
        return word.split("/")[-1]
    return ""


def check(command):
    """Raise Refused if this command may not run. Return None when it may."""
    if not command.strip():
        raise Refused("empty command")
    for bad in DENIED:
        if bad in command:
            raise Refused(f"refused: {bad!r} is never run here. This agent reads only; "
                          f"report what should change and let a person do it")
    for segment in pipeline_segments(commands_only(command)):
        word = first_word(segment)
        if word not in ALLOWED:
            raise Refused(f"refused: {word!r} is not one of the tools this agent may run. "
                          f"Allowed: {', '.join(sorted(ALLOWED))}")


def run(command, cwd, timeout=DEFAULT_TIMEOUT):
    """Run the command and return what the model should see."""
    check(command)
    # No pipefail: `... | head` closing the pipe reports 141, which reads as a
    # failure to the model and is not one. The last command's code is enough.
    try:
        p = subprocess.run(["bash", "-c", command], cwd=cwd,
                           capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"exit_code": 124, "output": f"timed out after {timeout}s"}
    out = (p.stdout + p.stderr).strip()
    if len(out) > MAX_OUTPUT:
        out = out[:MAX_OUTPUT] + f"\n[... {len(out) - MAX_OUTPUT} more characters cut]"
    return {"exit_code": p.returncode, "output": out or "(no output)"}
