#!/usr/bin/env bash
# Put the platform GitHub token where the github-prs tool server reads it.
# The token stays in that pod. No agent pod ever holds it.
# Real path: a GitHub App installation token, rotated by the minter.
# Usage: github-token.sh <kube-context> [token]
set -euo pipefail
CTX="$1"
TOKEN="${2:-${GITHUB_TOKEN:-$(gh auth token)}}"
[ -n "$TOKEN" ] || { echo "no token: pass one, set GITHUB_TOKEN, or run gh auth login" >&2; exit 1; }
kubectl --context "$CTX" create secret generic github-prs-token -n kagent \
  --from-literal=token="$TOKEN" \
  --dry-run=client -o yaml | kubectl --context "$CTX" apply -f -
echo "github-prs-token installed in kagent"
