# Dev guide: ship an agent

## The short version

1. Copy a folder under `deployments/agents/<team>/` to `deployments/agents/<team>/<your-agent>/`.
2. Edit `values.yaml`. Five to ten lines is normal.
3. Open a PR. CI checks your values against the chart schema and tells you the
   exact field if something is wrong.
4. Merge. Argo CD deploys into `team-<team>` and keeps it in sync. Your
   credentials appear within a minute. Pods wait for them.

## Pick a kind

**I have a prompt and want it to use some tools.** `kind: prompt`. No code.
The platform runs it. You get an A2A endpoint at
`http://kagent-controller.kagent:8083/api/a2a/team-<team>/<name>/`.

```yaml
name: faq-helper
team: demo
owner: you@example.com
description: Answers questions about the platform.
kind: prompt
systemPrompt: |
  You answer in two sentences or fewer. If you do not know, say so.
tools:
  - name: k8s-readonly
    approval: true        # a human approves each call
```

**I have an app in a container.** `kind: container`. Start from the
[template repo](https://github.com/dackota/agent-app-template). Your app reads
these env vars and nothing else:

| Var | What it is |
|---|---|
| `LLM_BASE_URL`, `LLM_MODEL`, `LLM_API_KEY` | OpenAI-compatible endpoint, your model name, your budgeted key |
| `TOOLS_URL`, `TOOLS_TOKEN` | MCP endpoint and your token. Only present if you asked for tools |
| `AGENT_SYSTEM_PROMPT` | your `systemPrompt`, so you can change it without a rebuild |
| `PORT` | listen here. Answer `GET /healthz` with 200 |
| `OTEL_*` | tracing target. Use it if your SDK supports OpenTelemetry |

Your URL is `http://<gateway>/agents/<team>/<name>/`. The prefix is stripped
before it reaches you.

**I want my agent to hand some questions to another team's agent.**
Add `{agent: <team>/<name>}` to your tools. The other team adds your team to
`delegation.allowFromTeams` on their agent. Both sides must agree. See
[examples](examples.md).

**My agent runs code it generates.** `kind: codeexec`. You get an isolated
sandbox pod from a warm pool with the same LLM env and a locked-down network.
Set `sandbox.expires` so it cleans itself up.

## Every value

| Value | Default | Notes |
|---|---|---|
| `name` | required | lowercase, 3 to 30 chars |
| `team` | required | deploys to `team-<team>` |
| `owner` | required | your email. Goes on every resource and on the gateway key |
| `description` | required | one line. Shows in the catalog |
| `kind` | `container` | `prompt`, `container`, `codeexec` |
| `model` | `default-chat` | or `default-fast`. Catalog names, never providers |
| `budget.usdPerMonth` | 20 | 1 to 500. Hard stop at the gateway. Ask the platform for more |
| `systemPrompt` | "" | required for `prompt` |
| `tools[].name` | [] | `k8s-readonly`, `grafana`, `web-fetch`, `orders-readonly`, `orders-cancel`, `renovate-prs` |
| `tools[].approval` | false | `prompt` only. Human approves each call |
| `tools[].agent` | | `prompt` only. `team/name` of another agent to delegate to over A2A |
| `delegation.allowFromTeams` | [] | `prompt` only. Teams whose agents may call this one |
| `image` | required for `container` | `ghcr.io/<org>/<repo>:<version>`. No `:latest` |
| `port` | 8080 | |
| `healthPath` | `/healthz` | |
| `command`, `args` | [] | override the image entrypoint. Rare |
| `env` | {} | plain values. Not secrets. Cannot start with `LLM_`, `TOOLS_`, `OTEL_`, `AGENT_` |
| `envFrom` | [] | names of Secrets you own in your namespace |
| `size` | `small` | `small` 0.25 cpu/256Mi, `medium` 1/1Gi, `large` 2/4Gi |
| `replicas` | 1 | 1 to 5 |
| `autoscaling.enabled` | true | HPA on CPU up to `maxReplicas` (default 3) |
| `persistence.enabled` | false | a disk at `/data`. `size` 1 to 20Gi |
| `expose.enabled` | true | the gateway URL |
| `schedule` | "" | cron. Set it and you get a CronJob, not a service |
| `egress` | [] | extra hostnames. Recorded for platform approval. Not yet enforced by name |
| `sandbox.expires` | "" | RFC3339. `codeexec` only |

## What you cannot set

Security context, network policy, gateway URLs, the credentials Secret,
resource limits beyond the presets, image pull policy, service account. These
are the same for every agent on purpose.

## Common questions

**My pod is stuck in `CreateContainerConfigError`.** The credentials Secret is
not there yet. The minter runs every minute. If it stays stuck, the platform
minter logs in `platform-gateway` say why.

**I hit the budget.** Requests return "Budget has been exceeded" from the
gateway. Raise `budget.usdPerMonth` in a PR. The budget resets every 30 days.

**I need a tool that is not in the catalog.** Ask the platform. Adding one is
a PR to `charts/agent/templates/_helpers.tpl` and a tool server behind the
gateway. See the platform guide.

**Can I change the prompt without a rebuild?** Yes. Edit `systemPrompt` and
merge. Pods restart with the new prompt.
