# Tooling: what each piece does and why it is here

Every tool below was chosen over at least one alternative. The alternative and
the reason are listed so the choice can be revisited.

## Kubernetes and GitOps

**kind.** Runs a real Kubernetes cluster in Docker on a laptop. Everything in
this repo installs into it. Recent kind ships `kube-network-policies`, so
NetworkPolicy is enforced, which let the egress guard rail be tested locally.
Alternative: minikube or k3d. Either works. kind was already installed.

**Helm.** The dev contract is a Helm chart with a strict
`values.schema.json`. Helm validates values before rendering, so a wrong field
fails fast with the field name. One lesson: Helm's schema engine uses Go
regex, which has no lookahead. Use `not` with a pattern instead.
Alternative: Kustomize overlays or a CRD plus operator. Kustomize has no
schema. A CRD is the right long-term shape, and kagent already offers one for
prompt agents, but writing an operator for all three kinds was more than the
job needed.

**Argo CD.** Watches `deployments/agents/*/*` with a git generator
`ApplicationSet` and turns each folder into an Application. One `AppProject`
per team pins the source repo and the destination namespace, and blocks
cluster-scoped resources. Automated sync with prune and self-heal means a
deleted folder removes the agent. Version 3.2.0.
Alternative: Flux. Equivalent. Argo's `AppProject` maps cleanly onto "a team
may only deploy its own agents into its own namespace".

**Gateway API.** The standard Kubernetes API for routing. Each container agent
gets an `HTTPRoute` at `/agents/<team>/<name>/` on the shared gateway. The
platform decides which gateway, the dev only gets a URL.
Alternative: Ingress. Ingress cannot express the MCP and JWT policies below.

## Model and tool access

**LiteLLM.** The LLM gateway. It holds the real provider keys and exposes an
OpenAI-compatible endpoint. The platform mints one virtual key per agent with
`max_budget`, `budget_duration`, a model allow-list, and rate limits. Spend is
tracked per key, so cost per agent and per team comes for free. It needs
Postgres for keys. Version `litellm-database:v1.90.2`.
Alternative: agentgateway can also route LLM traffic, and a real cluster might
consolidate on it. LiteLLM was chosen because per-key budgets and model
allow-lists were proven in an afternoon.

**agentgateway.** A data plane built for agent traffic: MCP, A2A, and LLM. It
fronts the MCP tool servers. One `AgentgatewayPolicy` requires a JWT on the
route. One small policy per agent, written by the minter, is a CEL expression
on `jwt.sub` and `mcp.tool.name`. Tools outside the list are absent from
`tools/list` and return "Unknown tool" if called by name. Multiple Allow
policies on one backend combine as OR, which is what makes per-agent policies
work. It also carries the HTTP routes to container agents. Version v1.5.0,
Linux Foundation, Apache 2.0.
Note: kgateway 2.4 no longer bundles agentgateway. agentgateway ships its own
Gateway API control plane now. The research note predates that.
Alternative: hand-rolled MCP proxy, or Kong or Envoy AI Gateway. Neither
understands MCP tool names for authorization.

**MCP (Model Context Protocol).** How agents call tools. Tool servers speak it,
agentgateway proxies it, the template app has a 60-line client for it. Using
the protocol means the platform can put one policy point in front of every
tool.

**Ollama.** Runs `gemma4:12b` on the laptop so the whole path works with no
cloud key. LiteLLM sees it as `ollama_chat/gemma4:12b`. Fake per-token prices
are set on the model so budgets do something locally. In a real cluster this
becomes Anthropic or Azure OpenAI behind the same gateway, and no agent changes.

## Runtimes

**kagent.** A Kubernetes operator for agents. A `prompt` agent is an `Agent`
CRD: system prompt, `ModelConfig`, tool list. kagent runs the loop (Google
ADK), exposes an A2A endpoint, supports `requireApproval` per tool, and
enforces `allowedNamespaces` on shared tool servers. Its `kagent-tools` MCP
server is the first entry in the tool catalog. CNCF sandbox, Apache 2.0,
version 0.10.0.
Alternative: run every agent as a container. That forces non-developers to
write and ship code for what is really a prompt.

**agent-sandbox (kubernetes-sigs).** `Sandbox`, `SandboxTemplate`,
`SandboxWarmPool`, `SandboxClaim`. The platform owns a hardened template and
keeps a warm pool. A `codeexec` agent claims one with a one-line CR and gets
an isolated pod in seconds with its own NetworkPolicy. On real nodes the
template sets `runtimeClassName: gvisor`. SIG Apps, v1.0.1.
Alternative: hosted sandboxes (E2B, Modal, AgentCore). Good products, but
they move the code and the data off the cluster.

## Supply chain

**GitHub Actions reusable workflow.** `build-agent.yml` is called from the
template repo. It validates `agent.yaml`, builds, pushes to GHCR, attaches an
SBOM, signs, and verifies. It lives in this public repo because a public
caller cannot use a reusable workflow from a private one.

**cosign (Sigstore).** Keyless signing with the workflow's GitHub OIDC
identity. `cosign verify` in the same run pins the identity to this
repository's workflow, so only images built by the golden path verify.
Alternative: key-based signing. Keyless removes the key to protect.

**syft via anchore/sbom-action.** Produces an SPDX SBOM per image and attaches
it as an attestation.

## Platform glue

**The minter.** Eighty lines of Python, stdlib only, running as a CronJob
every minute. It is the only thing that reads the LiteLLM master key or the
JWT signing key. It reacts to registration ConfigMaps the chart renders. This
is the piece that makes GitOps work without a dev ever handling a secret.
Alternative: External Secrets Operator with a secret manager the minter writes
to. That is the right shape for a real cluster and is listed in the platform
guide. A CronJob was enough to prove the flow.

**OpenTelemetry.** The chart injects `OTEL_*` env vars pointing at
`otel-collector.platform-observability`. That Service is the OpenTelemetry
Collector Helm chart. It receives OTLP, scrapes LiteLLM, agentgateway, and
the kagent controller for metrics, and forwards everything to one backend.
On kind the backend is one `grafana/otel-lgtm` pod: Tempo, Prometheus, Loki,
and Grafana. A real cluster changes one endpoint. Four things send to it. kagent sends traces. Its prompt audit stream is turned on but sent nothing from the Go runtime in 0.10.0, so treat it as unproven.
agentgateway sends a span and an access log line per request, with the MCP
tool name. LiteLLM sends a span per model call and exposes spend per key and
team on `/metrics`. The template app sends its own `invoke_agent`, `chat`, and
`execute_tool` spans with about 100 lines of stdlib code (`template/otel.py`),
and passes a `traceparent` header so the gateways join its trace. No message
content is captured anywhere by default.
Alternative: Jaeger (traces only), Phoenix (LLM views, single pod), Langfuse
(datasets and judges, six services). The LGTM pod is dev-only.

## Why not a hosted agent runtime

AWS AgentCore, Vertex Agent Engine, Azure Foundry Agent Service, and Claude
Managed Agents all exist and all work. They were not chosen for the default
path because each locks identity, memory, and tool routing to one vendor, and
because the org already runs Kubernetes with Helm, Argo, and GitHub Actions.
Agents became one more workload type on the platform the org already has.
The research note has the full comparison.
