# 0009: The gateway holds credentials for the APIs behind tools

Date: 2026-09-07

## Decision

When a tool's API needs a credential, agentgateway holds it. The API sits behind
an `AgentgatewayBackend` whose `policies.auth.secretRef` reads a Secret in
`platform-gateway`, and the gateway injects it on the outbound request. The MCP
wrapper calls the gateway with nothing.

## Why

The model key and the tool token were already minted and scoped. Every other
credential was not. An agent that had to call an API with a key would take that
key as an environment variable, and the platform would know nothing about it: no
rotation, no scoping, no inventory, no way to narrow the damage if it leaked.

Putting the credential at the gateway makes the agent side hold nothing. One
place to rotate, one place to audit, and the same allow-list already decides
which agent may reach which tool. It also opens the path to per-agent identity
later, because `auth` supports OAuth token exchange and a signed JWT per
request, so a shared key is not the end state.

## Alternative

Give the MCP wrapper the key. Simpler by one file, and it moves the problem one
hop instead of solving it: the wrapper becomes a pod holding a long-lived shared
credential, and every new API adds another.

`envFrom` stays in the chart. It is the escape hatch for a Secret the platform
does not manage, and the dev guide states plainly what it costs. Removing it was
the other option, and it was rejected because a team can have a legitimate
Secret that the platform has no way to source yet.
