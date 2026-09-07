# 0008: Team budgets live in platform config, and minting fails closed

Date: 2026-09-07

## Decision

`platform/budgets/team-budgets.yaml` holds one monthly dollar cap per team. The
minter reads it, holds a LiteLLM team at that cap, and mints every agent key
inside its team. A team with no entry gets no keys at all.

## Why

A per-agent cap answers "will one agent run up a bill". It does not answer "who
signs for the total". Ten agents at the $500 schema maximum is $5,000 that
nobody approved. LiteLLM already enforces a team cap under the key cap, so the
platform only had to decide where the number lives and who may change it.

It lives outside `deployments/`, so no AppProject can reach it and no team can
raise its own cap. Changing one is a platform pull request.

Failing closed is the whole point. A team that slips through with no cap is
exactly the hole this closes, so an unknown team is an error, not a default.

## Alternative

A default cap for any team not listed. Simpler, and wrong: the failure it
causes is silent, and the number would be a guess applied to teams nobody
reviewed. An annotation on the team namespace was the other option. It puts the
number next to the AppProject, but spreads the answer to "what does everything
cost" across one file per team.
