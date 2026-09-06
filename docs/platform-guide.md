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

1. Add an `AppProject` in `platform/argocd/` named `team-<slug>` with
   `destinations` limited to `team-<slug>` and `sourceRepos` limited to this
   repo. Copy `team-demo.yaml`.
2. Create `deployments/agents/<slug>/` with a `CODEOWNERS` line for the team.
3. If the team will run `codeexec` agents, apply a `SandboxTemplate` and
   `SandboxWarmPool` into `team-<slug>`. Copy `platform/sandbox/`.

The minter labels the namespace for kagent tool sharing on its own.

## Add a model

Edit the `model_list` in `platform/gateway/gateway.yaml`. Add the name to the
`model` enum in `charts/agent/values.schema.json` and to the `LLM_MODEL` note
in the dev guide. Apply. Existing keys are bound to their model, so a new
model needs a new PR from the team.

## Add a tool

1. Run the MCP server in the cluster. Expose it with a Service.
2. Add a target to the `AgentgatewayBackend` in `tools-gateway.yaml`, or a
   `RemoteMCPServer` in `kagent` for prompt agents.
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
