#!/usr/bin/env python3
"""PROTOTYPE. Turns one agent.yaml into platform manifests.

Usage: render.py <agent.yaml> <out-dir>

Emits, based on kind:
  prompt    -> kagent.yaml    (Namespace, ModelConfig -> gateway, Agent)
  container -> helm-values.yaml (for charts/agent-app)
  codeexec  -> sandbox.yaml   (SandboxClaim against the platform warm pool)
Always emits key.json: what the gateway key minter needs.
"""
import json
import re
import sys
from pathlib import Path

import yaml

HERE = Path(__file__).parent
SCHEMA = json.loads((HERE / "agent.schema.json").read_text())
GATEWAY_URL = "http://litellm.platform-gateway.svc.cluster.local:4000"
TOOL_CATALOG = {  # platform-owned. name -> (RemoteMCPServer, allowed tool names)
    "k8s-readonly": ("kagent-tool-server", ["k8s_get_resources", "k8s_describe_resource", "k8s_get_pod_logs"]),
    "grafana": ("kagent-grafana-mcp", []),
    "web-fetch": ("kagent-tool-server", ["http_fetch"]),
}


def fail(msg):
    print(f"REJECTED: {msg}", file=sys.stderr)
    sys.exit(2)


def validate(doc):
    """Tiny hand-rolled check. Real path: jsonschema + admission policy."""
    props = SCHEMA["properties"]
    for k in SCHEMA["required"]:
        if k not in doc:
            fail(f"missing required field '{k}'")
    for k in doc:
        if k not in props:
            fail(f"unknown field '{k}'. Allowed: {sorted(props)}")
    for k, spec in props.items():
        if k not in doc:
            continue
        v = doc[k]
        if "enum" in spec and v not in spec["enum"]:
            fail(f"{k}={v!r} not in {spec['enum']}")
        if "pattern" in spec and not re.match(spec["pattern"], str(v)):
            fail(f"{k}={v!r} does not match {spec['pattern']}")
    if doc["kind"] == "container":
        c = doc.get("container") or fail("kind=container needs 'container.image'")
        pat = props["container"]["properties"]["image"]["pattern"]
        if not re.match(pat, c["image"]):
            fail(f"image {c['image']!r} not from an allowed registry")
    usd = (doc.get("budget") or {}).get("usdPerMonth", 20)
    if not 1 <= usd <= 500:
        fail("budget.usdPerMonth must be 1..500")
    for t in doc.get("tools", []):
        if t["name"] not in TOOL_CATALOG:
            fail(f"tool {t['name']!r} not in catalog {sorted(TOOL_CATALOG)}")


def common(doc):
    ns = f"team-{doc['team']}"
    return ns, {
        "app.kubernetes.io/name": doc["name"],
        "platform.suresteel.com/team": doc["team"],
        "platform.suresteel.com/kind": doc["kind"],
    }, {"platform.suresteel.com/owner": doc["owner"], "platform.suresteel.com/description": doc["description"]}


def render_kagent(doc):
    ns, labels, ann = common(doc)
    tools = []
    for t in doc.get("tools", []):
        server, names = TOOL_CATALOG[t["name"]]
        entry = {"type": "McpServer", "mcpServer": {"apiGroup": "kagent.dev", "kind": "RemoteMCPServer",
                 "name": server, "namespace": "kagent", "toolNames": names}}
        if t.get("approval"):
            entry["mcpServer"]["requireApproval"] = names
        tools.append(entry)
    return [
        {"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": ns}},
        {"apiVersion": "kagent.dev/v1alpha2", "kind": "ModelConfig",
         "metadata": {"name": f"{doc['name']}-model", "namespace": ns, "labels": labels},
         "spec": {"provider": "OpenAI", "model": doc.get("model", "default-chat"),
                  "apiKeySecret": f"{doc['name']}-llm-key", "apiKeySecretKey": "LLM_API_KEY",
                  "openAI": {"baseUrl": GATEWAY_URL}}},
        {"apiVersion": "kagent.dev/v1alpha2", "kind": "Agent",
         "metadata": {"name": doc["name"], "namespace": ns, "labels": labels, "annotations": ann},
         "spec": {"type": "Declarative", "description": doc["description"],
                  "declarative": {"modelConfig": f"{doc['name']}-model",
                                  "systemMessage": doc.get("systemPrompt", ""),
                                  "tools": tools,
                                  "deployment": {"resources": {"requests": {"cpu": "100m", "memory": "128Mi"},
                                                               "limits": {"cpu": "1", "memory": "512Mi"}}}}}},
    ]


