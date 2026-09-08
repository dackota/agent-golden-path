# Agent golden path: the spike

## The problem

Teams are about to put AI agents into production. Agents spend money on model
calls, they call tools that change real systems, and many of the people
building them are not backend engineers. Without a shared path, every team
solves model keys, budgets, tool access, and isolation on its own, and each
solution is a new place for a credential to leak. Every agent someone creates
also starts a bill and a credential relationship that must end when the agent
does.

## The answer

The platform decides these things once. A developer writes one `values.yaml`
and opens a pull request. Argo CD deploys the agent. The platform gives the
agent a budgeted model key, a scoped tool token, a URL, network rules, and an
isolated runtime. The developer never sees a credential, and cannot switch a
guard rail off.

## How to read the tables

The third column names the mechanism and the tool that provides it, with a
link to that tool's documentation. That is what the chosen tools do, whether
or not this repo uses it yet.

The last column says what this proof of concept wired up. A gap there is a
configuration job, not a missing capability. Where a tool cannot do the thing
at all, the row says so.

## Concern, answer, proof

| # | Concern | The mechanism, and the tool that provides it | Evidence | In this proof of concept |
|---|---|---|---|---|
| 1 | Staff will paste model keys into Deployments | The gateway holds every provider key and issues a [virtual key](https://docs.litellm.ai/docs/proxy/virtual_keys) per caller. Pods get the virtual key only | LiteLLM docs. Our test: agent to model host unreachable, agent to gateway 200 | Wired and tested |
| 2 | An agent will run up a bill | LiteLLM caps spend at [key, user, team, and organization level](https://docs.litellm.ai/docs/proxy/users) with `max_budget` and `budget_duration`, caps per model with `model_max_budget`, and fires a `projected_limit_exceeded` alert on `soft_budget` before the hard stop | LiteLLM docs. Our test: a $0.0001 key refused its second call | Per-agent and per-team monthly caps, see row 13. No soft budget and no alert, so the first sign of trouble is a stopped agent |
| 3 | An agent will call a tool it should not | agentgateway applies [MCP authorization](https://agentgateway.dev/docs/standalone/latest/configuration/security/mcp-authn/) per target and filters the tool list. kagent scopes `toolNames` and gates writes with [`requireApproval`](https://kagent.dev/docs/kagent/examples/human-in-the-loop/) | Both docs. Our test: 3 of 18 tools listed, a hidden tool returns "Unknown tool", a cancel stopped in `input-required` | Wired and tested |
| 4 | Non-developers cannot ship a container | kagent's declarative `Agent` resource is a prompt and a tool list. No code, no image | kagent docs. Our test: two agents run from a values file only | Wired and tested |
| 5 | Generated code needs isolation | agent-sandbox runs each sandbox under [gVisor or Kata](https://agent-sandbox.sigs.k8s.io/docs/use-cases/gvisor-isolation/) through `runtimeClassName`, served from a warm pool in milliseconds | agent-sandbox docs. Our test: claim served in seconds, non-root, read-only root, egress blocked | Wired without gVisor. kind on macOS has no gVisor node |
| 6 | Teams will step on each other | One Argo CD AppProject per team pins the source repo and the destination namespace. Kubernetes `ResourceQuota` caps what a namespace may consume | Argo CD docs. Our test: Argo refused a foreign namespace, a foreign repo, and creating a namespace | AppProjects wired and tested. `ResourceQuota` is allowed by the project, but no quota is set |
| 7 | Who built this image? | cosign signs each image without a stored key. Kyverno or the sigstore policy-controller then refuses an unsigned image at admission | sigstore and Kyverno docs. Our test: `cosign verify` with an identity regexp passes | Signing wired. No admission check yet |
| 8 | How do agents reach internal systems? | Wrap the API in an MCP server. agentgateway holds the API credential and injects it per target with [backend authentication](https://agentgateway.dev/docs/standalone/latest/configuration/security/backend-authn/), so the agent never holds it | agentgateway docs. Our test: orders-agent read a real order, and its token sees no Kubernetes tools | Wrapper wired. The gateway injects no backend credential, because the demo API needs none |
| 9 | How do agents work together? | A2A delegation. kagent requires the callee to consent to a cross-namespace caller | kagent docs. Our test: a concierge delegated across namespaces, an unlisted agent was refused | Wired and tested |
| 10 | How do we see cost and behaviour? | kagent emits [OpenTelemetry traces and Prometheus metrics](https://kagent.dev/docs/kagent/observability/tracing) and can ship [every prompt and tool call to a SIEM](https://kagent.dev/docs/kagent/observability/audit-prompts/). agentgateway traces to Langfuse. LiteLLM reports spend per key | All three docs | The chart sets `OTEL_*` variables but no collector runs. Nothing receives the traces |
| 11 | What if an agent credential leaks? | Each key and token is scoped to one agent. LiteLLM keys take a `duration` so they expire, and [`/key/regenerate`](https://docs.litellm.ai/docs/proxy/virtual_keys) rotates one with a grace period. agentgateway can [exchange a short-lived identity token](https://agentgateway.dev/blog/2026-07-12-agentgateway-token-exchange-jwt-assertion-entra-obo/) for a backend credential instead of holding a static one | LiteLLM and agentgateway docs | Scoping wired. No expiry and no rotation. The tool token lives 30 days. LiteLLM key rotation needs an Enterprise licence |
| 12 | Is this lock-in? | Every part is open source and Kubernetes-native. Swap any one of them | The vendor comparison is in the research note | True, with one caveat. LiteLLM key rotation and soft-budget alerts sit behind an [Enterprise licence](https://docs.litellm.ai/docs/enterprise) |

Full test list with commands: [docs/guard-rails.md](docs/guard-rails.md).
Dated build log: [docs/spike-log.md](docs/spike-log.md).

## Provisioning and trust

Provisioning is where trust becomes a bill. Each agent the platform creates
answers three questions: who may create it, who pays for it, and what it may
reach. Each one also starts two lasting things, a spending obligation and a
credential relationship. Both must end when the agent ends.

| # | Concern | The mechanism, and the tool that provides it | Evidence | In this proof of concept |
|---|---|---|---|---|
| 13 | Who may provision what, and at whose expense? | LiteLLM has [organizations and teams](https://docs.litellm.ai/docs/proxy/users) that each carry their own `max_budget`. A key created with a `team_id` spends against the team, so a team cannot pass its own cap however many agents it creates. Argo CD AppProjects decide who may create an agent at all | LiteLLM docs. Our test: Argo refused a foreign namespace and a foreign repo | Wired. `platform/budgets/team-budgets.yaml` holds one cap per team, outside `deployments/` so no team can raise its own. The minter holds the LiteLLM team at that cap and mints every key inside it. A team with no entry gets no keys. Tested: a team capped at $1e-7 refused a key that still had $100 |
| 14 | Third-party services bring their own outages, data rules, and limits | Send every outbound call through agentgateway and keep pods default-deny to everything except the gateway. The gateway becomes the one place that holds the credential, applies timeouts and rate limits, and records the call. Adding a third party is then a platform change, so its data policy and uptime promise get reviewed once | [Credential injection through the gateway](https://agentgateway.dev/blog/2026-07-27-credential-injection-ai-agent-egress-cb4a/) | The `egress` field is written as a pod annotation, not a rule, so nothing is granted. No third party is configured. Keeping a register of each third party's data policy, owner, and uptime promise is a process job that no tool does for you |
| 15 | Agents pile up keys and tokens across many services | agentgateway's [backend authentication](https://agentgateway.dev/docs/standalone/latest/configuration/security/backend-authn/) holds one credential per target and injects it on the way out. It supports a static key, a signed JWT, cloud provider credentials, and OAuth token exchange, and reads secrets from a Kubernetes `secretRef`. The agent presents its own identity and never holds the third-party secret. External Secrets Operator can source those secrets from a vault that rotates them | agentgateway docs | Not used. The current path is `envFrom`, which mounts up to five Secrets a team made by hand. Those have no scoping, no rotation, and no inventory |
| 16 | Deleting the agent must end the obligation | LiteLLM `/key/delete` removes a key, and a key created with a `duration` expires by itself. agent-sandbox does [automatic cleanup after a configurable TTL](https://agent-sandbox.sigs.k8s.io/docs/) and can hibernate an idle sandbox. Argo CD prunes whatever leaves Git | LiteLLM and agent-sandbox docs. Our test: deleting a folder removed the workload, the ConfigMaps, and the minted Secret | The garbage collector deletes the Secret, the gateway policy, and the model key, so no budget survives the agent. Keys still get no `duration`, and sandboxes get no TTL, so an object that escapes the collector never expires by itself |
| 17 | Convenience pushes builders to ask for broad access | Tools come from a platform catalog. agentgateway serves a per-agent allow list, kagent scopes `toolNames`, and writes need `requireApproval`. kagent's [prompt and tool-call audit stream](https://kagent.dev/docs/kagent/observability/audit-prompts/) shows which tools an agent actually calls, so a grant that is never used can be removed | agentgateway and kagent docs. Our test: 3 of 18 tools listed | Catalog and allow list wired. No audit stream, so nothing reports a tool granted but never called. Nobody reviews why a tool is needed |
| 18 | Cost must attach to a behaviour, not just a month | LiteLLM tags each call with the key's metadata, caps spend per model with `model_max_budget`, and for agents adds session controls: `max_budget_per_session`, `max_iterations`, `session_rpm_limit`, and `session_tpm_limit`. A runaway loop then stops inside the task, not at the end of the month | [LiteLLM budgets and rate limits](https://docs.litellm.ai/docs/proxy/users) | A 30-day cap and per-key attribution only. No session budget and no iteration cap. Model spend is counted. Tool and third-party spend is not |

## What you run

```
make up        # kind cluster, platform, Argo CD, demo agents
make status    # watch Argo sync and the minter work
make test      # chart lint, render, and schema negative tests
make down
```

Needs Docker, kind, Helm, kubectl, Python 3 with PyYAML, and Ollama with
`gemma4:12b` pulled.

## Out of scope on purpose

This is a proof of concept on kind, not a product.

- **Agent quality.** No evaluation gate in CI, and no prompt-injection tests.
  The platform controls what an agent may do, not whether it does it well.
- **Intake.** Non-developers still edit YAML. A form, single sign-on, and a
  chat front door are the next piece of work.
- **Third-party register.** Deciding which outside services are allowed, and
  recording each one's data policy and uptime promise, is a governance job.
  The gateway enforces that decision. It does not make it.
- **Scaling.** Pods scale on CPU. Agents wait on the model, so in-flight
  requests are the right signal.
- **Data class.** The intake never asks what data the agent will touch.

## Where to read next

| You are | Read these two |
|---|---|
| A developer shipping an agent | [dev guide](docs/dev-guide.md), [examples](docs/examples.md) |
| A platform engineer | [architecture](docs/architecture.md), [platform guide](docs/platform-guide.md) |
| A security reviewer | [guard rails](docs/guard-rails.md), [decisions](docs/decisions/) |
| Deciding whether to fund this | this page, then [the research note](docs/research/2026-09-05-agent-deployment-golden-path.md) |
