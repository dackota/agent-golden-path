# 0006: Argo ApplicationSet plus one AppProject per team

Date: 2026-09-06

## Decision

One git-directory `ApplicationSet` over `deployments/agents/*/*` creates an
Application per agent. Each team gets an `AppProject` that allows only this
repo as a source and only `team-<team>` as a destination, with no
cluster-scoped resources.

## Why

A folder is the unit of deployment, which maps onto "open a PR". The project
turns the namespace boundary into something Argo enforces, so a team cannot
deploy into another team's namespace even if it edits the wrong path. Prune
and self-heal mean the repo is the truth.

## Alternative

One Application per team pointing at a folder of many agents. Rejected: one
bad values file would block the whole team's sync.
