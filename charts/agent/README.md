# agent chart

One chart for every agent on the platform. `kind` decides what renders.

| `kind` | Renders |
|---|---|
| `container` | Deployment or CronJob, Service, HTTPRoute, HPA, PDB (replicas > 1), PVC (optional), NetworkPolicy, ServiceAccount, prompt ConfigMap |
| `prompt` | kagent `Agent` and `ModelConfig` |
| `codeexec` | `SandboxClaim` |
| all | registration ConfigMap the platform minter reads |

Values and defaults are documented inline in [values.yaml](values.yaml). The
schema in [values.schema.json](values.schema.json) is strict: unknown fields,
foreign registries, `:latest`, out-of-range budgets and replicas, and env
names that shadow platform env are all rejected before rendering.

Platform constants (gateway URLs, Secret name, tool catalog, size presets,
security contexts) live in [templates/_helpers.tpl](templates/_helpers.tpl).
They are not values.

```
helm template my-agent . -f ../../deployments/agents/demo/vibe-app/values.yaml
../../tests/schema-tests.sh
```
