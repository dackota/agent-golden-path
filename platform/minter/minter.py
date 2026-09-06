"""Platform credential minter. Runs in-cluster on a schedule.

For every agent registration ConfigMap (label goldenpath.dev/registration=true):
  1. label the namespace so kagent lets it use the shared tool servers
  2. mint an LLM gateway key with the agent's budget (LiteLLM /key/generate)
  3. mint a tool JWT (RS256, platform key) when the agent has tools
  4. write both into Secret <name>-platform-credentials in the agent's namespace
  5. write the agent's tool allow-list policy at the tool gateway
Then garbage-collect Secrets and policies whose registration is gone.
Stdlib only. Talks to the Kubernetes API with the pod's ServiceAccount.
"""
import base64
import json
import os
import ssl
import subprocess
import sys
import time
import urllib.request

API = f"https://{os.environ['KUBERNETES_SERVICE_HOST']}:{os.environ['KUBERNETES_SERVICE_PORT']}"
SA = "/var/run/secrets/kubernetes.io/serviceaccount"
TOKEN = open(f"{SA}/token").read()
CTX = ssl.create_default_context(cafile=f"{SA}/ca.crt")
LITELLM = os.environ.get("LITELLM_URL", "http://litellm.platform-gateway.svc.cluster.local:4000")
MASTER = os.environ["LITELLM_MASTER_KEY"]
SIGNING_KEY = os.environ.get("SIGNING_KEY_PATH", "/keys/platform.pem")
GATEWAY_NS = os.environ.get("GATEWAY_NS", "platform-gateway")
TOOLS_URL = "http://agentgateway-proxy.agentgateway-system.svc.cluster.local/mcp/platform-tools"
LABEL = "goldenpath.dev/registration=true"
MINTED = {"goldenpath.dev/minted": "true"}