def render_tools_policy(doc):
    """Per-agent tool allow-list at the MCP gateway. Namespace = the Backend's."""
    ns, labels, ann = common(doc)
    names = sorted({n for t in doc.get("tools", []) for n in TOOL_CATALOG[t["name"]][1]})
    if not names:
        return None
    allow = ", ".join(f'"{n}"' for n in names)
    return {"apiVersion": "agentgateway.dev/v1alpha1", "kind": "AgentgatewayPolicy",
            "metadata": {"name": f"tools-allow-{doc['team']}-{doc['name']}", "namespace": "platform-gateway",
                         "labels": labels, "annotations": ann},
            "spec": {"targetRefs": [{"group": "agentgateway.dev", "kind": "AgentgatewayBackend", "name": "platform-tools"}],
                     "backend": {"mcp": {"authorization": {"action": "Allow", "policy": {"matchExpressions": [
                         f'jwt.sub == "{ns}/{doc["name"]}" && mcp.tool.name in [{allow}]']}}}}}}


def render_helm(doc):
    ns, labels, ann = common(doc)
    c = doc["container"]
    return {
        "name": doc["name"], "team": doc["team"], "owner": doc["owner"],
        "image": c["image"], "port": c.get("port", 8080), "replicas": c.get("replicas", 1),
        "model": doc.get("model", "default-chat"), "systemPrompt": doc.get("systemPrompt", ""),
        "gatewayUrl": GATEWAY_URL, "extraEgressHosts": doc.get("egress", []),
    }


def render_sandbox(doc):
    ns, labels, ann = common(doc)
    return [
        {"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": ns}},
        {"apiVersion": "extensions.agents.x-k8s.io/v1beta1", "kind": "SandboxClaim",
         "metadata": {"name": doc["name"], "namespace": ns, "labels": labels, "annotations": ann},
         "spec": {"warmPoolRef": {"name": "platform-python-pool"},
                  "lifecycle": {"shutdownPolicy": "Delete"}}},
    ]


def main():
    src, out = Path(sys.argv[1]), Path(sys.argv[2])
    doc = yaml.safe_load(src.read_text())
    validate(doc)
    out.mkdir(parents=True, exist_ok=True)
    ns = f"team-{doc['team']}"
    (out / "key.json").write_text(json.dumps({
        "agent": doc["name"], "namespace": ns, "team": doc["team"], "owner": doc["owner"],
        "model": doc.get("model", "default-chat"),
        "usdPerMonth": (doc.get("budget") or {}).get("usdPerMonth", 20)}, indent=2))
    if doc["kind"] == "prompt":
        (out / "kagent.yaml").write_text(yaml.safe_dump_all(render_kagent(doc), sort_keys=False))
    elif doc["kind"] == "container":
        (out / "helm-values.yaml").write_text(yaml.safe_dump(render_helm(doc), sort_keys=False))
        pol = render_tools_policy(doc)
        if pol:
            (out / "tools-policy.yaml").write_text(yaml.safe_dump(pol, sort_keys=False))
    else:
        (out / "sandbox.yaml").write_text(yaml.safe_dump_all(render_sandbox(doc), sort_keys=False))
    print(f"OK kind={doc['kind']} -> {out}")


if __name__ == "__main__":
    main()
