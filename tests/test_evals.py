"""Tests for the evals generator and for eval coverage of the deployed agents.

evals/build.py turns each agent's `evals` list into a promptfoo config. The
pure parts are tested here. Running promptfoo itself happens in CI and in
the nightly job, not here.
Run with: python3 -m unittest discover -s tests -v
"""
import glob
import os
import sys
import unittest

import yaml

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "evals"))
import build  # noqa: E402


def agent(**over):
    a = {"name": "faq-helper", "team": "demo", "kind": "prompt", "model": "default-chat",
         "description": "Answers questions.", "systemPrompt": "You are terse.",
         "evals": [{"input": "hi", "assert": [{"type": "icontains", "value": "hello"}]}]}
    a.update(over)
    return a


class DiscoverAgents(unittest.TestCase):

    def test_reads_every_deployed_values_file(self):
        agents = build.discover(os.path.join(ROOT, "deployments", "agents"))
        self.assertEqual(sorted(a["name"] for a in agents),
                         sorted(os.path.basename(os.path.dirname(v)) for v in
                                glob.glob(os.path.join(ROOT, "deployments/agents/*/*/values.yaml"))))

    def test_carries_the_fields_the_config_needs(self):
        a = next(x for x in build.discover(os.path.join(ROOT, "deployments", "agents"))
                 if x["name"] == "faq-helper")
        for k in ("team", "kind", "model", "systemPrompt", "evals"):
            self.assertIn(k, a)


class Coverage(unittest.TestCase):
    """Every agent with a system prompt ships at least one eval. This is the PR gate."""

    def test_every_agent_with_a_prompt_has_evals(self):
        for a in build.discover(os.path.join(ROOT, "deployments", "agents")):
            if a.get("systemPrompt"):
                with self.subTest(agent=a["name"]):
                    self.assertTrue(a.get("evals"), f"{a['name']} has a systemPrompt but no evals")

    def test_every_eval_has_an_input_and_an_assert(self):
        for a in build.discover(os.path.join(ROOT, "deployments", "agents")):
            for e in a.get("evals") or []:
                with self.subTest(agent=a["name"], input=e.get("input", "")[:30]):
                    self.assertTrue(e.get("input"))
                    self.assertTrue(e.get("assert"))


class Tests(unittest.TestCase):

    def test_maps_input_to_the_prompt_var(self):
        t = build.tests_for(agent(), "prompt")
        self.assertEqual(t[-1]["vars"], {"input": "hi"})
        self.assertEqual(t[-1]["assert"], [{"type": "icontains", "value": "hello"}])

    def test_every_agent_gets_the_injection_canary_first(self):
        t = build.tests_for(agent(evals=[]), "prompt")
        self.assertEqual(len(t), len(build.BASELINE_TESTS))
        self.assertIn("PWNED", t[0]["vars"]["input"])
        self.assertEqual(t[0]["assert"][0]["type"], "not-icontains")

    def test_a_test_marked_for_one_mode_runs_only_there(self):
        a = agent(evals=[{"input": "live only", "mode": "live", "assert": [{"type": "regex", "value": "x"}]},
                         {"input": "both", "assert": [{"type": "regex", "value": "x"}]}])
        prompt_inputs = [t["vars"]["input"] for t in build.tests_for(a, "prompt")]
        live_inputs = [t["vars"]["input"] for t in build.tests_for(a, "live")]
        self.assertNotIn("live only", prompt_inputs)
        self.assertIn("live only", live_inputs)
        self.assertIn("both", prompt_inputs)


