"""Tests for the renovate-auditor container agent's pure parts.

The command guard and the history trim decide what the pod runs and how many
tokens each turn costs. Both are pure, so both are tested here.
Run with: python3 -m unittest discover -s tests -v
"""
import datetime
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "apps", "renovate-auditor"))
os.environ.setdefault("LLM_BASE_URL", "http://example.invalid")
import agent  # noqa: E402
import shell  # noqa: E402


class CommandGuard(unittest.TestCase):

    def test_allows_the_tools_an_audit_needs(self):
        for command in ("git rev-list --count HEAD..origin/master",
                        "gh pr checks 1596", "pnpm --filter @acme/api test",
                        "npm view @types/bcryptjs --json", "cat package.json"):
            shell.check(command)

    def test_refuses_a_tool_that_is_not_on_the_list(self):
        with self.assertRaises(shell.Refused):
            shell.check("rm -rf node_modules")

    def test_refuses_the_disallowed_word_anywhere_in_a_pipeline(self):
        with self.assertRaises(shell.Refused):
            shell.check("git log | rm -rf .")

    def test_refuses_a_disallowed_word_after_a_separator(self):
        for command in ("git status && rm x", "git status; rm x", "git status || rm x"):
            with self.assertRaises(shell.Refused):
                shell.check(command)

    def test_reads_past_a_leading_variable_assignment(self):
        shell.check("GIT_PAGER=cat git log --oneline -1")

    def test_refuses_a_command_it_cannot_parse(self):
        with self.assertRaises(shell.Refused):
            shell.check("git commit -m 'unclosed")

    def test_refuses_an_empty_command(self):
        with self.assertRaises(shell.Refused):
            shell.check("   ")

    def test_no_command_outside_the_allow_list_is_ever_accepted(self):
        """The invariant: every segment's first word is in ALLOWED, or nothing runs."""
        outsiders = ["rm", "curl", "wget", "nc", "ssh", "sudo", "chmod", "kubectl", "docker",
                     "bash", "sh", "eval", "systemctl", "apt-get", "pip"]
        for word in outsiders:
            for command in (word, f"{word} --help", f"echo hi && {word} x", f"ls | {word}"):
                with self.assertRaises(shell.Refused, msg=command):
                    shell.check(command)

    def test_a_separator_inside_quotes_is_not_a_separator(self):
        """grep -E "a|b" is one command, not a pipeline into b."""
        shell.check('grep -inE "error|fail|Cannot|TypeError" /tmp/log.txt | head -50')
        shell.check("git log --oneline | grep -E 'feat|fix'")
        shell.check('echo "rm -rf /"')

    def test_a_disallowed_word_still_caught_when_quotes_are_present(self):
        with self.assertRaises(shell.Refused):
            shell.check('grep -E "a|b" file | rm -rf .')

    def test_reads_past_a_subshell_bracket(self):
        shell.check("(git log --oneline -1)")
        shell.check("cd /data && (gh pr view 1596 | head -5)")

    def test_a_heredoc_body_is_data_not_commands(self):
        shell.check("cat > /tmp/s.py << 'EOF'\nimport re\nrm -rf /\nEOF")

    def test_a_write_command_is_still_refused_inside_a_heredoc_line(self):
        with self.assertRaises(shell.Refused):
            shell.check("git push origin HEAD << 'EOF'\nbody\nEOF")

    def test_an_unbalanced_quote_later_in_the_line_does_not_hide_the_first_word(self):
        shell.check("python3 -c \"print('x')\"")

    def test_refuses_every_command_that_would_change_the_repository(self):
        """The invariant: this agent reads. A fix is a person's decision."""
        for command in ("git push origin HEAD:renovate/tsx-4.x",
                        "git push --force-with-lease=b:abc origin HEAD:b",
                        "git commit -m x", "gh pr merge 1596", "gh pr close 1596",
                        "gh pr comment 1596 --body-file f", "gh pr review 1596 --approve",
                        "gh pr edit 1596 --add-label x", "gh api -X DELETE repos/o/r",
                        "npm publish", "git status && git push"):
            with self.assertRaises(shell.Refused, msg=command):
                shell.check(command)

    def test_still_allows_the_read_only_gh_and_git_the_audit_needs(self):
        for command in ("gh pr checks 1596", "gh pr view 1596 --json mergeable",
                        "gh api repos/o/r/releases/tags/v26.0.0", "git fetch origin master",
                        "git rev-list --count HEAD..origin/master", "git grep -n opt origin/master"):
            shell.check(command)

    def test_output_longer_than_the_cap_is_cut_and_says_so(self):
        result = shell.run("python3 -c \"print('x' * 20000)\"", cwd=".")
        self.assertEqual(result["exit_code"], 0)
        self.assertLess(len(result["output"]), shell.MAX_OUTPUT + 200)
        self.assertIn("more characters cut", result["output"])

    def test_a_refused_command_does_not_run(self):
        with self.assertRaises(shell.Refused):
            shell.run("rm -rf /tmp/should-not-happen", cwd=".")


