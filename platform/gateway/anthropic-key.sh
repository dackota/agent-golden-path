#!/usr/bin/env bash
# Give the LLM gateway the Anthropic key that backs the deep-reason model.
# The key stays in the gateway. No agent pod holds it; agents get a virtual key.
# Usage: anthropic-key.sh <kube-context> [key]
set -euo pipefail
CTX="$1"
KEY="${2:-${ANTHROPIC_API_KEY:-}}"
[ -n "$KEY" ] || { echo "no key: pass one or set ANTHROPIC_API_KEY" >&2; exit 1; }
kubectl --context "$CTX" -n platform-gateway patch secret litellm-secrets \
  --type merge -p "{\"stringData\":{\"ANTHROPIC_API_KEY\":\"$KEY\"}}"
kubectl --context "$CTX" -n platform-gateway rollout restart deploy/litellm
kubectl --context "$CTX" -n platform-gateway rollout status deploy/litellm --timeout=300s
echo "deep-reason is live"
