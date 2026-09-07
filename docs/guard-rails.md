# Guard rails and how each one was proven

A guard rail counts only if it was tested. Every row below was.

## In the chart schema (fails at PR time)

| Rule | Test | Result |
|---|---|---|
| Images only from allowed registries | `image=docker.io/x/y:1` | rejected |
| No `:latest` | `image=ghcr.io/dackota/x:latest` | rejected |
| `container` needs an image | prompt values with `kind=container` | rejected |
| `prompt` needs a system prompt | codeexec values with `kind=prompt` | rejected |
| Only catalog models | `model=gpt-99` | rejected |
| Only catalog tools | `tools[0].name=shell` | rejected |
| Budget cap | `budget.usdPerMonth=9999` | rejected |
| Replica cap | `replicas=50` | rejected |
| No unknown fields | `privileged=true` | rejected |
| Dev env cannot shadow platform env | `env.LLM_API_KEY=x` | rejected |
| Cron must parse | `schedule="every day"` | rejected |

Run `tests/schema-tests.sh` to repeat all of them.

## In the rendered resources (always on, not values)

| Rule | Where |
|---|---|
| Non-root uid 10001, read-only root, drop all capabilities, seccomp RuntimeDefault | Deployment, CronJob, SandboxTemplate |
| No service account token mounted | `automountServiceAccountToken: false` |
| Default-deny network egress | NetworkPolicy per agent |
| Resource requests and limits | size presets small, medium, large |
| Credentials only from the platform Secret | env `secretKeyRef` to `<name>-platform-credentials` |
| Prompt change restarts pods | `checksum/prompt` annotation |

Proof for egress: an agent pod could not reach the model host directly
(`Network is unreachable`), could reach the gateway (`200`), and a control pod
without the policy reached the model host (`200`).

## At the LLM gateway

| Rule | Test | Result |
|---|---|---|
| No key, no model | request without `Authorization` | 401 |
| Key bound to one model | key for `default-chat` asks for `default-fast` | "key not allowed to access model" |
| Hard budget | key with $0.0001, two calls | second call "Budget has been exceeded" |
| Spend attributed | `/key/info` after one agent call | `spend: 0.0074` on that agent's key |

## At the tool gateway

| Rule | Test | Result |
|---|---|---|
| JWT required | no token, garbage token | 401, 401 |
| Per-agent allow-list | vibe-app token `tools/list` | 3 of 18 tools |
| Hidden tools unreachable | call `k8s_delete_resource` with vibe-app token | "Unknown tool" |
| No policy means no tools | valid JWT for an agent with no tools | `tools/list` empty |
| One gateway, many tool servers, still per-agent | vibe-app token vs orders-agent token on the same endpoint | 3 Kubernetes tools vs 3 order tools, no overlap |
| Human approval | prompt agent with `approval: true` | task stopped in `input-required` before the tool ran |

## At kagent

| Rule | Test | Result |
|---|---|---|
| Shared tool servers opt in per namespace | agent in unlabeled namespace | "cross-namespace reference ... is not allowed" until the platform labeled it |
| Tool names scoped per agent | `toolNames` on the Agent | 3 read-only tools granted out of 18 |
| Delegation needs the callee's consent | Agent in an unlisted namespace names `team-ops/orders-agent` as a tool | "cross-namespace reference to agent team-ops/orders-agent is not allowed" |
| Writes behind approval | `orders-cancel` with `approval: true`, "Cancel order 1004" | stopped in `input-required`, order untouched |

## At Argo CD

| Rule | Test | Result |
|---|---|---|
| Team deploys only to its namespace | Application in `team-demo` aimed at `kagent` | "do not match any of the allowed destinations" |
| Team deploys only from this repo | Application from `github.com/someone-else/evil` | "is not permitted in project" |
| No cluster-scoped resources | `clusterResourceWhitelist: []`, Argo tried to create namespace `team-ops` | "resource :Namespace is not permitted in project team-ops". Namespaces are platform-owned |
| Removal is complete | deleted an agent folder | CronJob, ConfigMaps, and minted Secret gone. The minter also deletes the gateway policy and the model key, so no budget survives the agent |

## In CI

| Rule | Test | Result |
|---|---|---|
| Foreign image in `agent.yaml` | `docker.io/evil/thing:latest` | build fails before docker runs |
| Image tag equals VERSION | mismatch | build fails |
| Image is signed by this workflow | `cosign verify` with identity regexp | green on the first real run |
| Broken chart path | chart committed at the wrong path | validate failed, then passed after the fix |

## Not yet enforced

- Signature verification at admission (Kyverno or policy-controller).
- gVisor on the sandbox nodes. kind on macOS cannot run it.
- Hostname-based egress. Plain NetworkPolicy cannot match names.
- Tool JWT rotation. Tokens live 30 days.
- Minter RBAC is cluster-wide for Secrets. Should be one Role per team namespace.