def conversation(tool_outputs):
    msgs = [{"role": "system", "content": "s"}, {"role": "user", "content": "task"}]
    for i, out in enumerate(tool_outputs):
        msgs.append({"role": "assistant", "content": None})
        msgs.append({"role": "tool", "tool_call_id": str(i), "content": out})
    return msgs


class HistoryTrim(unittest.TestCase):

    def test_a_short_conversation_is_returned_unchanged(self):
        msgs = conversation(["small", "also small"])
        self.assertEqual(agent.trim(msgs), msgs)

    def test_a_long_conversation_is_brought_under_the_budget(self):
        msgs = conversation(["y" * 50000 for _ in range(6)])
        size = sum(len(json.dumps(m)) for m in agent.trim(msgs))
        self.assertLessEqual(size, agent.HISTORY_BUDGET)

    def test_the_newest_tool_output_survives(self):
        msgs = conversation(["y" * 50000, "y" * 50000, "the newest result"])
        self.assertEqual(agent.trim(msgs)[-1]["content"], "the newest result")

    def test_the_task_and_the_method_are_never_trimmed(self):
        msgs = conversation(["y" * 80000 for _ in range(4)])
        trimmed = agent.trim(msgs)
        self.assertEqual(trimmed[0]["content"], "s")
        self.assertEqual(trimmed[1]["content"], "task")

    def test_trimming_does_not_change_the_caller_s_messages(self):
        msgs = conversation(["y" * 80000 for _ in range(4)])
        agent.trim(msgs)
        self.assertTrue(all(m["content"].startswith("y") for m in msgs if m["role"] == "tool"))

    def test_every_message_is_still_there_after_a_trim(self):
        msgs = conversation(["y" * 80000 for _ in range(4)])
        self.assertEqual(len(agent.trim(msgs)), len(msgs))


class RateLimitBackoff(unittest.TestCase):
    """A 429 from the LLM gateway is a pace, not a failure. The run waits."""

    BODY = ('{"error":{"message":"Rate limit exceeded for api_key: abc. Limit type: tokens. '
            'Current limit: 200000, Remaining: 11321. Limit resets at: 2026-09-09 23:23:31 UTC",'
            '"type":"throttling_error"}}')

    def test_waits_until_the_limit_the_gateway_named_resets(self):
        now = datetime.datetime(2026, 9, 9, 23, 23, 1, tzinfo=datetime.timezone.utc)
        self.assertEqual(agent.wait_seconds(self.BODY, now), 30 + agent.RESET_MARGIN)

    def test_a_reset_already_past_waits_the_margin_and_no_more(self):
        now = datetime.datetime(2026, 9, 9, 23, 25, 0, tzinfo=datetime.timezone.utc)
        self.assertEqual(agent.wait_seconds(self.BODY, now), agent.RESET_MARGIN)

    def test_a_body_with_no_reset_time_still_gives_a_usable_wait(self):
        self.assertGreater(agent.wait_seconds('{"error":"slow down"}', None), 0)

    def test_the_wait_is_capped_so_a_run_cannot_hang_forever(self):
        body = self.BODY.replace("2026-09-09 23:23:31", "2027-01-01 00:00:00")
        now = datetime.datetime(2026, 9, 9, 23, 23, 1, tzinfo=datetime.timezone.utc)
        self.assertLessEqual(agent.wait_seconds(body, now), agent.MAX_WAIT)
