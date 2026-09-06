# A golden path for deploying AI agents

Date: 2026-09-05

## Summary

There is no single industry standard for deploying LLM agents the way there is for deploying stateless web services. What exists is a set of converging pieces: MCP for tools with OAuth 2.1 authorization ([MCP spec](https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization)), OpenTelemetry GenAI conventions for tracing ([OTel](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-spans.md)), the OWASP LLM and Agentic Top 10 lists for threats ([OWASP](https://genai.owasp.org/llm-top-10/)), and hosted runtimes from AWS, Google, Azure, and Anthropic that bundle identity, sandboxing, and gateways. Companies that run agents at scale (Uber, LinkedIn, Stripe, Spotify, Block) did not adopt a vendor runtime. They built a thin layer on infrastructure they already had: a gateway in front of models and tools, workload identity for agents, a registry, and sandboxed execution. Recommendation: build the same thin layer here using the org's existing pattern (Helm chart, hardened base image, shared GitHub Actions), adopt an open source gateway instead of writing one, and keep hosted runtimes as an opt-in for teams that need managed sandboxes. Details and a phased plan are in part 4.

## Part 1: what the standard should encode

### Identity and auth

- Give every agent its own workload identity. Uber issues each agent workload a SPIFFE SVID from SPIRE, then a Security Token Service mints a short-lived, single-hop JWT with a specific audience and an `act_chain` claim that records the human and every agent in the delegation chain. P99 token exchange is under 40 ms ([Uber, May 2026](https://www.uber.com/us/en/blog/solving-the-agent-identity-crisis/)).
- Use OAuth 2.1 for tools. The current MCP spec (2026-07-28) makes the MCP server an OAuth 2.1 resource server. Servers MUST publish Protected Resource Metadata (RFC 9728), clients MUST send the RFC 8707 `resource` parameter, servers MUST validate token audience, and servers MUST NOT pass a client's token through to upstream APIs. Client ID Metadata Documents are SHOULD; Dynamic Client Registration is MAY and deprecated. Stdio servers SHOULD NOT use this flow and should read credentials from the environment ([MCP authorization](https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization), [versioning](https://modelcontextprotocol.io/specification/versioning)).
- Separate autonomous agents (own system identity) from user-proxy agents (delegated user credentials), and scope tokens to least privilege ([Salesforce Engineering](https://engineering.salesforce.com/building-an-enterprise-agent-platform-enforcing-identity-data-and-api-governance/)).
- Hosted runtimes do this for you: AgentCore assigns each agent a workload identity and keeps outbound OAuth tokens in a Token Vault ([AWS](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-oauth.html)); Azure Foundry gives each hosted agent a dedicated Entra identity ([Microsoft](https://learn.microsoft.com/en-us/azure/foundry/agents/overview)); Vertex Agent Engine uses IAM agent identity ([Google](https://docs.cloud.google.com/vertex-ai/generative-ai/docs/agent-engine/overview)).

### Secrets

Keep model and tool credentials out of the agent container. Anthropic's hosting guidance: supply the API key from a secret manager or route model calls through a proxy that injects it, put inbound auth at a gateway so the agent never validates user tokens, and route outbound tool calls through a proxy that adds credentials after the request leaves the container ([Claude Agent SDK hosting](https://code.claude.com/docs/en/agent-sdk/hosting)). Uber's MCP Gateway plays this role centrally ([Uber](https://www.uber.com/us/en/blog/solving-the-agent-identity-crisis/)).

### Tool and MCP permission scoping

- Allow-list tools per agent and per environment. Block's goose supports a YAML allow-list; an extension whose install command is not listed is rejected ([goose docs](https://block.github.io/goose/docs/guides/allowlist/)).
- Request minimal scopes. MCP servers SHOULD return the required `scope` in the 401 challenge and clients SHOULD follow least privilege with step-up authorization ([MCP](https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization)).
- Keep tool counts small. Shopify saw tool boundaries blur at 20 to 50 tools and moved to "just-in-time instructions" returned with tool results ([Shopify, Aug 2025](https://shopify.engineering/building-production-ready-agentic-systems)).
- OWASP Agentic Top 10 (Dec 2025) names the risks this addresses: ASI02 Tool Misuse, ASI03 Identity and Privilege Abuse, ASI04 Agentic Supply Chain ([OWASP](https://genai.owasp.org/2025/12/09/owasp-top-10-for-agentic-applications-the-benchmark-for-agentic-security-in-the-age-of-autonomous-ai/)).

### Observability

- Use the OTel GenAI semantic conventions. Status is Development, not Stable. Required span attributes are `gen_ai.operation.name` and `gen_ai.provider.name`; `gen_ai.request.model`, `gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens`, and `gen_ai.conversation.id` are recommended. Operation names include `chat`, `embeddings`, `execute_tool`, and `invoke_agent`. Message content attributes are opt-in because they hold PII ([gen-ai-spans](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-spans.md), [agent spans](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-agent-spans.md)). Metrics are `gen_ai.client.token.usage` and `gen_ai.client.operation.duration` ([OTel blog](https://opentelemetry.io/blog/2026/genai-observability/)).
- LinkedIn uses LangSmith before production and OTel spans in production for LLM calls, tool calls, and memory use ([LinkedIn, Sept 2025](https://www.linkedin.com/blog/engineering/generative-ai/the-linkedin-generative-ai-application-tech-stack-extending-to-build-ai-agents)). Datadog ingests OTel GenAI spans natively ([Datadog](https://www.datadoghq.com/blog/llm-otel-semantic-convention/)).

### Evals and guardrails

- OWASP LLM Top 10 2025: LLM01 Prompt Injection, LLM05 Improper Output Handling, LLM06 Excessive Agency, LLM10 Unbounded Consumption ([OWASP](https://genai.owasp.org/llm-top-10/)).
- OpenAI lists guardrail types: relevance classifier, safety classifier, PII filter, moderation, tool safeguards, rules-based protections, output validation ([OpenAI guide](https://cdn.openai.com/business-guides-and-resources/a-practical-guide-to-building-agents.pdf)).
- Gate releases on evals. Shopify calibrated LLM judges against human labels with Cohen's kappa (0.02 to 0.61, human baseline 0.69), built ground truth from sampled production conversations, and found models gaming the judge ([Shopify](https://shopify.engineering/building-production-ready-agentic-systems)). Promptfoo runs evals and prompt-injection red teams in GitHub Actions ([promptfoo](https://www.promptfoo.dev/docs/integrations/github-action/)).
- NIST AI RMF 1.0 gives the governance frame: Govern, Map, Measure, Manage ([NIST](https://www.nist.gov/itl/ai-risk-management-framework)).

### Cost controls

Token cost dominates. Anthropic states token spend typically exceeds container cost by an order of magnitude; a minimal container is about $0.05 per hour while one long session can spend dollars ([Claude Agent SDK hosting](https://code.claude.com/docs/en/agent-sdk/hosting)). Gateways enforce budgets: LiteLLM sets hard budgets and rate limits per virtual key, team, and model ([LiteLLM](https://docs.litellm.ai/docs/proxy/virtual_keys)); Envoy AI Gateway adds token rate limits and a QuotaPolicy with CEL cost expressions that weight input, output, cached, and reasoning tokens ([Envoy AI Gateway](https://aigateway.envoyproxy.io/docs/next/capabilities/traffic/quota-policy/)); Cloudflare AI Gateway has spend limits and caching ([Cloudflare](https://developers.cloudflare.com/ai-gateway/)). Bound turns: the Claude Agent SDK has no session timeout, so set `maxTurns` ([hosting](https://code.claude.com/docs/en/agent-sdk/hosting)).

### Sandboxing and egress

Run tool execution in an isolated container with an egress proxy that enforces domain allow-lists and logs requests ([Claude Agent SDK hosting](https://code.claude.com/docs/en/agent-sdk/hosting)). Stripe runs its coding agents in "devboxes" with no internet or production access ([Stripe, Feb 2026](https://stripe.dev/blog/minions-stripes-one-shot-end-to-end-coding-agents)). On Kubernetes, `agent-sandbox` (SIG Apps, `v1beta1`) provides Sandbox, SandboxTemplate, SandboxClaim, and SandboxWarmPool CRDs and delegates isolation to gVisor or Kata via RuntimeClass ([kubernetes-sigs](https://github.com/kubernetes-sigs/agent-sandbox/blob/main/README.md)). Anthropic's `sandbox-runtime` restricts filesystem and network at the OS level using Seatbelt on macOS and bubblewrap on Linux ([anthropic-experimental](https://github.com/anthropic-experimental/sandbox-runtime)). OWASP ASI05 is Unexpected Code Execution ([OWASP](https://genai.owasp.org/2025/12/09/owasp-top-10-for-agentic-applications-the-benchmark-for-agentic-security-in-the-age-of-autonomous-ai/)).

### Versioning

Version prompts, models, and tool schemas together. Azure Foundry snapshots agent versions and supports rollback; Toolboxes version tool sets behind one MCP endpoint ([Microsoft](https://learn.microsoft.com/en-us/azure/foundry/agents/overview)). Databricks logs agents with MLflow and registers them in Unity Catalog ([Databricks](https://www.databricks.com/blog/announcing-mosaic-ai-agent-framework-and-agent-evaluation)). Pin the SDK: the Claude Agent SDK bundles a CLI binary pinned to the package version ([hosting](https://code.claude.com/docs/en/agent-sdk/hosting)).

### Human in the loop and audit

OpenAI names two triggers for human intervention: exceeding failure thresholds and high-risk or irreversible actions ([OpenAI guide](https://cdn.openai.com/business-guides-and-resources/a-practical-guide-to-building-agents.pdf)). LinkedIn agents ask for clarification or approval at key decision points ([LinkedIn](https://www.linkedin.com/blog/engineering/generative-ai/the-linkedin-generative-ai-application-tech-stack-extending-to-build-ai-agents)). Stripe and Spotify require human code review before merge ([Stripe](https://stripe.dev/blog/minions-stripes-one-shot-end-to-end-coding-agents), [Spotify](https://engineering.atspotify.com/2025/11/spotifys-background-coding-agent-part-1)). For audit, Uber's gateway attributes every action and authorization decision to the full actor chain ([Uber](https://www.uber.com/us/en/blog/solving-the-agent-identity-crisis/)); Salesforce aggregates every agent interaction as trace data ([Salesforce](https://engineering.salesforce.com/building-an-enterprise-agent-platform-enforcing-identity-data-and-api-governance/)).

### Standards bodies

The Linux Foundation formed the Agentic AI Foundation (AAIF) in December 2025 with MCP, goose, and AGENTS.md as founding projects. Platinum members: AWS, Anthropic, Block, Bloomberg, Cloudflare, Google, Microsoft, OpenAI ([LF press](https://www.linuxfoundation.org/press/linux-foundation-announces-the-formation-of-the-agentic-ai-foundation)). A2A ([LF press](https://www.linuxfoundation.org/press/linux-foundation-launches-the-agent2agent-protocol-project-to-enable-secure-intelligent-communication-between-ai-agents)) and AGNTCY ([LF press](https://www.linuxfoundation.org/press/linux-foundation-welcomes-the-agntcy-project-to-standardize-open-multi-agent-system-infrastructure-and-break-down-ai-agent-silos)) are also LF projects. There is no CNCF working group standard for agent deployment; kagent and kgateway are CNCF sandbox projects ([cncf/sandbox #360](https://github.com/cncf/sandbox/issues/360), [#319](https://github.com/cncf/sandbox/issues/319)).

## Part 2: what other companies are doing

Only first-party sources are reported.

| Company | What they built | Source |
|---|---|---|
| Uber | Agent identity on SPIFFE/SPIRE plus an STS minting per-hop JWTs with delegation chains; an Agent Registry; an MCP Gateway as the single policy enforcement point with tool-level policy and redaction | [Uber blog, May 2026](https://www.uber.com/us/en/blog/solving-the-agent-identity-crisis/) |
| LinkedIn | Agents are gRPC services registered via a build plugin into a skill registry; existing messaging platform is the orchestration backbone; LangGraph for control flow; LangSmith pre-prod, OTel in prod; human-in-the-loop approvals | [LinkedIn Engineering, Sept 2025](https://www.linkedin.com/blog/engineering/generative-ai/the-linkedin-generative-ai-application-tech-stack-extending-to-build-ai-agents) |
| Stripe | "Minions" coding agents: a fork of Block's goose wrapped in blueprints that mix deterministic steps with agent loops; run in devboxes without internet or prod access; MCP to an internal "Toolshed" of 400+ tools; lint, then at most two CI runs; human review; 1,000+ merged PRs per week | [Stripe, Feb 2026](https://stripe.dev/blog/minions-stripes-one-shot-end-to-end-coding-agents) |
| Spotify | "Honk" background coding agent on top of Fleet Management; a small internal CLI delegates prompts to an agent and runs lint via local MCP; LLM-as-judge on diffs; central handling of commit tagging, LLM quotas, and traces; 1,500+ merged PRs; guardrails and sandboxing called out as unfinished | [Spotify, Nov 2025](https://engineering.atspotify.com/2025/11/spotifys-background-coding-agent-part-1), [part 4](https://engineering.atspotify.com/2026/4/background-coding-agents-dataset-migrations-honk-part-4) |
| Shopify | Sidekick: just-in-time instructions, small tool sets, calibrated LLM judges, production-sampled ground truth; Roast, an open source workflow framework for structured AI steps | [Shopify](https://shopify.engineering/building-production-ready-agentic-systems), [Roast](https://shopify.engineering/introducing-roast) |
| Block | goose, an open source MCP-based agent, with a corporate extension allow-list; goose moved to AAIF | [Block](https://block.xyz/inside/block-open-source-introduces-codename-goose), [allowlist](https://block.github.io/goose/docs/guides/allowlist/), [AAIF move](https://goose-docs.ai/blog/2026/04/07/goose-moves-to-aaif/) |
| Salesforce | Agentforce: identity propagation through multi-system workflows, system identity for autonomous agents vs delegated identity for user-proxy agents, least-privilege tokens, MuleSoft as API policy layer, traces in Data 360 | [Salesforce Engineering](https://engineering.salesforce.com/building-an-enterprise-agent-platform-enforcing-identity-data-and-api-governance/) |
| Anthropic | Internal teams use Claude Code for automation; the public hosting guide describes one subprocess per session, egress proxies, and OTel export | [Anthropic](https://claude.com/blog/how-anthropic-teams-use-claude-code), [hosting](https://code.claude.com/docs/en/agent-sdk/hosting) |
| Cloudflare | Agents SDK: each agent is a Durable Object with its own state, storage, and scheduler; hibernates when idle | [Cloudflare](https://developers.cloudflare.com/agents/) |
| Vercel | AI SDK 7 `WorkflowAgent` for durable, resumable agents with tool approvals on Vercel Workflows | [Vercel](https://vercel.com/blog/ai-sdk-7) |
| Replit | Defense in depth across dev sandbox and production for agent-generated code | [Replit](https://blog.replit.com/defense-in-depth-how-replit-secures-every-layer-of-the-vibe-coding-stack) |
| Databricks | Agents built with any framework, logged with MLflow, registered in Unity Catalog, deployed to Model Serving | [Databricks](https://www.databricks.com/blog/announcing-mosaic-ai-agent-framework-and-agent-evaluation) |
| Snowflake | Cortex Agents: data access governed by existing Snowflake roles and each tool's execution context | [Snowflake docs](https://docs.snowflake.com/en/user-guide/snowflake-cortex/cortex-agents) |
| Netflix | In-house LLM serving (title only; page blocked fetch, details unverified) | [Netflix TechBlog, July 2026](https://netflixtechblog.com/in-house-llm-serving-at-netflix-a5a8e799ea2c) |
| Airbnb | No first-party post on an internal agent deployment standard found. Viaduct release notes mention AGENTS.md in generated skeletons (unverified) | [Viaduct](https://viaduct.airbnb.tech/blog/2026/05/13/viaduct-10-whats-new) |
| Microsoft, Google, Amazon | No first-party post about their internal agent platform found in this pass; product docs are in part 3 | |

Pattern across Uber, LinkedIn, Stripe, and Spotify: reuse existing platform pieces (service mesh identity, messaging, CI, dev environments, MCP tool catalog), add a gateway and registry, and keep humans in the merge or approval path.

## Part 3: existing solutions

### Hosted agent runtimes

| Product | Layer | K8s story | Auth | Observability | Maturity | Cost signal |
|---|---|---|---|---|---|---|
| [AWS Bedrock AgentCore](https://aws.amazon.com/bedrock/agentcore/pricing/) | Runtime (microVMs), Gateway (MCP + APIs to tools), Identity, Memory, Policy, Evaluations, Browser, Code Interpreter | None; AWS-managed | Inbound JWT/OAuth; workload identity per agent; Token Vault for outbound OAuth ([docs](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-oauth.html)) | CloudWatch; ADOT OTel SDK ([docs](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/observability-configure.html)) | GA Oct 2025 ([AWS](https://aws.amazon.com/about-aws/whats-new/2025/10/amazon-bedrock-agentcore-available)) | Runtime $0.0895/vCPU-hr, $0.00945/GB-hr; Gateway $0.005/1k invocations; Identity free via Runtime/Gateway; Memory $0.25/1k events; Policy $0.000025/request; Observability at CloudWatch rates |
| [Google Vertex AI Agent Engine](https://docs.cloud.google.com/vertex-ai/generative-ai/docs/agent-engine/overview) | Managed serverless runtime; Sessions; Memory Bank | None; runs ADK, LangGraph, LangChain, A2A, AG2, LlamaIndex, custom | IAM agent identity; service accounts, API keys, OAuth clients | Cloud Trace, Cloud Logging | Runtime billing started Nov 2025 ([release notes](https://docs.cloud.google.com/vertex-ai/generative-ai/docs/release-notes)); GA status not stated on overview page | Per vCPU-hour and GB-hour ([pricing](https://cloud.google.com/vertex-ai/pricing)); exact rates unverified in this pass |
| [Azure Foundry Agent Service](https://learn.microsoft.com/en-us/azure/foundry/agents/overview) | Prompt agents (config only) or hosted agents (your container); Toolboxes as one managed MCP endpoint | None; Foundry-managed containers; BYO VNet with VM-isolated sandbox per session | Dedicated Entra identity per hosted agent; MCP auth via key, managed identity, or OAuth OBO | Application Insights tracing, evals, metrics | Docs dated Aug 2026; A2A endpoint in preview | Per-call inference + tool usage + container compute |
| [Claude Managed Agents](https://platform.claude.com/docs/en/managed-agents/overview) | Agent, Environment, Session, Events; Anthropic sandbox or self-hosted sandbox | None directly; self-hosted sandbox runs on your infra ([docs](https://platform.claude.com/docs/en/managed-agents/self-hosted-sandboxes)) | Claude API key; MCP servers | SSE event stream, server-side history | Beta (`managed-agents-2026-04-01` header); not eligible for ZDR or HIPAA BAA | $0.08 per session-hour plus standard token rates ([pricing](https://platform.claude.com/docs/en/about-claude/pricing)) |
| [OpenAI Agents SDK](https://openai.github.io/openai-agents-python/) | Framework, not a runtime; Responses API is hosted | You host it | Your own | Built-in tracing to OpenAI Traces dashboard; disable with `OPENAI_AGENTS_DISABLE_TRACING=1` ([tracing](https://openai.github.io/openai-agents-python/tracing/)) | Released; guardrails run in parallel with agent ([guardrails](https://openai.github.io/openai-agents-python/guardrails/)) | Token cost only; license unverified |
| [LangSmith Deployment (LangGraph Platform)](https://docs.langchain.com/langsmith/deployment) | Runtime for LangGraph agents | Cloud SaaS; hybrid (SaaS control plane, your data plane, Enterprise); fully self-hosted | Plan-dependent SSO/RBAC | LangSmith tracing | GA ([LangChain](https://www.langchain.com/blog/langgraph-platform-ga)) | Plus $39/seat/month with one small serverless deployment; Enterprise custom ([pricing](https://www.langchain.com/pricing)) |
| [Cloudflare Agents](https://developers.cloudflare.com/agents/) | Runtime on Workers and Durable Objects | None | Cloudflare account; your own | Workers observability | Available | Workers/DO pricing (not checked) |

### Kubernetes-native

| Project | Layer | Auth | Observability | Maturity and license | Notes |
|---|---|---|---|---|---|
| [kagent](https://kagent.dev/docs/kagent/introduction/what-is-kagent) | Operator: Agent, ModelConfig, ToolServer CRDs; runs ADK (Go, Python), LangGraph, CrewAI; A2A between agents | K8s RBAC; GitOps | OTel tracing, Prometheus, structured logs | CNCF sandbox ([#360](https://github.com/cncf/sandbox/issues/360)); Apache 2.0 ([repo](https://github.com/kagent-dev/kagent)) | Created by Solo.io; Helm install; HITL approvals, prompt templating via ConfigMaps (per kagent.dev) |
| [agent-sandbox](https://github.com/kubernetes-sigs/agent-sandbox/blob/main/README.md) | Sandbox CRDs for stateful singleton workloads; gVisor or Kata via RuntimeClass | K8s RBAC | Standard K8s | SIG Apps; `v1beta1`; license unverified (kubernetes-sigs) | Warm pools for fast start |
| [Dapr Agents](https://docs.dapr.io/developing-ai/dapr-agents/) | Python framework; DurableAgent on Dapr Workflows | Dapr security | Dapr tracing | Part of Dapr (CNCF); 1.0 per [Diagrid](https://www.diagrid.io/blog/dapr-agents-1-0-durable-cloud-native-production-ready) | Checkpointed, resumable agent runs |
| [agentgateway](https://github.com/agentgateway/agentgateway) | Data plane for MCP, A2A, and LLM traffic (OpenAI-compatible) plus HTTP/gRPC | JWT, API keys, OAuth; RBAC with CEL | OTel metrics, logs, traces | Linux Foundation; Apache 2.0; Rust | Content filtering hooks (regex, OpenAI moderation, Bedrock Guardrails, Model Armor) |
| [kgateway](https://kgateway.dev/) | Gateway API control plane on Envoy; v2.1 integrates agentgateway and deprecates the Envoy-based AI gateway | Gateway API policies | Envoy telemetry | CNCF sandbox ([#319](https://github.com/cncf/sandbox/issues/319)); [v2.1](https://www.cncf.io/blog/2025/11/18/kgateway-v2-1-is-released/) | Formerly Gloo |
| [Envoy AI Gateway](https://aigateway.envoyproxy.io/docs/) | LLM gateway on Envoy Gateway; token rate limits; QuotaPolicy | Envoy Gateway security policies | Envoy telemetry | [1.0 released](https://aigateway.envoyproxy.io/blog/v1.0-release-announcement/); Envoy project | CEL token cost weighting |
| [KServe](https://kserve.github.io/website/docs/install/overview) | Model serving (LLMInferenceService), not agent runtime; Knative autoscaling and scale to zero | K8s | Standard | Mature | Use only if self-hosting models |

### LLM and MCP gateways (non-K8s-specific)

| Product | Covers | Auth and budgets | Observability | License and cost |
|---|---|---|---|---|
| [LiteLLM](https://docs.litellm.ai/docs/simple_proxy) | OpenAI-compatible proxy to 100+ LLMs | Virtual keys with budgets, rate limits, model allow-lists; Postgres + Redis | Logging, spend tracking | MIT; Enterprise license for SSO over 5 users, audit logs ([enterprise](https://docs.litellm.ai/docs/enterprise)) |
| [Portkey](https://github.com/Portkey-AI/gateway) | Gateway to 1,600+ LLMs; 50+ guardrails; virtual keys | Virtual keys, budgets | Built in | MIT; enterprise gateway merging into OSS in 2.0 |
| [Kong AI Gateway](https://developer.konghq.com/mcp/) | AI Proxy, Semantic Prompt Guard, AI MCP Proxy turns APIs into MCP tools | Kong plugins | Kong analytics | Kong OSS is Apache 2.0; which AI plugins are enterprise-only unverified |
| [Cloudflare AI Gateway](https://developers.cloudflare.com/ai-gateway/) | Caching, rate limiting, fallback; BYOK or Unified Billing | Spend limits | Analytics | Hosted |
| [Traefik Hub MCP Gateway](https://doc.traefik.io/traefik-hub/mcp-gateway/mcp) | OAuth-compliant MCP gateway; task-based access control; session routing | TBAC | OTel metrics and traces | Commercial (Hub) |
| [Docker MCP Gateway](https://github.com/docker/mcp-gateway) | Runs MCP servers as containers; catalog; secrets via Docker Desktop; OAuth | Docker Desktop | Call tracing | Open source; license unverified |
| [Microsoft MCP Gateway](https://github.com/microsoft/mcp-gateway) | Reverse proxy and control plane for MCP servers on Kubernetes; session-affine routing | Adapters | Telemetry hooks | Open source; license unverified |
| [IBM ContextForge](https://github.com/IBM/mcp-context-forge) | Gateway, registry, proxy for MCP, A2A, REST/gRPC; 40+ plugins; multi-cluster with Redis | Auth, rate limits | OTel to Phoenix, Jaeger, Zipkin | Open source; license unverified |
| [Lasso MCP Gateway](https://github.com/lasso-security/mcp-gateway) | Python plugin gateway; token masking, PII (Presidio), prompt injection filters | Plugins | Basic | Open source; license unverified |

### Frameworks and SDKs

| SDK | Language | Deployment guidance | Guardrails and HITL | Observability |
|---|---|---|---|---|
| [Claude Agent SDK](https://code.claude.com/docs/en/agent-sdk/hosting) | Python, TypeScript | One subprocess per session; Docker, Kubernetes, Modal cookbook; 1 GiB RAM, 5 GiB disk, 1 CPU starting point | Permission modes, `canUseTool`, hooks ([permissions](https://platform.claude.com/docs/en/agent-sdk/permissions), [hooks](https://platform.claude.com/docs/en/agent-sdk/hooks)); sandbox mode | OTel via env vars; content excluded by default |
| [OpenAI Agents SDK](https://openai.github.io/openai-agents-python/) | Python, TypeScript | Self-host | Input, output, tool guardrails; handoffs | Built-in tracing |
| [Google ADK](https://google.github.io/adk-docs/deploy/) | Python, Go, Java | `adk deploy` to Agent Engine, Cloud Run, GKE | Callbacks | OTel |
| [Microsoft Agent Framework](https://learn.microsoft.com/en-us/agent-framework/overview/) | .NET, Python | Foundry hosted agents or self-host | Middleware, workflows, HITL state | Telemetry built in; MIT; 1.0 GA date unverified |
| [LangGraph](https://docs.langchain.com/langsmith/deployment) | Python, JS | LangSmith Deployment or self-host | Interrupts | LangSmith, OTel |
| [Pydantic AI](https://pydantic.dev/docs/ai/overview/) | Python | Self-host; Temporal durable execution ([docs](https://pydantic.dev/docs/ai/integrations/durable_execution/temporal/)) | Typed outputs | OTel native; Logfire |
| [CrewAI](https://docs.crewai.com/en/enterprise/introduction) | Python | AMP SaaS or self-hosted | Flows | AMP | MIT framework; AMP custom pricing |
| [Mastra](https://mastra.ai/) | TypeScript | `mastra deploy` to Mastra Cloud or self-host | Workflows, evals | Mastra Cloud logs and traces |
| [Vercel AI SDK](https://ai-sdk.dev/docs/reference/ai-sdk-core/tool-loop-agent) | TypeScript | Vercel or any Node host | `toolApproval`; durable `WorkflowAgent` ([AI SDK 7](https://vercel.com/blog/ai-sdk-7)) | Telemetry hooks |

### Sandboxes

| Option | Isolation | Hosting | Notes |
|---|---|---|---|
| [E2B](https://github.com/e2b-dev/infra) | Firecracker microVMs | SaaS; self-host via Terraform (details unverified) | Apache 2.0 per repo listing (unverified) |
| [Modal Sandboxes](https://modal.com/docs/guide/sandbox-networking) | gVisor | SaaS | Default: no inbound connections, no access to Modal resources |
| [Daytona](https://github.com/daytonaio/daytona) | Dedicated kernel per sandbox | SaaS; source status changed in 2026 (unverified) | SDKs for Python, TypeScript, Go, Java, Ruby |
| [Fly Sprites](https://sprites.dev/api) | Stateful Linux environments with checkpoint and restore | SaaS | Details from secondary sources; unverified |
| [gVisor](https://gvisor.dev/) | User-space kernel | Self-host | Used by Modal and agent-sandbox |
| [Firecracker](https://firecracker-microvm.github.io/) | microVM | Self-host | Used by E2B and AgentCore Runtime |
| [agent-sandbox](https://github.com/kubernetes-sigs/agent-sandbox) | gVisor or Kata via RuntimeClass | Your cluster | Kubernetes-native |
| [Anthropic sandbox-runtime](https://github.com/anthropic-experimental/sandbox-runtime) | OS-level (Seatbelt, bubblewrap) | Inside your container | Research preview; no container needed |

### Observability

| Tool | Self-host | License | OTel |
|---|---|---|---|
| [Langfuse](https://langfuse.com/self-hosting) | Yes | MIT core; `ee/` separate license | OTLP endpoint |
| [Arize Phoenix](https://github.com/arize-ai/phoenix) | Yes | ELv2 | OpenInference on OTel |
| [LangSmith](https://www.langchain.com/pricing) | Enterprise | Commercial | Yes |
| [OpenLLMetry](https://github.com/traceloop/openllmetry) | Library | Apache 2.0 | Native OTel instrumentation |
| [Braintrust](https://www.braintrust.dev/docs/guides/self-hosting) | Data plane in your cloud (Enterprise) | Commercial | Eval CI gates |
| [W&B Weave](https://docs.wandb.ai/weave/guides/tracking/otel) | SaaS | Commercial | OTLP ingest |
| [Datadog LLM Observability](https://docs.datadoghq.com/llm_observability/) | SaaS | Commercial | Native GenAI semconv |
| [Grafana Cloud AI Observability](https://grafana.com/docs/grafana-cloud/monitor-applications/ai-observability/) | SaaS | Commercial | OpenLIT SDK, OTel-native |

### Protocols and registries

- MCP: current version 2026-07-28; governed under AAIF ([versioning](https://modelcontextprotocol.io/specification/versioning)).
- A2A: Linux Foundation; agent cards describe identity, skills, endpoint, and auth requirements ([spec](https://a2a-protocol.org/latest/specification/)); v1.0 with signed agent cards reported March 2026 (unverified).
- AGNTCY: LF; discovery, identity, messaging, observability ([LF](https://www.linuxfoundation.org/press/linux-foundation-welcomes-the-agntcy-project-to-standardize-open-multi-agent-system-infrastructure-and-break-down-ai-agent-silos)).
- AG-UI: event stream between agent backend and UI, by CopilotKit; supported in Microsoft Agent Framework ([AG-UI](https://docs.ag-ui.com/introduction), [Microsoft](https://learn.microsoft.com/en-us/agent-framework/integrations/ag-ui/)).
- MCP Registry: preview since Sept 8, 2025; a meta-registry with reverse-DNS namespaces tied to GitHub accounts or domains ([MCP blog](https://blog.modelcontextprotocol.io/posts/2025-09-08-mcp-registry-preview/)).
- Backstage: `mcp-actions-backend` exposes Backstage actions as MCP tools at `/api/mcp-actions/v1` ([Backstage](https://backstage.io/docs/ai/mcp-actions/)). Azure publishes agents to the Entra Agent Registry ([Microsoft](https://learn.microsoft.com/en-us/azure/foundry/agents/overview)).

## Part 4: adopt vs build

### What "build our own" looks like

**(a) Universal agent Helm chart.** One chart, one `values.yaml` per agent. Knobs: image, model, tool allow-list, budget, egress allow-list, approval policy. Baked in:

- Deployment or StatefulSet (agents hold session state on disk; see the subprocess model in [Claude Agent SDK hosting](https://code.claude.com/docs/en/agent-sdk/hosting)); HPA on concurrent sessions; PDB.
- Workload identity via the cloud IAM binding or SPIFFE. Uber's design is the reference ([Uber](https://www.uber.com/us/en/blog/solving-the-agent-identity-crisis/)).
- NetworkPolicy: default deny egress; allow only the gateway. The gateway holds the model and tool credentials.
- Secret mounts from the secret manager via CSI; no keys in env for tool credentials.
- OTel sidecar or collector endpoint with `OTEL_*` env vars set; content capture off.
- Optional `agent-sandbox` RuntimeClass (gVisor) for agents that run generated code ([agent-sandbox](https://github.com/kubernetes-sigs/agent-sandbox)).
- Resource defaults near 1 CPU, 1 GiB RAM, 5 GiB disk per session ([hosting](https://code.claude.com/docs/en/agent-sdk/hosting)).

**(b) Hardened agent base image.** Python and Node variants on Chainguard/Wolfi or Google distroless, non-root, no shell in the runtime stage, pinned agent SDK versions, nightly rebuild ([Chainguard](https://edu.chainguard.dev/chainguard/chainguard-images/overview/), [distroless](https://github.com/GoogleContainerTools/distroless)). Ship `sandbox-runtime` in the image for OS-level restriction of tool subprocesses ([srt](https://github.com/anthropic-experimental/sandbox-runtime)).

**(c) Shared GitHub Actions.** One reusable workflow: build, SBOM, keyless cosign signing with the job's OIDC token ([Sigstore](https://docs.sigstore.dev/cosign/signing/signing_with_containers/)), eval gate and prompt-injection red team with promptfoo ([promptfoo](https://www.promptfoo.dev/docs/integrations/github-action/)), tool-schema diff check, then promote. Fail the build if eval score drops below the agent's threshold.

**(d) Central LLM and MCP gateway.** Do not write this. Deploy agentgateway (MCP, A2A, LLM in one Rust data plane, JWT/OAuth, CEL RBAC, OTel) behind kgateway if the cluster already uses Gateway API ([agentgateway](https://github.com/agentgateway/agentgateway), [kgateway v2.1](https://www.cncf.io/blog/2025/11/18/kgateway-v2-1-is-released/)), or LiteLLM if the need is only model routing with per-key budgets ([LiteLLM](https://docs.litellm.ai/docs/proxy/virtual_keys)). The gateway is where budgets, allow-lists, redaction, and audit live, which matches what Uber, Stripe (Toolshed), and Salesforce (MuleSoft) did.

### Comparison

| Option | Lock-in | Cost | Ops burden | Security posture | Time to first agent | Portability |
|---|---|---|---|---|---|---|
| Build thin path on K8s (a to d) | Low; open protocols (MCP, OTel, OAuth) | Compute plus tokens; no per-runtime fees | Medium: you run gateway, chart, image, CI | Strong if network policy, identity, and gateway are enforced; you own the audit trail | 4 to 8 weeks for the path; days per agent after | High |
| AgentCore | High (AWS-only APIs for Identity, Gateway, Memory) | Runtime $0.0895/vCPU-hr plus per-call fees on Gateway, Memory, Policy ([pricing](https://aws.amazon.com/bedrock/agentcore/pricing/)) | Low | Strong; microVM isolation, Token Vault | Days | Low |
| Vertex Agent Engine | High (Google) | Per vCPU-hour plus sessions and memory ([pricing](https://cloud.google.com/vertex-ai/pricing)) | Low | VPC-SC, CMEK, IAM agent identity | Days | Low to medium (ADK also runs on GKE) |
| Azure Foundry Agent Service | High (Azure), but hosted agents accept any framework container | Inference plus tool plus container compute | Low | Entra identity per agent, BYO VNet, content filters | Days | Medium |
| Claude Managed Agents | Medium; Anthropic-only models; self-hosted sandbox option keeps data on your infra | $0.08/session-hour plus tokens ([pricing](https://platform.claude.com/docs/en/about-claude/pricing)) | Very low | Managed sandbox; beta; no ZDR or HIPAA BAA yet | Hours | Low |
| kagent operator | Low; CNCF, Apache 2.0 | Compute | Medium; sandbox-stage project | K8s RBAC, OTel; you still need a gateway | 1 to 2 weeks | High |

### Recommendation

Build the thin golden path. The org already ships a universal Helm chart, hardened images, and shared Actions; agents are one more workload type with a stateful process, a gateway dependency, and an eval gate. Adopt rather than build the gateway (agentgateway or LiteLLM) and the sandbox (agent-sandbox with gVisor). Keep Claude Managed Agents with self-hosted sandboxes, and AgentCore, as opt-in for teams whose agent needs long-running managed sandboxes and whose data policy allows it. Treat kagent as a candidate to replace the chart later; it is CNCF sandbox and its CRD set is still moving.

### Phased plan

1. **Weeks 1 to 2: define the contract.** Write `agent.yaml` (model, tool allow-list, budget, egress allow-list, approval rules, eval threshold). Pick one Python and one TypeScript reference SDK. Adopt the OTel GenAI attribute names as the tracing contract even though they are still in Development status.
2. **Weeks 3 to 8: ship the path.** Base images, Helm chart, reusable workflow with eval gate and cosign, gateway with per-agent virtual keys and budgets, default-deny egress. Migrate two pilot agents.
3. **Weeks 9 to 14: identity and tools.** Workload identity per agent; MCP servers behind the gateway with OAuth 2.1 and audience validation; internal tool catalog in the MCP Registry format; Backstage `mcp-actions-backend` for platform actions; approval flow for high-risk tools.
4. **Weeks 15 to 20: hardening and cost.** agent-sandbox for code-executing agents; prompt-injection red team in CI; cost dashboards from `gen_ai.client.token.usage`; audit log retention tied to the actor chain.
5. **Quarterly review.** Re-evaluate kagent, AgentCore, and Managed Agents against the path. Switch only if a hosted option removes more ops burden than the lock-in costs.
