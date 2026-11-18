import json
import subprocess
import unittest

from helpers import init_line, rate_limit_line, result_line
import warmup

HELP_ALL = """
  --tools <tools...>        Specify the list of available tools
  --strict-mcp-config       Only use MCP servers from --mcp-config
  --setting-sources <s>     Comma-separated list
  --safe-mode               Start with all customizations disabled
  --disable-slash-commands  Disable all skills
  --no-session-persistence  Disable session persistence
"""


class Done:
    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout, self.stderr, self.returncode = stdout, stderr, returncode


class FakeRunner:
    def __init__(self, stream, help_text=HELP_ALL, stderr="", returncode=0, raises=None):
        self.stream, self.help_text, self.stderr, self.returncode, self.raises = stream, help_text, stderr, returncode, raises
        self.calls = []

    def __call__(self, command, **kwargs):
        self.calls.append((command, kwargs))
        if command[1:] == ["--help"]:
            return Done(self.help_text)
        if self.raises:
            raise self.raises
        return Done(self.stream, self.stderr, self.returncode)


class ParseStreamTests(unittest.TestCase):
    def test_reads_both_windows_and_tokens(self):
        stream = "\n".join([init_line(), rate_limit_line("five_hour", 1795000000, 0.12), rate_limit_line("seven_day", 1795400000, 0.41), result_line()])
        outcome = warmup.parse_stream(stream)
        self.assertTrue(outcome["ok"])
        self.assertEqual(outcome["windows"]["five_hour"], {"resets_at": 1795000000, "used_percent": 12.0})
        self.assertEqual(outcome["windows"]["seven_day"]["used_percent"], 41.0)
        self.assertEqual(outcome["tokens"], 122)
        self.assertEqual(outcome["status"], "allowed")

    def test_reads_the_stream_claude_code_2_1_303_emits(self):
        info = {"status": "allowed", "resetsAt": 1795008600, "rateLimitType": "five_hour", "overageStatus": "rejected",
                "overageResetsAt": 1796083200, "overageDisabledReason": "org_level_disabled_until", "isUsingOverage": False,
                "unifiedWindows": {"five_hour": {"utilization": 0.19, "resetsAt": 1795008600}, "seven_day": {"utilization": 0.03, "resetsAt": 1795316400}}}
        usage = {"input_tokens": 2, "cache_creation_input_tokens": 608, "cache_read_input_tokens": 0, "output_tokens": 4}
        stream = "\n".join([
            init_line(),
            json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "OK"}]}}),
            json.dumps({"type": "rate_limit_event", "rate_limit_info": info}),
            json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": "OK", "usage": usage}),
        ])
        outcome = warmup.parse_stream(stream)
        self.assertEqual(outcome["windows"], {"five_hour": {"resets_at": 1795008600, "used_percent": 19.0},
                                              "seven_day": {"resets_at": 1795316400, "used_percent": 3.0}})
        self.assertEqual((outcome["ok"], outcome["status"], outcome["tokens"]), (True, "allowed", 614))

    def test_reads_unified_windows_and_snake_case(self):
        unified = {"five_hour": {"resetsAt": 1795000000, "utilization": 0.2}, "seven_day": {"resets_at": 1795400000, "utilization": 0.5}}
        stream = "\n".join([rate_limit_line("five_hour", None, None, unifiedWindows=unified), result_line()])
        outcome = warmup.parse_stream(stream)
        self.assertEqual(outcome["windows"]["seven_day"], {"resets_at": 1795400000, "used_percent": 50.0})

    def test_percentages_above_one_are_kept(self):
        self.assertEqual(warmup.percent(37), 37.0)
        self.assertEqual(warmup.percent(0.37), 37.0)

    def test_rejected_limit(self):
        stream = "\n".join([rate_limit_line("five_hour", 1795000000, 1.0, status="rejected"), result_line("limit reached", is_error=True)])
        outcome = warmup.parse_stream(stream)
        self.assertEqual((outcome["status"], outcome["rejected"], outcome["ok"]), ("rejected", "five_hour", False))

    def test_auth_error_is_classified_and_redacted(self):
        stream = result_line("Not logged in · Please run /login sk-ant-oat01-abcdefghijklmnopqrstuvwxyz", is_error=True)
        outcome = warmup.parse_stream(stream)
        self.assertEqual(outcome["error_kind"], "auth")
        self.assertNotIn("sk-ant", outcome["error"])

    def test_garbage_lines_are_ignored(self):
        self.assertFalse(warmup.parse_stream("not json\n[1,2]\n")["ok"])


class CommandTests(unittest.TestCase):
    def test_minimal_command_with_every_flag(self):
        command = warmup.build_command("claude", HELP_ALL)
        for flag in ("--safe-mode", "--strict-mcp-config", "--no-session-persistence", "--disable-slash-commands"):
            self.assertIn(flag, command)
        self.assertEqual(command[command.index("--tools") + 1], "")
        self.assertEqual(command[command.index("--model") + 1], "haiku")
        self.assertEqual(command[-5:], ["--max-turns", "1", "--output-format", "stream-json", "--verbose"])

    def test_unknown_flags_are_dropped_on_older_cli(self):
        command = warmup.build_command("claude", "  --tools <tools...>  list\n")
        self.assertNotIn("--safe-mode", command)
        self.assertIn("--tools", command)

    def test_never_uses_bare_mode(self):
        self.assertNotIn("--bare", warmup.build_command("claude", HELP_ALL + "  --bare  minimal\n"))

    def test_clean_env_keeps_only_safe_keys(self):
        source = {"HOME": "/h", "PATH": "/p", "CLAUDECODE": "1", "ANTHROPIC_BASE_URL": "http://proxy", "CLAUDE_CONFIG_DIR": "/other"}
        env = warmup.clean_env("/profile", source)
        self.assertEqual(env, {"HOME": "/h", "PATH": "/p", "CLAUDE_CONFIG_DIR": "/profile"})
        self.assertNotIn("CLAUDE_CONFIG_DIR", warmup.clean_env(None, source))


class RunTests(unittest.TestCase):
    def test_runs_in_workdir_with_profile_env(self):
        runner = FakeRunner("\n".join([rate_limit_line("five_hour", 1795000000), result_line()]))
        outcome = warmup.run("claude", "/profile", "/empty", runner=runner)
        command, kwargs = runner.calls[-1]
        self.assertTrue(outcome["ok"])
        self.assertEqual(kwargs["cwd"], "/empty")
        self.assertEqual(kwargs["env"]["CLAUDE_CONFIG_DIR"], "/profile")
        self.assertIs(kwargs["stdin"], subprocess.DEVNULL)

    def test_stderr_explains_a_crash(self):
        outcome = warmup.run("claude", None, "/empty", runner=FakeRunner("", stderr="Please run /login", returncode=1))
        self.assertEqual(outcome["error_kind"], "auth")

    def test_timeout(self):
        runner = FakeRunner("", raises=subprocess.TimeoutExpired("claude", 120))
        self.assertEqual(warmup.run("claude", None, "/empty", runner=runner)["error_kind"], "timeout")

    def test_missing_binary(self):
        runner = FakeRunner("", raises=FileNotFoundError(2, "No such file"))
        self.assertEqual(warmup.run("/nope/claude", None, "/empty", runner=runner)["error_kind"], "claude")


if __name__ == "__main__":
    unittest.main()
