#!/usr/bin/env bash
# Generate the Orders API credential and put it where the two sides need it.
#
#   demo-apis/orders-api-credentials        the API checks incoming requests against it
#   platform-gateway/orders-api-credentials the gateway injects it on the way out
#
# The value is random per install and never enters Git. On a real cluster the API
# team owns the key and External Secrets Operator syncs it from a vault, so this
# script exists only because a laptop has no vault.
set -euo pipefail
CTX="${1:?usage: orders-credential.sh <kube-context>}"

if kubectl --context "$CTX" -n platform-gateway get secret orders-api-credentials >/dev/null 2>&1; then
  echo "orders-api-credentials already exists, keeping it"
  exit 0
fi

KEY="$(head -c 32 /dev/urandom | base64 | tr -d '=+/' | cut -c1-32)"
for ns in demo-apis platform-gateway; do
  kubectl --context "$CTX" create namespace "$ns" --dry-run=client -o yaml | kubectl --context "$CTX" apply -f - >/dev/null
  kubectl --context "$CTX" -n "$ns" create secret generic orders-api-credentials \
    --from-literal=apiKey="$KEY" --dry-run=client -o yaml | kubectl --context "$CTX" apply -f -
done
echo "orders-api-credentials created in demo-apis and platform-gateway"
