"""Tests for the minter's garbage collection plan.

gc_plan is pure: it takes what the cluster holds and returns what to delete.
The HTTP calls stay in gc(), which is not covered here.
Run with: python3 -m unittest discover -s tests -v
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "platform", "minter"))
import minter  # noqa: E402


def secret(ns, agent):
    return {"metadata": {"name": f"{agent}-platform-credentials", "namespace": ns,
                         "labels": {"goldenpath.dev/minted": "true",
                                    "goldenpath.dev/agent-name": agent}}}


def policy(ns, agent):
    return {"metadata": {"name": f"tools-allow-{ns}-{agent}", "namespace": "platform-gateway",
                         "labels": {"goldenpath.dev/minted": "true",
                                    "goldenpath.dev/agent-ns": ns}}}


def kinds(plan):
    return sorted(d.kind for d in plan)


class GcPlan(unittest.TestCase):

    def test_plans_nothing_when_every_object_has_a_live_registration(self):
        live = {("team-demo", "vibe-app")}
        plan = minter.gc_plan([secret("team-demo", "vibe-app")],
                              [policy("team-demo", "vibe-app")], live)
        self.assertEqual(plan, [])

    def test_orphan_secret_plans_the_secret_and_its_model_key(self):
        plan = minter.gc_plan([secret("team-demo", "vibe-app")], [], live=set())
        self.assertEqual(kinds(plan), ["llm-key", "secret"])

    def test_orphan_secret_names_the_key_by_its_alias(self):
        plan = minter.gc_plan([secret("team-demo", "vibe-app")], [], live=set())
        key = [d for d in plan if d.kind == "llm-key"][0]
        self.assertEqual(key.name, "team-demo/vibe-app")

    def test_orphan_policy_plans_a_policy_deletion(self):
        plan = minter.gc_plan([], [policy("team-ops", "orders-agent")], live=set())
        self.assertEqual(kinds(plan), ["policy"])
        self.assertEqual(plan[0].name, "tools-allow-team-ops-orders-agent")

    def test_a_live_agent_keeps_its_model_key(self):
        """The defect this suite was written for. A live agent must not lose its key."""
        live = {("team-demo", "vibe-app")}
        plan = minter.gc_plan([secret("team-demo", "vibe-app")], [], live)
        self.assertNotIn("llm-key", kinds(plan))

    def test_deleting_one_agent_leaves_another_alone(self):
        live = {("team-demo", "keeper")}
        plan = minter.gc_plan([secret("team-demo", "keeper"), secret("team-demo", "goner")],
                              [], live)
        self.assertEqual({d.name for d in plan},
                         {"goner-platform-credentials", "team-demo/goner"})


class GcPlanRejectsMalformedInput(unittest.TestCase):
    """The planner reads labels an operator can edit. It must never delete a guess."""

    def test_secret_without_an_agent_label_plans_nothing(self):
        s = secret("team-demo", "vibe-app")
        del s["metadata"]["labels"]["goldenpath.dev/agent-name"]
        self.assertEqual(minter.gc_plan([s], [], live=set()), [])

    def test_policy_without_a_namespace_label_plans_nothing(self):
        p = policy("team-demo", "vibe-app")
        del p["metadata"]["labels"]["goldenpath.dev/agent-ns"]
        self.assertEqual(minter.gc_plan([], [p], live=set()), [])

    def test_no_planned_alias_is_ever_partly_empty(self):
        """Every alias the plan emits must be <namespace>/<agent>, both present."""
        cases = [secret("", "vibe-app"), secret("team-demo", ""), secret("team-demo", "ok")]
        for s in cases:
            for d in minter.gc_plan([s], [], live=set()):
                if d.kind == "llm-key":
                    ns, _, agent = d.name.partition("/")
                    self.assertTrue(ns and agent, f"bad alias {d.name!r}")


if __name__ == "__main__":
    unittest.main()
