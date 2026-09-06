# 0002: Gateways hold every credential

Date: 2026-09-05

## Decision

Agent pods never hold a provider key or a tool credential. They hold a
per-agent LiteLLM virtual key and a per-agent JWT. The LLM gateway holds the
provider keys. The tool gateway holds tool access rules. NetworkPolicy makes
the gateways the only reachable endpoints.

## Why

This is where budgets, allow-lists, audit, and revocation can live once
instead of in every agent. It matches what Uber, Stripe, and Salesforce
describe in their published designs. It also means a leaked agent credential
is scoped to one agent, one model, one budget, and one tool list.

## Alternative

Inject provider keys into pods from a secret manager. Rejected: no budget,
no per-agent attribution, and a leak is the whole provider account.
