# 0003: A minter CronJob, not an operator, for now

Date: 2026-09-06

## Decision

The chart renders a registration ConfigMap. A platform CronJob reads all of
them every minute and creates credentials Secrets and tool policies. It also
garbage-collects both when a registration disappears.

## Why

GitOps needs something to turn "an agent exists" into "an agent has
credentials" without a human. Eighty lines of stdlib Python in a CronJob
proved the flow in an hour and has no dependencies. Everything it does is
idempotent, so running it often is safe.

## Alternative

A proper controller with a watch, or External Secrets Operator with a secret
manager. Both are better for a real cluster and are listed in the platform
guide. The CronJob is a stepping stone with the same contract: the
registration ConfigMap and the Secret name.
