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


class ParseTeamBudgets(unittest.TestCase):
    """The budget map is platform config. Bad input must stop the run, not pass through."""

    def test_reads_a_dollar_cap_per_team(self):
        self.assertEqual(minter.parse_team_budgets({"demo": "200", "ops": "300.50"}),
                         {"demo": 200.0, "ops": 300.5})

    def test_ignores_surrounding_whitespace(self):
        self.assertEqual(minter.parse_team_budgets({"demo": " 200 \n"}), {"demo": 200.0})

    def test_no_teams_configured_is_an_empty_map(self):
        self.assertEqual(minter.parse_team_budgets({}), {})

    def test_rejects_a_cap_that_is_not_a_number(self):
        with self.assertRaises(ValueError) as e:
            minter.parse_team_budgets({"demo": "lots"})
        self.assertIn("demo", str(e.exception))

    def test_rejects_a_cap_of_zero_or_less(self):
        for bad in ("0", "-5"):
            with self.subTest(cap=bad), self.assertRaises(ValueError):
                minter.parse_team_budgets({"demo": bad})


class RequireTeamBudget(unittest.TestCase):
    """An uncapped team is the defect this closes. Minting into one must fail."""

    def test_returns_the_cap_for_a_configured_team(self):
        self.assertEqual(minter.require_team_budget("demo", {"demo": 200.0}), 200.0)

    def test_refuses_a_team_with_no_cap(self):
        with self.assertRaises(RuntimeError) as e:
            minter.require_team_budget("rogue", {"demo": 200.0})
        self.assertIn("rogue", str(e.exception))


class TeamAction(unittest.TestCase):

    def test_creates_a_team_that_does_not_exist(self):
        self.assertEqual(minter.team_action(None, 200.0), "create")

    def test_leaves_a_team_whose_cap_already_matches(self):
        self.assertEqual(minter.team_action({"max_budget": 200.0}, 200.0), "unchanged")

    def test_updates_a_team_whose_cap_changed(self):
        self.assertEqual(minter.team_action({"max_budget": 200.0}, 300.0), "update")

    def test_updates_a_team_that_has_no_cap_at_all(self):
        self.assertEqual(minter.team_action({"max_budget": None}, 200.0), "update")


def registration(**over):
    reg = {"name": "auditor", "team": "demo", "owner": "a@b.co", "kind": "container",
           "model": "default-chat", "usdPerMonth": 5, "toolNames": [], "repos": []}
    reg.update(over)
    return reg


class SecretData(unittest.TestCase):
    """What lands in an agent's credentials Secret, given what was minted."""

    def test_every_agent_gets_the_model_key_and_where_to_send_it(self):
        data = minter.secret_data(registration(), "sk-1", None, None)
        self.assertEqual(data["LLM_API_KEY"], "sk-1")
        self.assertEqual(data["LLM_MODEL"], "default-chat")
        self.assertIn("LLM_BASE_URL", data)

    def test_an_agent_with_no_tools_gets_no_tool_token(self):
        data = minter.secret_data(registration(), "sk-1", "jwt-1", None)
        self.assertNotIn("TOOLS_TOKEN", data)
        self.assertNotIn("TOOLS_URL", data)

    def test_an_agent_with_tools_gets_the_tool_token(self):
        data = minter.secret_data(registration(toolNames=["pr_status"]), "sk-1", "jwt-1", None)
        self.assertEqual(data["TOOLS_TOKEN"], "jwt-1")
        self.assertIn("TOOLS_URL", data)

    def test_an_agent_with_no_repos_gets_no_github_token(self):
        data = minter.secret_data(registration(), "sk-1", None, "gh-1")
        self.assertNotIn("GITHUB_TOKEN", data)

    def test_an_agent_with_repos_gets_the_github_token(self):
        data = minter.secret_data(registration(repos=["acme/widgets"]), "sk-1", None, "gh-1")
        self.assertEqual(data["GITHUB_TOKEN"], "gh-1")

    def test_a_missing_github_token_is_never_written_as_an_empty_value(self):
        """An empty credential reads as present and fails far from here."""
        data = minter.secret_data(registration(repos=["acme/widgets"]), "sk-1", None, "")
        self.assertNotIn("GITHUB_TOKEN", data)

    def test_no_value_in_the_secret_is_ever_empty(self):
        data = minter.secret_data(registration(toolNames=["pr_status"], repos=["o/r"]),
                                  "sk-1", "jwt-1", "gh-1")
        self.assertTrue(all(v for v in data.values()), data)


class KeyBody(unittest.TestCase):
    """The /key/generate request for an agent. Pure, so the budget rules are tested."""

    def test_names_the_agent_and_its_team(self):
        body = minter.key_body(registration(usdPerMonth=10), "team-demo", "team-demo")
        self.assertEqual(body["key_alias"], "team-demo/auditor")
        self.assertEqual(body["team_id"], "team-demo")
        self.assertEqual(body["models"], ["default-chat"])

    def test_hard_budget_is_the_declared_cap(self):
        body = minter.key_body(registration(usdPerMonth=10), "team-demo", "team-demo")
        self.assertEqual(body["max_budget"], 10)
        self.assertEqual(body["budget_duration"], "30d")

    def test_soft_budget_warns_before_the_hard_stop(self):
        """Row 2 of SPIKE.md: the first sign of trouble must not be a stopped agent."""
        body = minter.key_body(registration(usdPerMonth=10), "team-demo", "team-demo")
        self.assertAlmostEqual(body["soft_budget"], 8.0)
        self.assertLess(body["soft_budget"], body["max_budget"])

    def test_soft_budget_stays_below_the_cap_for_any_cap(self):
        for cap in (1, 0.5, 20, 500, 0.0001):
            with self.subTest(cap=cap):
                body = minter.key_body(registration(usdPerMonth=cap), "ns", "t")
                self.assertGreater(body["soft_budget"], 0)
                self.assertLess(body["soft_budget"], body["max_budget"])

    def test_metadata_carries_who_pays(self):
        body = minter.key_body(registration(), "team-demo", "team-demo")
        self.assertEqual(body["metadata"], {"team": "demo", "owner": "a@b.co", "agent": "auditor"})


class MetricsKeyBody(unittest.TestCase):
    """The collector reads LiteLLM /metrics with a key that can do nothing else."""

    def test_cannot_call_any_catalog_model(self):
        body = minter.metrics_key_body()
        self.assertEqual(body["models"], [minter.NO_MODEL])

    def test_has_a_budget_too_small_to_spend(self):
        body = minter.metrics_key_body()
        self.assertGreater(body["max_budget"], 0)
        self.assertLess(body["max_budget"], 0.001)

    def test_is_named_for_what_it_is(self):
        body = minter.metrics_key_body()
        self.assertEqual(body["key_alias"], minter.METRICS_KEY_ALIAS)
        self.assertEqual(body["metadata"]["purpose"], "metrics")
