#!/usr/bin/env bash
# Every case here must be REJECTED by charts/agent/values.schema.json.
set -u
cd "$(dirname "$0")/.."
BASE=deployments/agents/demo/vibe-app/values.yaml
PROMPT=deployments/agents/demo/faq-helper/values.yaml
fail=0
expect_reject() {
  local label="$1"; shift
  if helm template t charts/agent "$@" >/dev/null 2>&1; then
    echo "FAIL (accepted): $label"; fail=1
  else
    echo "ok   (rejected): $label"
  fi
}
expect_reject "container without image"     -f "$PROMPT" --set kind=container
expect_reject "image tag :latest"           -f "$BASE" --set image=ghcr.io/dackota/x:latest
expect_reject "image from foreign registry" -f "$BASE" --set image=docker.io/x/y:1
expect_reject "env overrides LLM_API_KEY"   -f "$BASE" --set env.LLM_API_KEY=x
expect_reject "unknown field privileged"    -f "$BASE" --set privileged=true
expect_reject "budget over cap"             -f "$BASE" --set budget.usdPerMonth=9999
expect_reject "tool not in catalog"         -f "$BASE" --set 'tools[0].name=shell'
expect_reject "replicas over cap"           -f "$BASE" --set replicas=50
expect_reject "model not in catalog"        -f "$BASE" --set model=gpt-99
expect_reject "prompt kind without prompt"  -f deployments/agents/demo/data-cruncher/values.yaml --set kind=prompt
expect_reject "bad cron schedule"           -f "$BASE" --set schedule="every day"
expect_reject "env overrides GITHUB_TOKEN"  -f "$BASE" --set env.GITHUB_TOKEN=x
expect_reject "repo without an owner"       -f "$BASE" --set 'github.repos[0]=widgets'
expect_reject "repo with a url"             -f "$BASE" --set 'github.repos[0]=https://github.com/o/r'
expect_reject "egress host not approved"    -f "$BASE" --set 'egress[0]=evil.example.com'
expect_reject "eval without an assert"      -f tests/values-nightly-report.yaml --set 'evals[0].input=hi'
expect_reject "eval with an unknown field"  -f "$BASE" --set 'evals[0].input=hi' --set 'evals[0].assert[0].type=regex' --set 'evals[0].expect=x'
expect_reject "more than five repos"        -f "$BASE" --set 'github.repos={a/b,c/d,e/f,g/h,i/j,k/l}'
echo "-- positive: cron agent renders"
helm template t charts/agent -f tests/values-nightly-report.yaml >/dev/null && echo "ok   (accepted): nightly-report" || { echo "FAIL: nightly-report"; fail=1; }
exit $fail
