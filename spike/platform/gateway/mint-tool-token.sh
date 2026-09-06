#!/usr/bin/env bash
# PROTOTYPE. Mint a 30-day JWT for one agent and add it to the agent's Secret.
# sub = <namespace>/<agent>. The tool allow-list is matched on sub at the gateway.
# Usage: mint-tool-token.sh <kube-context> <out-dir-with-key.json>
set -euo pipefail
CTX="$1"; OUT="$2"; DIR="$(cd "$(dirname "$0")" && pwd)"; KEY="$DIR/.keys/platform.pem"
read -r AGENT NS < <(python3 -c 'import json,sys; d=json.load(open(sys.argv[1]+"/key.json")); print(d["agent"], d["namespace"])' "$OUT")
b64() { python3 -c 'import base64,sys; print(base64.urlsafe_b64encode(sys.stdin.buffer.read()).rstrip(b"=").decode())'; }
NOW=$(date +%s); EXP=$((NOW + 30*24*3600))
H=$(printf '{"alg":"RS256","typ":"JWT","kid":"platform-1"}' | b64)
P=$(printf '{"iss":"platform.suresteel.com","aud":"platform-tools","sub":"%s/%s","iat":%s,"nbf":%s,"exp":%s}' "$NS" "$AGENT" "$NOW" "$NOW" "$EXP" | b64)
S=$(printf '%s.%s' "$H" "$P" | openssl dgst -sha256 -sign "$KEY" -binary | b64)
JWT="$H.$P.$S"
TOOLS_URL="http://agentgateway-proxy.agentgateway-system.svc.cluster.local/mcp/platform-tools"
kubectl --context "$CTX" -n "$NS" patch secret "$AGENT-llm-key" --type merge -p \
  "{\"stringData\":{\"TOOLS_TOKEN\":\"$JWT\",\"TOOLS_URL\":\"$TOOLS_URL\"}}" >/dev/null
echo "minted tool token for $NS/$AGENT (30d)"
