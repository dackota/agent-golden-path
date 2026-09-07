# Platform guide: install and operate

## Install on a laptop

```
make up
```

That creates a kind cluster and installs, in order: kagent and its CRDs,
agent-sandbox, the LiteLLM gateway with Postgres, Gateway API CRDs,
agentgateway and its proxy, the tools route and JWT policy, the minter, Argo
CD, and the Argo project and ApplicationSet. Argo then deploys the demo agents
from `deployments/agents/demo/`.

Ollama must be running on the host with `gemma4:12b` pulled. Pods reach it at
`host.docker.internal:11434`.

## What lives where

| Path | Purpose |
|---|---|
| `platform/gateway/gateway.yaml` | LiteLLM, its Postgres, and the model catalog (`config.yaml`) |
| `platform/gateway/agentgateway-proxy.yaml` | the one `Gateway` every route attaches to |
| `platform/gateway/tools-gateway.yaml` | `AgentgatewayBackend` for the MCP servers, its route, default deny |
| `platform/gateway/tools-jwks.sh` | creates the platform signing key and installs the JWT policy |
| `platform/minter/` | the credential minter: script, image, CronJob, RBAC |
| `platform/kagent/` | Helm values and the tool server sharing rule |
| `platform/sandbox/` | hardened `SandboxTemplate` and warm pool |
| `platform/argocd/` | `AppProject` per team and the `ApplicationSet` |

## Add a team

1. Copy `platform/argocd/team-ops.yaml` to `team-<slug>.yaml`. It holds the
   `Namespace` (platform-owned, because Argo projects may not create
   cluster-scoped resources) and the `AppProject` that limits the team to that
   namespace and this repo.
2. Add the team and its monthly dollar cap to
   `platform/budgets/team-budgets.yaml`. Without an entry the minter refuses to
   mint keys for that team, so this step is not optional.
3. Create `deployments/agents/<slug>/` with a `CODEOWNERS` line for the team.
4. If the team will run `codeexec` agents, apply a `SandboxTemplate` and
   `SandboxWarmPool` into `team-<slug>`. Copy `platform/sandbox/`.

The minter labels the namespace for kagent tool sharing on its own.

## Change a team's budget

Edit `platform/budgets/team-budgets.yaml` and apply. The minter reconciles the
cap at the gateway on its next run, within a minute. Lowering a cap below what
the team already spent this period stops its agents at once, so lower it with
the team, not at them.

Per-agent caps in `values.yaml` sit under the team cap. They do not need to sum
to it, and the team cap wins when they exceed it.

## Move existing agents into their team

An agent minted before team budgets has a key with no team, so the team cap
does not cover it. The minter only mints when the credentials Secret is absent,
so delete the Secret and it re-mints inside the team within a minute.

```
kubectl --context <ctx> -n team-<slug> delete secret <agent>-platform-credentials
kubectl --context <ctx> -n team-<slug> rollout restart deploy/<agent>
```

The restart is not optional. Re-minting replaces the key at the gateway, and a
running pod keeps the old one in its environment until it restarts. Do one
agent at a time.

## Add a model

Edit the `model_list` in `platform/gateway/gateway.yaml`. Add the name to the
`model` enum in `charts/agent/values.schema.json` and to the `LLM_MODEL` note
in the dev guide. Apply. Existing keys are bound to their model, so a new
model needs a new PR from the team.

## Add a tool

1. Run the MCP server in the cluster with a kagent `MCPServer` resource. kmcp
   creates the Deployment and Service. `platform/tools/orders.yaml` is the
   worked example, and `examples/orders/mcp_server.py` shows how to wrap a
   REST API in about 90 lines with no framework.
2. Add a `RemoteMCPServer` in `kagent` with `allowedNamespaces` for prompt
   agents, and a target on the `AgentgatewayBackend` in `tools-gateway.yaml`
   for container agents. Keep `prefixMode: Never` and make sure tool names do
   not collide across servers.
3. Add a catalog entry in `charts/agent/templates/_helpers.tpl`: the friendly
   name, the server, and the exact tool names it grants. Add the name to the
   `tools[].name` enum in the schema.

Prefer read-only tool sets. Put anything that writes behind `approval: true`.

## Rotate the tool signing key

Run `platform/gateway/tools-jwks.sh` with a fresh key in
`platform/gateway/.keys/`, update `Secret platform-tools-signing-key` in
`platform-gateway`, then delete every `*-platform-credentials` Secret. The
minter re-mints them on its next run and pods restart on the new token. Old
tokens fail at the gateway as soon as the JWKS changes.

## Watch it

```
make status
kubectl --context kind-agent-spike -n platform-gateway logs job/$(kubectl --context kind-agent-spike -n platform-gateway get jobs --sort-by=.metadata.creationTimestamp -o name | tail -1 | cut -d/ -f2)
```

Spend per agent: `GET /key/info?key=<key>` on LiteLLM with the master key, or
the LiteLLM UI. Tool calls: agentgateway logs in `agentgateway-system`.

## What changes for a real cluster

| Local | Real |
|---|---|
| Ollama on the host | Anthropic or Azure OpenAI behind LiteLLM. No agent changes |
| Platform RSA key signs tool JWTs | The org IdP (Entra) is the issuer. agentgateway reads its JWKS |
| Minter CronJob every minute | A controller with a watch, or External Secrets Operator writing to a secret manager |
| Minter reads Secrets cluster-wide | One Role per team namespace |
| SandboxTemplate without `runtimeClassName` | `runtimeClassName: gvisor` and node pools that have it |
| `OTEL_*` env with no collector | An OpenTelemetry collector in `platform-observability`. Content capture off |
| Signed images, not enforced | Kyverno or Sigstore policy-controller verifies the cosign identity at admission |
| Public git, no credentials | Argo repo Secret for a private repo |
| Path-based HTTP on the proxy | TLS and SSO on the gateway listener |
| `egress` recorded only | Cilium `toFQDNs` or an egress proxy |

## Known limits

- The minter cannot recover a lost LiteLLM key. It deletes the alias and mints
  a new one, which changes the key. Pods pick it up on restart.
- Two agents with the same `name` in different teams share a tool policy
  name prefix but different `sub`, so they do not collide. Same `name` in the
  same team is a Helm release conflict and Argo will say so.
- `sandbox.expires` is a fixed time, not a duration, because anything derived
  from "now" makes Argo permanently out of sync.
