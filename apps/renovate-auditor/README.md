# renovate-auditor

A `kind: container` agent that audits one Renovate dependency pull request and
says whether it is safe to merge. It is the worked example for two things this
chart can do that a prompt agent cannot: hold a repository token the platform
minted, and run a shell.

## What it does

The model gets one tool: run a command in a clone of the repository. That is
enough for the whole method. Triage the checks with `gh`, measure the stale base
with `git rev-list`, read the major's release notes, grep every removed option
against the base branch, install both versions of a package side by side and run
the real operation, then build and test.

It reads. `shell.py` refuses `git push`, `git commit` and every `gh` write, so a
fix stays a person's decision.

## Deploying it

```yaml
name: renovate-auditor
team: <your team>
owner: you@example.com
description: Audits one Renovate pull request end to end.
kind: container
image: ghcr.io/dackota/renovate-auditor:0.3.0
model: deep-reason
budget:
  usdPerMonth: 50
github:
  repos:
    - <owner>/<repo>          # the platform mints GITHUB_TOKEN for this
egress:
  - github.com
  - api.github.com
  - registry.npmjs.org
size: large
autoscaling:
  enabled: false
persistence:
  enabled: true               # the clone and the package store live here
  size: 20Gi
env:
  HOME: /data
systemPrompt: |
  <the audit method, and anything true only of your repository>
```

The prompt is the method, so put your own rules in it: packages that must never
be bumped, paths that need a human, the build commands that count as verified.

## Talking to it

An audit takes minutes, so a run is asynchronous.

```
POST /audit  {"task": "Audit pull request 1596."}   -> 202 {"run_id": "..."}
GET  /runs/<run_id>                                 -> state, log, answer
GET  /healthz                                       -> answers even mid-audit
```

## What it needs from the platform

| Env | Where it comes from |
|---|---|
| `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL` | the LLM gateway, budgeted per agent |
| `GITHUB_TOKEN`, `GITHUB_REPOS` | minted because `github.repos` was declared |
| `AGENT_SYSTEM_PROMPT` | your `systemPrompt`, so the method changes without a rebuild |

None of it is typed by a developer.

## Two limits worth knowing

Tool output is cut at 4000 characters, and the conversation is trimmed to a
character budget, because the whole history is re-sent on every turn and an
agent key has a per-minute token cap at the gateway. When the cap is reached the
loop reads the reset time out of the gateway's message and waits, rather than
failing the run.
