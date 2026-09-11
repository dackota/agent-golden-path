"""Platform credential minter. Runs in-cluster on a schedule.

For every agent registration ConfigMap (label goldenpath.dev/registration=true):
  1. label the namespace so kagent lets it use the shared tool servers
  2. make sure the team exists at the LLM gateway with its monthly cap
  3. mint an LLM gateway key inside that team, with the agent's own budget
  4. mint a tool JWT (RS256, platform key) when the agent has tools
  5. mint a GitHub token when the agent declares repos
  6. write them all into Secret <name>-platform-credentials in the agent's namespace
  7. write the agent's tool allow-list policy at the tool gateway
Then garbage-collect the Secrets, policies, and model keys whose
registration is gone.
Stdlib only. Talks to the Kubernetes API with the pod's ServiceAccount.
"""
import base64
import collections
import json
import os
import ssl
import subprocess
import sys
import time
import urllib.request

SA = "/var/run/secrets/kubernetes.io/serviceaccount"
# Read defensively so this module imports outside a cluster. require_config()
# fails fast in main() when a value is genuinely missing in the pod.
API = f"https://{os.environ.get('KUBERNETES_SERVICE_HOST', '')}:{os.environ.get('KUBERNETES_SERVICE_PORT', '')}"
TOKEN = open(f"{SA}/token").read() if os.path.exists(f"{SA}/token") else ""
CTX = ssl.create_default_context(cafile=f"{SA}/ca.crt") if os.path.exists(f"{SA}/ca.crt") else None
LITELLM = os.environ.get("LITELLM_URL", "http://litellm.platform-gateway.svc.cluster.local:4000")
MASTER = os.environ.get("LITELLM_MASTER_KEY", "")
SIGNING_KEY = os.environ.get("SIGNING_KEY_PATH", "/keys/platform.pem")
GATEWAY_NS = os.environ.get("GATEWAY_NS", "platform-gateway")
TOOLS_URL = "http://agentgateway-proxy.agentgateway-system.svc.cluster.local/mcp/platform-tools"
LABEL = "goldenpath.dev/registration=true"
BUDGETS_CM = os.environ.get("TEAM_BUDGETS_CONFIGMAP", "team-budgets")
GITHUB_SECRET = os.environ.get("GITHUB_TOKEN_SECRET", "kagent/github-prs-token")
MINTED = {"goldenpath.dev/minted": "true"}
# An alert fires at this share of an agent's budget, so someone hears before the hard stop.
SOFT_BUDGET_RATIO = 0.8
# The metrics reader key. It may call no model and can spend nothing, only read /metrics.
OBSERVABILITY_NS = os.environ.get("OBSERVABILITY_NS", "platform-observability")
METRICS_KEY_SECRET = "litellm-metrics-key"
METRICS_KEY_ALIAS = "platform/otel-collector"
NO_MODEL = "no-model"


