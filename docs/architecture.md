# Architecture

## Components

| Component | Namespace | Owner | Job |
|---|---|---|---|
| Argo CD | `argocd` | platform | Turns `deployments/agents/<team>/<name>/` into an Application and keeps it in sync |
| `charts/agent` | in git | platform | Renders one agent's resources from `values.yaml` |
| Platform minter | `platform-gateway` | platform | Creates each agent's credentials Secret and tool policy from its registration |
| LiteLLM | `platform-gateway` | platform | LLM gateway. Holds provider keys. Per-agent keys with budgets and model allow-lists |
| agentgateway | `agentgateway-system` | platform | Gateway API data plane. Fronts MCP tool servers with JWT auth and CEL allow-lists. Also the public route to container agents |
| kagent | `kagent` | platform | Runs `prompt` agents from a CRD. Ships the MCP tool servers used in the catalog |
| agent-sandbox | `agent-sandbox-system` | platform | Warm pool of isolated pods for `codeexec` agents |
| Observability | `platform-observability` | platform | OpenTelemetry Collector as Service `otel-collector`. Receives traces and logs from agents, kagent, agentgateway, and LiteLLM. Scrapes their metrics. Forwards to `otel-lgtm` on kind, the org stack elsewhere |
| Agent pods | `team-<team>` | team | The agents themselves |

## Deploy flow

```mermaid
sequenceDiagram
  participant Dev
  participant Git as GitHub (this repo)
  participant CI as validate workflow
  participant Argo as Argo CD
  participant K8s as team namespace
  participant Minter
  Dev->>Git: PR adds deployments/agents/demo/x/values.yaml
  Git->>CI: helm template against schema
  CI-->>Dev: pass or fail with the field that is wrong
  Dev->>Git: merge
  Argo->>Git: poll (or webhook)
  Argo->>K8s: apply Deployment, Service, HTTPRoute, NetworkPolicy, registration ConfigMap
  Note over K8s: pod waits, Secret not yet present
  Minter->>K8s: list registration ConfigMaps (every minute)
  Minter->>LiteLLM: /key/generate with budget and model
  Minter->>K8s: create Secret x-platform-credentials
  Minter->>agentgateway: AgentgatewayPolicy allow-list for x
  K8s->>K8s: pod starts
```

## Request flow for a container agent

```mermaid
sequenceDiagram
  participant User
  participant AGW as agentgateway
  participant Pod as agent pod
  participant LLM as LiteLLM
  participant MCP as MCP tool server
  User->>AGW: POST /agents/demo/x/chat
  AGW->>Pod: /chat (prefix stripped)
  Pod->>LLM: chat/completions, Bearer LLM_API_KEY
  LLM-->>Pod: tool_call k8s_get_resources
  Pod->>AGW: MCP tools/call, Bearer TOOLS_TOKEN
  AGW->>AGW: verify JWT, evaluate CEL: sub == team-demo/x && tool in allow-list
  AGW->>MCP: forward
  MCP-->>Pod: result
  Pod->>LLM: continue
  LLM-->>Pod: answer
  Pod-->>User: reply + trace
```

## Credential flow

No credential is written by a human or committed to git.

1. The chart renders a `ConfigMap` named `<name>-agent` with the label
   `goldenpath.dev/registration: "true"`. It contains name, team, owner,
   model, budget, and the expanded list of tool names.
2. The minter reads every such ConfigMap across the cluster.
3. For each one without a Secret it mints a LiteLLM virtual key
   (`max_budget`, `budget_duration: 30d`, `models: [<model>]`) and, if the
   agent has tools, an RS256 JWT with `sub: team-<team>/<name>` and
   `aud: platform-tools`.
4. It writes both into `Secret <name>-platform-credentials` in the team
   namespace and applies an `AgentgatewayPolicy` in `platform-gateway` whose
   CEL expression names that `sub` and the allowed tool names.
5. When the registration disappears, the minter deletes the Secret and the
   policy.

The chart only knows the Secret's name. Pods reference it and wait.

## Network model

Each container agent gets a `NetworkPolicy`:

- Ingress from the agentgateway proxy and from its own namespace, on its port.
- Egress to `kube-system` DNS, to the LiteLLM pod on 4000, to the agentgateway
  proxy on 80, and to the OTel collector namespace on 4318.
- Everything else is denied. This was verified: a pod with the policy cannot
  reach the model host directly. A pod without it can.

Sandboxes get the same shape from the platform `SandboxTemplate`, minus ingress.

## Identity

Two identities exist per agent today. The LiteLLM virtual key identifies it to
the model gateway. The JWT `sub` identifies it to the tool gateway. Both are
minted by the platform and carry the team and owner in metadata, so spend and
tool calls can be attributed. The `ServiceAccount` carries a placeholder
annotation for cloud workload identity. That is the third identity a real
cluster adds.
