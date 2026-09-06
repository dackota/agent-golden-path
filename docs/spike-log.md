# Spike log: how this was proven

This is the dated record of the proof of concept. Paths in it refer to the
pre-consolidation layout (`spike/...`). The current layout is in the root README.

PROTOTYPE. Throwaway. Date: 2026-09-05.

## The question

Many devs and non-devs have vibe-coded agent skills and AI apps. They ask us
how to get them into production. Which approach gives the easiest self-service
intake with guard rails: kagent CRDs, our own universal agent Helm chart, or
agent-sandbox?

## The answer

Use all three, behind one intake file. They cover different kinds of agent.

| What the dev has | Kind in `agent.yaml` | What runs it | Verified |
|---|---|---|---|
| A prompt plus tools, no code (a "skill") | `prompt` | kagent `Agent` CRD | Yes. Called a Kubernetes tool through the gateway and answered right. |
| Their own HTTP app image | `container` | `charts/agent-app` (our chart) | Yes. Chat worked through the gateway. The schema rejected a bad image and a bad model before anything was applied. With tools: the app saw only its 3 allowed tools and used one. |
| An agent that runs generated code | `codeexec` | agent-sandbox `SandboxClaim` | Yes. Claimed a warm sandbox in seconds. Non-root, read-only root, egress blocked. |

All three share two platform gateways. LiteLLM is the LLM gateway. It holds
the real model credentials and needs its own Postgres for keys and budgets,
which I did not expect for a spike. agentgateway is the MCP tool gateway. It
checks a per-agent JWT and a per-agent CEL tool allow-list. An agent never
holds a model key or a tool credential of its own. Each agent gets its own key with a budget and a model
allow-list. No agent pod can reach a model any other way.

## The GitOps path (the current answer)

The first pass used a renderer script and `make deploy`. That is now replaced
by one Helm chart and Argo CD. This is how a dev ships an agent today:

1. Copy a folder in [agent-deployments](https://github.com/dackota/agent-deployments)
   under `agents/<team>/<name>/` and edit `values.yaml`. Five to ten lines is
   normal. Every option and default is documented in `charts/agent/values.yaml`.
2. Open a PR. CI renders the values against the chart schema. Bad values fail
   there. CODEOWNERS keeps the chart and the Argo wiring platform-owned.
3. Merge. An Argo `ApplicationSet` turns each folder into an Application in
   project `team-<team>`, which may only deploy into namespace `team-<team>`
   from that one repo. Argo syncs, prunes, and self-heals.
4. The chart renders a small registration ConfigMap. A platform minter CronJob
   reads it and creates the agent's credentials Secret (LLM key with budget,
   tool JWT) and its tool allow-list policy. Pods wait for the Secret, then
   start. Delete the folder and the minter removes the Secret and the policy.

The chart has one `kind` switch and bundles only what one agent needs:

| `kind` | Renders |
|---|---|
| `container` | Deployment or CronJob (`schedule` set), Service, HTTPRoute at `/agents/<team>/<name>/`, HPA, PDB when replicas > 1, PVC when `persistence.enabled`, NetworkPolicy, ServiceAccount, prompt ConfigMap |
| `prompt` | kagent `Agent` and `ModelConfig` pointed at the LLM gateway, tools from the catalog, `approval: true` per tool |
| `codeexec` | `SandboxClaim` against the platform warm pool, optional `expires` |

Platform pieces stay out of the chart: gateways, JWKS, warm pool templates,
kagent install, tool servers, the minter, Argo projects.

Proven on this cluster with Argo CD v3.2.0:

| Check | Result |
|---|---|
| Four folders pushed | Four Applications created, all Synced and Healthy |
| Minter | Four Secrets and two tool policies created within one minute, no dev action |
| Prompt change committed | New pod rolled, reply changed |
| Folder deleted | CronJob, ConfigMaps, and the minted Secret all gone |
| App aimed at namespace `kagent` | Argo: "do not match any of the allowed destinations in project team-demo" |
| App from a foreign repo | Argo: "is not permitted in project team-demo" |
| Ten bad values files | All rejected by the schema. `:latest`, foreign registry, `privileged`, budget 9999, replicas 50, `env.LLM_API_KEY`, unknown tool, bad model, container without image, prompt without systemPrompt |
| Chart at the wrong path | Deployments repo CI failed on the first push, then passed |
| kagent agent with `approval: true` | Task stopped in `input-required` before the tool ran |
| Container agent through its gateway URL | Answered with a tool call |

Two lessons. Helm's schema engine is Go regex, so no lookaheads. Use `not`
with a pattern. And anything rendered from `now` makes Argo permanently
OutOfSync, so the sandbox expiry is an explicit timestamp.

