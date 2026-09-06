# 0005: agentgateway with its own control plane

Date: 2026-09-06

## Decision

Install agentgateway v1.5.0 from `cr.agentgateway.dev` as the Gateway API
implementation for tool traffic and for the routes to container agents.

## Why

The research note said "agentgateway behind kgateway" because kgateway 2.1
bundled it. By kgateway 2.4 it did not. agentgateway now ships its own
Gateway API control plane, so there is one fewer moving part. It understands
MCP tool names in CEL policies, which no generic gateway does.

## Alternative

Keep kgateway for HTTP and add a separate MCP proxy. Rejected: two data
planes, two policy languages, and no gain.
