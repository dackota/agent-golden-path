#!/usr/bin/env bash
# PROTOTYPE. One-time: create the platform signing key and install the JWT
# policy on the tools route. Real path: use the org IdP (Entra) as issuer.
# Usage: tools-jwks.sh <kube-context>
set -euo pipefail
CTX="$1"; DIR="$(cd "$(dirname "$0")" && pwd)"; KEYS="$DIR/.keys"; mkdir -p "$KEYS"
[ -f "$KEYS/platform.pem" ] || openssl genrsa -out "$KEYS/platform.pem" 2048 2>/dev/null
openssl rsa -in "$KEYS/platform.pem" -pubout -out "$KEYS/platform.pub" 2>/dev/null
MOD_HEX=$(openssl rsa -pubin -in "$KEYS/platform.pub" -noout -modulus | cut -d= -f2)
N=$(python3 -c "import base64,sys; print(base64.urlsafe_b64encode(bytes.fromhex(sys.argv[1])).rstrip(b'=').decode())" "$MOD_HEX")
JWKS=$(printf '{"keys":[{"kty":"RSA","use":"sig","alg":"RS256","kid":"platform-1","n":"%s","e":"AQAB"}]}' "$N")
kubectl --context "$CTX" apply -f - <<YAML
apiVersion: agentgateway.dev/v1alpha1
kind: AgentgatewayPolicy
metadata:
  name: platform-tools-jwt
  namespace: platform-gateway
spec:
  targetRefs:
    - group: gateway.networking.k8s.io
      kind: HTTPRoute
      name: platform-tools
  traffic:
    jwtAuthentication:
      mode: Strict
      providers:
        - issuer: goldenpath.dev
          audiences: [platform-tools]
          jwks:
            inline: '$JWKS'
YAML
echo "jwks installed (kid platform-1)"