def k8s(method, path, body=None):
    req = urllib.request.Request(API + path, method=method, data=json.dumps(body).encode() if body else None,
                                 headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json",
                                          "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, context=CTX, timeout=30) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def litellm_get(path):
    req = urllib.request.Request(LITELLM + path, headers={"Authorization": f"Bearer {MASTER}"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
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


def parse_team_budgets(data):
    """ConfigMap data to {team: dollars per month}. Raises on anything unusable.

    Platform config, so a typo must stop the run. A team that silently loses its
    cap is the failure this whole change exists to prevent.
    """
    budgets = {}
    for team, raw in data.items():
        try:
            cap = float(str(raw).strip())
        except ValueError:
            raise ValueError(f"team budget for {team!r} is not a number: {raw!r}")
        if cap <= 0:
            raise ValueError(f"team budget for {team!r} must be above zero, got {cap}")
        budgets[team] = cap
    return budgets


def require_team_budget(team, budgets):
    if team not in budgets:
        raise RuntimeError(f"team {team!r} has no budget in ConfigMap {BUDGETS_CM}")
    return budgets[team]


def team_action(existing, wanted):
    """create, update, or unchanged. `existing` is the team's info, or None."""
    if existing is None:
        return "create"
    return "unchanged" if existing.get("max_budget") == wanted else "update"


def team_id_for(team):
    return f"team-{team}"


def ensure_team(team, cap):
    """Make the gateway hold this team with this cap. Every agent key hangs off it."""
    tid = team_id_for(team)
    code, info = litellm_get(f"/team/info?team_id={tid}")
    existing = (info.get("team_info") or info) if code == 200 else None
    action = team_action(existing, cap)
    if action == "create":
        code, res = litellm("/team/new", {"team_id": tid, "team_alias": tid,
                                          "max_budget": cap, "budget_duration": "30d"})
    elif action == "update":
        code, res = litellm("/team/update", {"team_id": tid, "max_budget": cap,
                                             "budget_duration": "30d"})
    else:
        return tid, "unchanged"
    if code != 200:
        raise RuntimeError(f"team {action} {code}: {res}")
    return tid, action


def read_team_budgets():
    code, cm = k8s("GET", f"/api/v1/namespaces/{GATEWAY_NS}/configmaps/{BUDGETS_CM}")
    if code != 200:
        sys.exit(f"read {GATEWAY_NS}/{BUDGETS_CM}: {code} {cm}")
    return parse_team_budgets(cm.get("data") or {})


def key_body(reg, ns, team_id):
    """The /key/generate request for one agent. Pure, so the budget rules are tested."""
    return {"key_alias": f"{ns}/{reg['name']}", "team_id": team_id, "models": [reg["model"]],
            "max_budget": reg["usdPerMonth"], "soft_budget": reg["usdPerMonth"] * SOFT_BUDGET_RATIO,
            "budget_duration": "30d", "rpm_limit": 60, "tpm_limit": 200000,
            "metadata": {"team": reg["team"], "owner": reg["owner"], "agent": reg["name"]}}


def metrics_key_body():
    """A key that authenticates to /metrics and nothing else."""
    # /metrics is an admin route unless the key names it in allowed_routes.
    return {"key_alias": METRICS_KEY_ALIAS, "models": [NO_MODEL], "max_budget": 0.0001,
            "allowed_routes": ["/metrics", "/metrics/"], "metadata": {"purpose": "metrics"}}


def mint_llm_key(reg, ns, team_id):
    body = key_body(reg, ns, team_id)
    alias = body["key_alias"]
    code, res = litellm("/key/generate", body)
    if code == 400 and "alias" in json.dumps(res).lower():   # stale alias from a lost Secret
        litellm("/key/delete", {"key_aliases": [alias]})
        code, res = litellm("/key/generate", body)
    if code != 200:
        raise RuntimeError(f"litellm {code}: {res}")
    return res["key"]


def read_platform_github_token():
    ns, _, name = GITHUB_SECRET.partition("/")
    code, sec = k8s("GET", f"/api/v1/namespaces/{ns}/secrets/{name}")
    if code != 200:
        raise RuntimeError(f"read {GITHUB_SECRET}: {code} {sec}")
    return base64.b64decode(sec["data"]["token"]).decode()


def mint_github_token(reg):
    """A GitHub token for the repositories this agent declared, or None.

    PoC: hands back the platform token. Real path: exchange it here for a
    GitHub App installation token scoped to reg["repos"], short lived, and
    re-mint on every run. This function is the seam for that.
    """
    if not reg.get("repos"):
        return None
    return read_platform_github_token()


def secret_data(reg, llm_key, tools_token, github_token):
    """What an agent's credentials Secret holds. Pure, so it is tested.

    A credential is written only when the agent asked for the capability and
    the mint produced a value. An empty value would read as present in the pod
    and fail a long way from here.
    """
    data = {"LLM_API_KEY": llm_key, "LLM_BASE_URL": LITELLM, "LLM_MODEL": reg["model"]}
    if reg.get("toolNames") and tools_token:
        data["TOOLS_TOKEN"] = tools_token
        data["TOOLS_URL"] = TOOLS_URL
    if reg.get("repos") and github_token:
        data["GITHUB_TOKEN"] = github_token
    return data


def ensure_secret(reg, ns, team_id):
    name = f"{reg['name']}-platform-credentials"
    code, _ = k8s("GET", f"/api/v1/namespaces/{ns}/secrets/{name}")
    if code == 200:
        return "exists"
    jwt = mint_jwt(f"{ns}/{reg['name']}") if reg["toolNames"] else None
    data = secret_data(reg, mint_llm_key(reg, ns, team_id), jwt, mint_github_token(reg))
    body = {"apiVersion": "v1", "kind": "Secret", "type": "Opaque",
            "metadata": {"name": name, "namespace": ns, "labels": {**MINTED, "goldenpath.dev/agent-name": reg["name"]}},
            "stringData": data}
    code, res = k8s("POST", f"/api/v1/namespaces/{ns}/secrets", body)
    if code not in (200, 201):
        raise RuntimeError(f"secret {code}: {res}")
    return "created"


def ensure_metrics_key():
    """The collector's read-only key for LiteLLM /metrics, in the observability namespace."""
    path = f"/api/v1/namespaces/{OBSERVABILITY_NS}/secrets/{METRICS_KEY_SECRET}"
    code, _ = k8s("GET", path)
    if code == 200:
        return "exists"
    if code != 404:
        raise RuntimeError(f"read {path}: {code}")
    body = metrics_key_body()
    code, res = litellm("/key/generate", body)
    if code == 400 and "alias" in json.dumps(res).lower():   # stale alias from a lost Secret
        litellm("/key/delete", {"key_aliases": [body["key_alias"]]})
        code, res = litellm("/key/generate", body)
    if code != 200:
        raise RuntimeError(f"litellm metrics key {code}: {res}")
    secret = {"apiVersion": "v1", "kind": "Secret", "type": "Opaque",
              "metadata": {"name": METRICS_KEY_SECRET, "namespace": OBSERVABILITY_NS},
              "stringData": {"LITELLM_METRICS_KEY": res["key"]}}
    code, res = k8s("POST", f"/api/v1/namespaces/{OBSERVABILITY_NS}/secrets", secret)
    if code not in (200, 201):
        raise RuntimeError(f"metrics key secret {code}: {res}")
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


Deletion = collections.namedtuple("Deletion", "kind ns name")


def gc_plan(secrets, policies, live):
    """What to delete for agents whose registration is gone. Pure, so it is tested.

    Labels carry the agent identity. A missing label means the object cannot be
    matched to an agent, so the planner leaves it alone rather than guess.
    """
    plan = []
    for s in secrets:
        ns = s["metadata"]["namespace"]
        agent = s["metadata"]["labels"].get("goldenpath.dev/agent-name")
        if not (ns and agent) or (ns, agent) in live:
            continue
        plan.append(Deletion("secret", ns, s["metadata"]["name"]))
        plan.append(Deletion("llm-key", ns, f"{ns}/{agent}"))
    for p in policies:
        ns = p["metadata"]["labels"].get("goldenpath.dev/agent-ns")
        name = p["metadata"]["name"]
        agent = name.removeprefix(f"tools-allow-{ns}-")
        if not (ns and agent) or (ns, agent) in live:
            continue
        plan.append(Deletion("policy", GATEWAY_NS, name))
    return plan


def apply_deletion(d):
    if d.kind == "secret":
        k8s("DELETE", f"/api/v1/namespaces/{d.ns}/secrets/{d.name}")
    elif d.kind == "policy":
        k8s("DELETE", f"/apis/agentgateway.dev/v1alpha1/namespaces/{d.ns}/agentgatewaypolicies/{d.name}")
    elif d.kind == "llm-key":
        # Without this the key keeps its budget at the gateway after the agent is gone.
        litellm("/key/delete", {"key_aliases": [d.name]})
    print(f"gc {d.kind} {d.name}")


def gc(live):
    """Delete minted Secrets, policies, and model keys whose registration is gone."""
    _, secrets = k8s("GET", "/api/v1/secrets?labelSelector=goldenpath.dev/minted=true")
    _, pols = k8s("GET", f"/apis/agentgateway.dev/v1alpha1/namespaces/{GATEWAY_NS}/agentgatewaypolicies?labelSelector=goldenpath.dev/minted=true")
    for d in gc_plan(secrets.get("items", []), pols.get("items", []), live):
        apply_deletion(d)


def require_config():
    checks = (("KUBERNETES_SERVICE_HOST", os.environ.get("KUBERNETES_SERVICE_HOST")),
              ("KUBERNETES_SERVICE_PORT", os.environ.get("KUBERNETES_SERVICE_PORT")),
              ("service account token", TOKEN),
              ("LITELLM_MASTER_KEY", MASTER))
    missing = [name for name, value in checks if not value]
    if missing:
        sys.exit(f"missing configuration: {', '.join(missing)}")


def main():
    require_config()
    budgets = read_team_budgets()
    try:
        print(f"metrics key: {ensure_metrics_key()}")
    except Exception as e:   # the collector can wait a minute; agents cannot
        print(f"metrics key: failed ({e})")
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
            tid, t = ensure_team(reg["team"], require_team_budget(reg["team"], budgets))
            s = ensure_secret(reg, ns, tid)
            p = ensure_policy(reg, ns)
            print(f"{ns}/{reg['name']}: team={t} secret={s} policy={p} tools={reg['toolNames']} repos={reg.get('repos') or []}")
        except Exception as e:  # keep going for the other agents; surface in logs
            print(f"{ns}/{reg['name']}: ERROR {e}")
    gc(live)


if __name__ == "__main__":
    main()