class Providers(unittest.TestCase):

    def test_prompt_mode_talks_to_the_llm_gateway_as_the_agent_model(self):
        p = build.provider(agent(), "prompt", build.Endpoints(llm="http://litellm:4000/v1"))
        self.assertEqual(p["id"], "openai:chat:default-chat")
        self.assertEqual(p["config"]["apiBaseUrl"], "http://litellm:4000/v1")
        self.assertEqual(p["config"]["apiKeyEnvar"], build.KEY_ENV)

    def test_prompt_mode_can_override_the_model(self):
        p = build.provider(agent(), "prompt", build.Endpoints(model="deep-reason"))
        self.assertEqual(p["id"], "openai:chat:deep-reason")

    def test_a_laptop_run_talks_to_ollama_with_thinking_off(self):
        p = build.provider(agent(), "prompt", build.Endpoints(ollama="http://localhost:11434", model="gemma4:12b"))
        self.assertEqual(p["id"], "ollama:chat:gemma4:12b")
        self.assertIs(p["config"]["think"], False)

    def test_live_mode_hits_a_container_agent_through_the_gateway(self):
        p = build.provider(agent(name="vibe-app", kind="container"), "live",
                           build.Endpoints(gateway="http://agentgateway-proxy"))
        self.assertEqual(p["id"], "https")
        self.assertEqual(p["config"]["url"], "http://agentgateway-proxy/agents/demo/vibe-app/chat")
        self.assertEqual(p["config"]["body"], {"message": "{{input}}"})
        self.assertEqual(p["config"]["transformResponse"], "json.reply")

    def test_live_mode_hits_a_prompt_agent_over_a2a(self):
        p = build.provider(agent(), "live", build.Endpoints())
        self.assertEqual(p["config"]["url"], "http://faq-helper.team-demo.svc.cluster.local:8080/")
        self.assertEqual(p["config"]["body"]["method"], "message/send")
        self.assertIn("{{input}}", str(p["config"]["body"]))
        self.assertIn("parts[0].text", p["config"]["transformResponse"])

    def test_codeexec_agents_have_no_chat_endpoint(self):
        with self.assertRaises(build.NoEndpoint):
            build.provider(agent(kind="codeexec"), "live", build.Endpoints())


class Config(unittest.TestCase):

    def test_prompt_mode_embeds_the_system_prompt_as_a_chat_prompt(self):
        c = build.config(agent(), "prompt", build.Endpoints())
        msgs = build.chat_prompt(agent())
        self.assertEqual(msgs[0], {"role": "system", "content": "You are terse."})
        self.assertEqual(msgs[1], {"role": "user", "content": "{{input}}"})
        self.assertEqual(c["prompts"], ["file://prompt.json"])

    def test_live_mode_sends_the_input_raw(self):
        c = build.config(agent(), "live", build.Endpoints())
        self.assertEqual(c["prompts"], ["{{input}}"])

    def test_grader_is_the_platform_model_through_the_gateway(self):
        c = build.config(agent(), "prompt", build.Endpoints(llm="http://litellm:4000/v1", grader="default-fast"))
        g = c["defaultTest"]["options"]["provider"]
        self.assertEqual(g["id"], "openai:chat:default-fast")
        self.assertEqual(g["config"]["apiBaseUrl"], "http://litellm:4000/v1")

    def test_redteam_config_names_the_agent_purpose_and_small_test_count(self):
        r = build.redteam_config(agent(), "prompt", build.Endpoints())
        self.assertIn("Answers questions.", r["redteam"]["purpose"])
        self.assertLessEqual(r["redteam"]["numTests"], 3)
        ids = [p if isinstance(p, str) else p["id"] for p in r["redteam"]["plugins"]]
        self.assertEqual(ids, build.REDTEAM_PLUGINS)
        self.assertEqual(r["targets"][0]["id"], "openai:chat:default-chat")

    def test_prompt_extraction_would_carry_the_real_prompt(self):
        entries = build.redteam_plugins(agent())
        self.assertTrue(all(isinstance(e, str) for e in entries))
        build.REDTEAM_PLUGINS.append("prompt-extraction")
        try:
            extraction = next(p for p in build.redteam_plugins(agent()) if isinstance(p, dict))
            self.assertEqual(extraction["config"]["systemPrompt"], "You are terse.")
        finally:
            build.REDTEAM_PLUGINS.remove("prompt-extraction")


class Report(unittest.TestCase):
    """promptfoo's results JSON becomes one OTLP metrics body per agent."""

    def test_counts_pass_and_fail(self):
        stats = build.summarize({"results": {"stats": {"successes": 3, "failures": 1, "errors": 0}}})
        self.assertEqual(stats, {"passed": 3, "failed": 1, "errors": 0, "pass_rate": 0.75})

    def test_no_tests_is_a_zero_rate_not_a_crash(self):
        self.assertEqual(build.summarize({"results": {"stats": {}}})["pass_rate"], 0.0)

    def test_metrics_body_is_otlp_json_with_agent_and_team(self):
        body = build.metrics_body(agent(), build.summarize({"results": {"stats": {"successes": 1, "failures": 0}}}), "eval")
        m = body["resourceMetrics"][0]["scopeMetrics"][0]["metrics"]
        names = {x["name"] for x in m}
        self.assertEqual(names, {"evals.pass_rate", "evals.tests"})
        point = m[0]["gauge"]["dataPoints"][0]
        keys = {a["key"] for a in point["attributes"]}
        self.assertEqual(keys, {"agent", "team", "suite"})
        self.assertTrue(point["timeUnixNano"].isdigit())


if __name__ == "__main__":
    unittest.main()
