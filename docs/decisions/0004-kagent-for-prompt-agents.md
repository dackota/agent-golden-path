# 0004: kagent runs prompt-only agents

Date: 2026-09-05

## Decision

`kind: prompt` renders a kagent `Agent` and `ModelConfig`. kagent runs the
agent loop, exposes A2A, and enforces per-tool approval.

## Why

Most people who asked for help had a prompt and a wish list of tools, not a
service. Forcing them to write and ship a container would have failed the
self-service goal. kagent is CNCF sandbox, Apache 2.0, Kubernetes-native, and
already ships the Kubernetes MCP tools that started the catalog.

## Risk

The CRDs are `v1alpha2` and moving. The default install ships demo agents and
a UI with Postgres that a real cluster turns off. BYO mode needs an image
that speaks A2A, which is why container agents use the Helm path instead.
