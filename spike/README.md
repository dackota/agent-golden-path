# Spike: self-service golden path for agents on Kubernetes

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
| Their own HTTP app image | `container` | `charts/agent-app` (our chart) | Yes. Chat worked through the gateway. The schema rejected a bad image and a bad model before anything was applied. |
| An agent that runs generated code | `codeexec` | agent-sandbox `SandboxClaim` | Yes. Claimed a warm sandbox in seconds. Non-root, read-only root, egress blocked. |

All three share one central LLM gateway (LiteLLM). It holds the real model
credentials. It needs its own Postgres for keys and budgets, which I did not
expect for a spike, but that is where every guard rail below lives. Each agent gets its own key with a budget and a model
allow-list. No agent pod can reach a model any other way.

## How the self-service path works

1. The dev writes one file, `agent.yaml`. See `intake/examples/`.
2. The platform pipeline runs `make deploy AGENT=<file>`. It:
   - validates the file against `intake/agent.schema.json`. Unknown fields,
     unknown tools, foreign registries, and budgets over the cap are rejected.
   - renders manifests to `out/<name>/`.
   - mints a gateway key with the agent's budget and stores it as a Secret in
     the team namespace.
   - applies the kagent Agent, or the Helm release, or the SandboxClaim.
3. The dev never touches a ModelConfig, a Secret, a NetworkPolicy, or a
   SecurityContext. Those are not knobs.

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

## Not proven here

- gVisor. kind on macOS has no gVisor. The template has the line commented out.
- Workload identity. The ServiceAccount has a placeholder annotation.
- OpenTelemetry. The chart sets `OTEL_*` env vars but no collector is deployed.
- Eval gate and image signing in CI. Out of scope for this spike.
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

Talk to the container agent:

```
kubectl --context kind-agent-spike -n team-demo port-forward svc/vibe-app 18080:80 &
curl -s -X POST http://127.0.0.1:18080/chat -H 'Content-Type: application/json' -d '{"message":"hi"}'
```

## Layout

```
intake/       agent.yaml contract, schema, renderer, examples   (dev-facing)
platform/     gateway, kagent tool sharing, sandbox template     (platform-owned, installed once)
charts/       agent-app universal chart                          (platform-owned)
sample-app/   a stdlib Python "vibe-coded" agent                 (stand-in for a dev's app)
out/          rendered manifests, one dir per agent              (generated)
```

## Versions used

kind v1.36.1 node, kagent 0.10.0, agent-sandbox v1.0.1, LiteLLM
`litellm-database:v1.90.2`, Ollama gemma4:12b.
