# 0001: One chart with a kind switch

Date: 2026-09-05

## Decision

One Helm chart, `charts/agent`, serves all three agent kinds. A `kind` value
selects which templates render. The chart bundles only what a single agent
needs and nothing the platform owns.

## Why

Developers learn one values file. The platform maintains one schema and one
set of guard rails. Kind-specific resources live in their own template
folders, so the chart stays readable.

## Alternative

Three charts, one per kind. Rejected because the shared parts (registration,
labels, budget, tools, owner) would drift, and a dev who moves from a prompt
to a container would start over.
