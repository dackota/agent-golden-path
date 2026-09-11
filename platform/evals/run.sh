#!/usr/bin/env bash
# One nightly run. For every agent that ships evals: build the live-mode
# config from its ConfigMap, run the evals and a small red team against the
# running agent, and post a pass rate per agent to the collector.
# Exit 100 if any agent failed a test, so the Job shows red.
set -uo pipefail
OUT=/tmp/evals
rm -rf "$OUT"; mkdir -p "$OUT"
python3 build.py build --mode live --out "$OUT" --from-cluster
rc=0
for d in "$OUT"/*/; do
  name=$(basename "$d")
  echo "== $name: evals"
  (cd "$d" && promptfoo eval -c promptfooconfig.yaml -o results.json --no-cache --no-progress-bar) || true
  python3 build.py report --agent "$d/agent.json" --results "$d/results.json" --suite eval || rc=100
  if [ "${RUN_REDTEAM:-true}" = "true" ]; then
    echo "== $name: red team"
    (cd "$d" && promptfoo redteam run -c redteam.yaml -o redteam-results.json --no-cache --no-progress-bar) || true
    [ -f "$d/redteam-results.json" ] && python3 build.py report --agent "$d/agent.json" \
        --results "$d/redteam-results.json" --suite redteam || rc=100
  fi
done
exit $rc
