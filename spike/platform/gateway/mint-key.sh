#!/usr/bin/env bash
# PROTOTYPE. Mint one gateway virtual key for one agent, store it as a Secret
# in the agent's namespace. Run by the platform pipeline, never by the dev.
# Usage: mint-key.sh <kube-context> <out-dir-with-key.json>
set -euo pipefail
CTX="$1"; OUT="$2"
read -r AGENT NS TEAM OWNER MODEL USD < <(python3 -c '
import json,sys; d=json.load(open(sys.argv[1]+"/key.json"))
print(d["agent"], d["namespace"], d["team"], d["owner"], d["model"], d["usdPerMonth"])' "$OUT")
MASTER=$(kubectl --context "$CTX" -n platform-gateway get secret litellm-secrets -o jsonpath='{.data.LITELLM_MASTER_KEY}' | base64 -d)
kubectl --context "$CTX" -n platform-gateway port-forward svc/litellm 14000:4000 >/dev/null 2>&1 &
PF=$!; trap 'kill $PF 2>/dev/null' EXIT; sleep 2
KEY=$(curl -sf http://127.0.0.1:14000/key/generate \
  -H "Authorization: Bearer $MASTER" -H 'Content-Type: application/json' \
  -d "{\"key_alias\":\"$NS/$AGENT\",\"models\":[\"$MODEL\"],\"max_budget\":$USD,\"budget_duration\":\"30d\",
       \"rpm_limit\":60,\"tpm_limit\":200000,
       \"metadata\":{\"team\":\"$TEAM\",\"owner\":\"$OWNER\",\"agent\":\"$AGENT\"}}" | python3 -c 'import json,sys; print(json.load(sys.stdin)["key"])')
kubectl --context "$CTX" create ns "$NS" --dry-run=client -o yaml | kubectl --context "$CTX" apply -f - >/dev/null
kubectl --context "$CTX" -n "$NS" create secret generic "$AGENT-llm-key" \
  --from-literal=LLM_API_KEY="$KEY" --dry-run=client -o yaml | kubectl --context "$CTX" apply -f -
echo "minted key for $NS/$AGENT model=$MODEL budget=\$$USD/30d"
