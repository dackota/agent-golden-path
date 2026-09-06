# 0007: What goes in the chart and what stays out

Date: 2026-09-06

## Decision

In the chart: anything one agent needs and nobody else shares. Deployment,
Service, route, autoscaler, disruption budget, disk, network policy, service
account, prompt ConfigMap, registration, kagent Agent, sandbox claim.

Out of the chart: anything shared or privileged. Gateways, JWKS, the minter,
tool servers, sandbox templates and pools, Argo projects, namespace labels,
credentials.

## Why

The chart is deployed by teams under an AppProject that forbids
cluster-scoped and privileged resources. If a shared piece were in the chart,
the second team would collide with the first, or the project would have to
be loosened. Keeping the line sharp is what lets the schema be strict.