def k8s(method, path, body=None):
    req = urllib.request.Request(API + path, method=method, data=json.dumps(body).encode() if body else None,
                                 headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json",
                                          "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, context=CTX, timeout=30) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def litellm(path, body):
    req = urllib.request.Request(LITELLM + path, data=json.dumps(body).encode(),
                                 headers={"Authorization": f"Bearer {MASTER}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def b64url(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def mint_jwt(sub, days=30):
    now = int(time.time())
    header = b64url(json.dumps({"alg": "RS256", "typ": "JWT", "kid": "platform-1"}).encode())
    payload = b64url(json.dumps({"iss": "goldenpath.dev", "aud": "platform-tools", "sub": sub,
                                 "iat": now, "nbf": now, "exp": now + days * 86400}).encode())
    sig = subprocess.run(["openssl", "dgst", "-sha256", "-sign", SIGNING_KEY, "-binary"],
                         input=f"{header}.{payload}".encode(), capture_output=True, check=True).stdout
    return f"{header}.{payload}.{b64url(sig)}"


def mint_llm_key(reg, ns):
    alias = f"{ns}/{reg['name']}"
    body = {"key_alias": alias, "models": [reg["model"]], "max_budget": reg["usdPerMonth"],
            "budget_duration": "30d", "rpm_limit": 60, "tpm_limit": 200000,
            "metadata": {"team": reg["team"], "owner": reg["owner"], "agent": reg["name"]}}
    code, res = litellm("/key/generate", body)
    if code == 400 and "alias" in json.dumps(res).lower():   # stale alias from a lost Secret
        litellm("/key/delete", {"key_aliases": [alias]})
        code, res = litellm("/key/generate", body)
    if code != 200:
        raise RuntimeError(f"litellm {code}: {res}")
    return res["key"]


def ensure_secret(reg, ns):
    name = f"{reg['name']}-platform-credentials"
    code, _ = k8s("GET", f"/api/v1/namespaces/{ns}/secrets/{name}")
    if code == 200:
        return "exists"
    data = {"LLM_API_KEY": mint_llm_key(reg, ns), "LLM_BASE_URL": LITELLM, "LLM_MODEL": reg["model"]}
    if reg["toolNames"]:
        data["TOOLS_TOKEN"] = mint_jwt(f"{ns}/{reg['name']}")
        data["TOOLS_URL"] = TOOLS_URL
    body = {"apiVersion": "v1", "kind": "Secret", "type": "Opaque",
            "metadata": {"name": name, "namespace": ns, "labels": {**MINTED, "goldenpath.dev/agent-name": reg["name"]}},
            "stringData": data}
    code, res = k8s("POST", f"/api/v1/namespaces/{ns}/secrets", body)
    if code not in (200, 201):
        raise RuntimeError(f"secret {code}: {res}")
    return "created"


def ensure_policy(reg, ns):
    name = f"tools-allow-{ns}-{reg['name']}"
    path = f"/apis/agentgateway.dev/v1alpha1/namespaces/{GATEWAY_NS}/agentgatewaypolicies"
    if not reg["toolNames"]:
        code, _ = k8s("DELETE", f"{path}/{name}")
        return "removed" if code == 200 else "none"
    allow = ", ".join(f'"{t}"' for t in reg["toolNames"])
    body = {"apiVersion": "agentgateway.dev/v1alpha1", "kind": "AgentgatewayPolicy",
            "metadata": {"name": name, "namespace": GATEWAY_NS, "labels": {**MINTED, "goldenpath.dev/agent-ns": ns}},
            "spec": {"targetRefs": [{"group": "agentgateway.dev", "kind": "AgentgatewayBackend", "name": "platform-tools"}],
                     "backend": {"mcp": {"authorization": {"action": "Allow", "policy": {"matchExpressions": [
                         f'jwt.sub == "{ns}/{reg["name"]}" && mcp.tool.name in [{allow}]']}}}}}}
    code, cur = k8s("GET", f"{path}/{name}")
    if code == 200:
        if cur["spec"] == body["spec"]:
            return "unchanged"
        body["metadata"]["resourceVersion"] = cur["metadata"]["resourceVersion"]
        code, res = k8s("PUT", f"{path}/{name}", body)
    else:
        code, res = k8s("POST", path, body)
    if code not in (200, 201):
        raise RuntimeError(f"policy {code}: {res}")
    return "applied"


def ensure_ns_label(ns):
    req = urllib.request.Request(f"{API}/api/v1/namespaces/{ns}", method="PATCH",
                                 data=json.dumps({"metadata": {"labels": {"goldenpath.dev/agent-namespace": "true"}}}).encode(),
                                 headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/merge-patch+json"})
    urllib.request.urlopen(req, context=CTX, timeout=30).read()


def gc(live):
    """Delete minted Secrets and policies whose registration no longer exists."""
    _, secrets = k8s("GET", "/api/v1/secrets?labelSelector=goldenpath.dev/minted=true")
    for s in secrets.get("items", []):
        key = (s["metadata"]["namespace"], s["metadata"]["labels"].get("goldenpath.dev/agent-name"))
        if key not in live:
            k8s("DELETE", f"/api/v1/namespaces/{key[0]}/secrets/{s['metadata']['name']}")
            print(f"gc secret {key[0]}/{s['metadata']['name']}")
    _, pols = k8s("GET", f"/apis/agentgateway.dev/v1alpha1/namespaces/{GATEWAY_NS}/agentgatewaypolicies?labelSelector=goldenpath.dev/minted=true")
    for p in pols.get("items", []):
        ns = p["metadata"]["labels"].get("goldenpath.dev/agent-ns")
        agent = p["metadata"]["name"].removeprefix(f"tools-allow-{ns}-")
        if (ns, agent) not in live:
            k8s("DELETE", f"/apis/agentgateway.dev/v1alpha1/namespaces/{GATEWAY_NS}/agentgatewaypolicies/{p['metadata']['name']}")
            print(f"gc policy {p['metadata']['name']}")


def main():
    code, cms = k8s("GET", f"/api/v1/configmaps?labelSelector={LABEL}")
    if code != 200:
        sys.exit(f"list configmaps: {code} {cms}")
    live = set()
    for cm in cms.get("items", []):
        ns = cm["metadata"]["namespace"]
        try:
            reg = json.loads(cm["data"]["registration.json"])
        except (KeyError, ValueError) as e:
            print(f"skip {ns}/{cm['metadata']['name']}: bad registration ({e})")
            continue
        live.add((ns, reg["name"]))
        try:
            ensure_ns_label(ns)
            s = ensure_secret(reg, ns)
            p = ensure_policy(reg, ns)
            print(f"{ns}/{reg['name']}: secret={s} policy={p} tools={reg['toolNames']}")
        except Exception as e:  # keep going for the other agents; surface in logs
            print(f"{ns}/{reg['name']}: ERROR {e}")
    gc(live)


if __name__ == "__main__":
    main()