## How the first pass worked (superseded)

`intake/render.py` and `make deploy` still run, but the chart and Argo above
replace them. Kept for reference.

### The first pass, step by step

1. The dev clicks "Use this template" on
   [agent-app-template](https://github.com/dackota/agent-app-template), adds
   code, and fills in `agent.yaml`. See also `intake/examples/`.
2. On push, the shared workflow in
   [agent-golden-path-workflows](https://github.com/dackota/agent-golden-path-workflows)
   validates `agent.yaml`, builds the image, pushes it to GHCR, attaches an
   SBOM, signs it with keyless cosign, and verifies the signature.
3. The platform pipeline runs `make deploy AGENT=<file>`. It:
   - validates the file against `intake/agent.schema.json`. Unknown fields,
     unknown tools, foreign registries, and budgets over the cap are rejected.
   - renders manifests to `out/<name>/`.
   - mints an LLM gateway key with the agent's budget and stores it as a
     Secret in the team namespace.
   - if the agent asked for tools, mints a tool JWT and applies a CEL
     allow-list at the tool gateway for that agent only.
   - applies the kagent Agent, or the Helm release, or the SandboxClaim.
4. The dev never touches a ModelConfig, a Secret, a NetworkPolicy, a
   SecurityContext, or a gateway policy. Those are not knobs.

## Guard rails proven in this spike

| Guard rail | Where | Proof |
|---|---|---|
| Only allowed registries, no `:latest` | chart `values.schema.json`, intake schema | `helm template` with `docker.io/evil/thing:latest` fails |
| Only catalog models | intake schema, chart schema, gateway key | `model=gpt-99` fails at render. Key for `default-chat` gets "key not allowed to access model" for `default-fast` |
| Per-agent budget | gateway virtual key | Key with $0.0001 budget: first call OK, second call "Budget has been exceeded" |
| Per-agent spend tracking | gateway `/key/info` | faq-helper key shows spend $0.0074 after one kagent call |
| No key, no model | gateway | Request without key returns 401 |
| Default-deny egress, gateway only | chart NetworkPolicy, SandboxTemplate networkPolicy | Agent pod to Ollama direct: unreachable. Same pod to gateway: 200. Unlabeled control pod to Ollama: 200 |
| Non-root, read-only root, no caps | chart, SandboxTemplate | `touch /etc/x` in sandbox: "Read-only file system". uid 10001 |
| Tool catalog is shared on purpose only | kagent `RemoteMCPServer.allowedNamespaces` | kagent refused the agent until the platform labeled the namespace |
| Tool allow-list per agent | kagent `toolNames` | faq-helper gets 3 read-only k8s tools out of 18 |
| Human approval per tool | kagent `requireApproval` | Rendered from `tools[].approval: true`. I did not test it. |
| Tool gateway needs a valid JWT | agentgateway `jwtAuthentication` Strict | No token: 401. Garbage token: 401 |
| Per-agent tool allow-list | agentgateway CEL on `jwt.sub` and `mcp.tool.name` | vibe-app token: `tools/list` shows 3 of 18 tools. Calling a hidden tool returns "Unknown tool". A valid JWT with no policy sees 0 tools |
| Only signed images from the shared workflow | CI `cosign verify` on the workflow identity | Run 34005158232 on the template repo: build, SBOM, sign, verify all green |
| CI rejects foreign images | shared workflow validation step | `docker.io/evil/thing:latest` in `agent.yaml` fails before the build |

## What each approach is like

**kagent (prompt agents).** The best fit for "skills", and the part of this
spike that surprised me. A CRD holds the prompt, model ref, and tool list.
kagent runs the agent loop for you (Google ADK). Every agent gets an A2A
endpoint for free. It caught my cross-namespace tool reference before I did.
The downsides are real, though. The CRDs are `v1alpha2`. The project is CNCF
sandbox stage. BYO mode needs the image to speak A2A, and a vibe-coded HTTP
app does not. The default Helm install also ships a dozen demo agents and a
UI with Postgres. Turn those off for real use.

**Our Helm chart (container agents).** The fit for "I have an image". The dev
sees eight values. Dangerous settings are not values at all, so there is
nothing to misuse. The schema rejects bad input before Helm touches the
cluster. The downside is that we own the chart forever.

**agent-sandbox (code-exec agents).** Simple and it works. The platform owns
the `SandboxTemplate` and a `SandboxWarmPool`. The dev files a one-line
`SandboxClaim`. The controller writes one NetworkPolicy per sandbox. Add
`runtimeClassName: gvisor` on real nodes. The catch is that you get an
isolated pod, not an agent. Something still has to drive it.

**agentgateway as the tool gateway.** This closed the biggest hole from the
first pass. Container agents had no safe way to reach tools. Now one
`AgentgatewayBackend` fronts the MCP server, one `HTTPRoute` exposes it, one
policy requires a JWT, and one small policy per agent says which tools exist
for it. Hidden tools do not show up in `tools/list` at all, so the model
cannot even try them. Two Allow policies on the same backend combine as OR,
which is what makes per-agent policies work. One correction to the research
note: kgateway 2.4 no longer bundles agentgateway. agentgateway now ships its
own Gateway API control plane, so "agentgateway behind kgateway" is now just
"agentgateway".

**The shared build workflow.** A public repo cannot call a reusable workflow
in a private repo, so the workflow lives in its own public repo. The template
repo is one click to copy. Its only rules are a `VERSION` file, an
`agent.yaml` whose image tag matches, and a `/healthz` route. The first real
run built, signed, and verified in about two minutes. The image then pulled
into kind with no credentials because GHCR inherits the public repo's
visibility.

## Not proven here

- gVisor. kind on macOS has no gVisor. The template has the line commented out.
- Workload identity. The ServiceAccount has a placeholder annotation.
- OpenTelemetry. The chart sets `OTEL_*` env vars but no collector is deployed.
- Eval gate in CI. Image signing is done. Signature enforcement at admission
  (Kyverno or Sigstore policy-controller) is not.
- Tool JWTs come from a platform RSA key in `platform/gateway/.keys/`. Real
  path: the org IdP (Entra) is the issuer and agentgateway reads its JWKS.
- Tool JWT rotation. Tokens live 30 days in a Secret. Nothing rotates them.
- Minter RBAC reads and writes Secrets cluster-wide. Real path: one Role per team namespace.
- Minter runs every minute as a CronJob. A controller with a watch would react in seconds.
- Argo pulls from public repos with no credentials. Private repos need a repo Secret.
- LLM traffic still goes to LiteLLM directly. It could also route through
  agentgateway so one data plane sees everything.
- kagent human approval flow.
- Hostname egress allow-lists. Plain NetworkPolicy cannot match hostnames.
  Needs Cilium `toFQDNs` or an egress proxy.

## Run it

Needs Docker, kind, helm, kubectl, python3 with PyYAML, and Ollama with
`gemma4:12b` pulled and running on the host.

```
make up
make deploy AGENT=intake/examples/faq-helper.yaml
make deploy AGENT=intake/examples/vibe-app.yaml      # build sample-app first, see below
make deploy AGENT=intake/examples/data-cruncher.yaml
make status
make down
```

Build the sample app once:

```
docker build -t spike/vibe-app:0.1.0 sample-app && kind load docker-image spike/vibe-app:0.1.0 --name agent-spike
```

Talk to the kagent agent:

```
kubectl --context kind-agent-spike -n kagent port-forward svc/kagent-controller 18083:8083 &
curl -s http://127.0.0.1:18083/api/a2a/team-demo/faq-helper/ -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":"1","method":"message/send","params":{"message":{"role":"user","messageId":"m1","kind":"message","parts":[{"kind":"text","text":"How many pods run in team-demo?"}]}}}'
```

Deploy the CI-built image from the template repo:

```
make deploy AGENT=intake/examples/ci-app.yaml
```

Talk to the container agent:

```
kubectl --context kind-agent-spike -n team-demo port-forward svc/vibe-app 18080:80 &
curl -s -X POST http://127.0.0.1:18080/chat -H 'Content-Type: application/json' -d '{"message":"hi"}'
```

## Layout

```
charts/agent/     the dev chart. Same content as agent-deployments/charts/agent   (platform-owned)
charts/agent-app/ first-pass chart, superseded
platform/         LLM gateway, tool gateway, minter, Argo wiring, kagent tool sharing, sandbox template
intake/           first-pass contract and renderer, superseded
sample-app/   a stdlib Python "vibe-coded" agent                 (stand-in for a dev's app)
out/          rendered manifests, one dir per agent              (generated)
```

## Versions used

kind v1.36.1 node, kagent 0.10.0, agent-sandbox v1.0.1, LiteLLM
`litellm-database:v1.90.2`, Gateway API v1.6.0, agentgateway v1.5.0, Ollama
gemma4:12b.

## Related repos

- [agent-app-template](https://github.com/dackota/agent-app-template): public template a dev copies.
- [agent-golden-path-workflows](https://github.com/dackota/agent-golden-path-workflows): public shared build workflow.
- [agent-deployments](https://github.com/dackota/agent-deployments): public GitOps repo. PR here to deploy.
