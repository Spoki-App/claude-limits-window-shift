import contextlib
import datetime
import io
import json
import os
import tempfile
import unittest
from unittest import mock
from zoneinfo import ZoneInfo

from helpers import SKILL, load, rate_limit_line, result_line

cli = load(SKILL / "window_shift.py", "window_shift")
store = cli.store
ROME = ZoneInfo("Europe/Rome")


def epoch(text):
    return int(datetime.datetime.fromisoformat(text).replace(tzinfo=ROME).timestamp())


class CliCase(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.claude = os.path.join(self.folder.name, "claude")
        with open(self.claude, "w") as handle:
            handle.write("#!/bin/sh\n")
        os.chmod(self.claude, 0o755)
        env = {"CLWS_CONFIG": os.path.join(self.folder.name, "cfg", "config.json"), "CLAUDE_CONFIG_DIR": "/profiles/2/cli"}
        patcher = mock.patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)
        for name in ("installed",):
            patch = mock.patch.object(cli.service, name, return_value=False)
            patch.start()
            self.addCleanup(patch.stop)

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def setup_workday(self):
        return self.run_cli("setup", "--start", "09:00", "--end", "18:00", "--timezone", "Europe/Rome", "--claude", self.claude)


class SetupAndPlanTests(CliCase):
    def test_setup_saves_workday_and_current_profile(self):
        code, out, _ = self.setup_workday()
        config = store.load_config()
        self.assertEqual(code, 0)
        self.assertEqual(config["profiles"], [{"name": "default", "config_dir": "/profiles/2/cli"}])
        self.assertEqual((config["workday"]["days"], config["claude"]), ("mon-fri", self.claude))
        self.assertIn("With a warm-up at 06:00: 3 windows", out)

    def test_setup_needs_hours_the_first_time(self):
        code, _, err = self.run_cli("setup", "--days", "mon-fri")
        self.assertEqual(code, cli.EXIT_CONFIG)
        self.assertIn("--start and --end", err)

    def test_setup_adds_and_removes_profiles(self):
        self.setup_workday()
        self.run_cli("setup", "--profile", "main", "--config-dir", "~/.claude")
        self.assertEqual([p["name"] for p in store.load_config()["profiles"]], ["default", "main"])
        self.run_cli("setup", "--remove-profile", "default")
        self.assertEqual([p["name"] for p in store.load_config()["profiles"]], ["main"])

    def test_bad_timezone_is_refused(self):
        code, _, err = self.run_cli("setup", "--start", "09:00", "--end", "18:00", "--timezone", "Mars/Base")
        self.assertEqual(code, cli.EXIT_CONFIG)
        self.assertIn("unknown timezone", err)

    def test_plan_works_without_setup(self):
        code, out, _ = self.run_cli("plan", "--start", "08:00", "--end", "17:00")
        self.assertEqual(code, 0)
        self.assertIn("warm-up at 05:00", out)

    def test_cloud_spec_json(self):
        self.setup_workday()
        code, out, _ = self.run_cli("cloud-spec", "--json")
        spec = json.loads(out)
        self.assertEqual((code, spec["cron"], spec["tools"], spec["connectors"]), (0, "CRON_TZ=Europe/Rome 7 6 * * 1-5", ["Read"], []))

    def test_cloud_spec_never_asks_for_an_empty_tool_list(self):
        self.setup_workday()
        _, out, _ = self.run_cli("cloud-spec")
        self.assertIn("Tools:      Read only", out)


class TickTests(CliCase):
    def setUp(self):
        super().setUp()
        self.setup_workday()
        self.outcome = {"ok": True, "error": None, "error_kind": None, "status": "allowed", "rejected": None, "tokens": 240,
                        "windows": {"five_hour": {"resets_at": epoch("2026-11-19T11:07"), "used_percent": 1.0},
                                    "seven_day": {"resets_at": epoch("2026-11-20T22:00"), "used_percent": 40.0}}}
        patch = mock.patch.object(cli.warmup, "run", side_effect=lambda *a, **k: self.outcome)
        self.run_mock = patch.start()
        self.addCleanup(patch.stop)

    def test_tick_before_anchor_skips(self):
        code, out, _ = self.run_cli("tick", "--now", "2026-11-19T05:50")
        self.assertEqual(code, 0)
        self.assertIn("SKIP profile=default before the 06:00 warm-up", out)
        self.run_mock.assert_not_called()

    def test_tick_warms_once_per_window(self):
        _, first, _ = self.run_cli("tick", "--now", "2026-11-19T06:07")
        _, second, _ = self.run_cli("tick", "--now", "2026-11-19T06:12")
        self.assertIn("WARMED profile=default tokens=240, window until Thu 11:07", first)
        self.assertIn("window open until Thu 11:07", second)
        self.assertEqual(self.run_mock.call_count, 1)
        self.assertEqual(self.run_mock.call_args.args[1], "/profiles/2/cli")

    def test_dry_run_never_sends(self):
        _, out, _ = self.run_cli("tick", "--now", "2026-11-19T06:07", "--dry-run")
        self.assertIn("WARM profile=default no open window", out)
        self.run_mock.assert_not_called()

    def test_status_shows_window_and_weekly_pace(self):
        self.run_cli("tick", "--now", "2026-11-19T06:07")
        with mock.patch.object(cli, "now_in", return_value=datetime.datetime(2026, 11, 19, 9, 0, tzinfo=ROME)):
            code, out, _ = self.run_cli("status")
        self.assertEqual(code, 0)
        self.assertIn("5h window: open until Thu 11:07, 1% used", out)
        self.assertIn("weekly: 40% used, resets Fri 22:00, 2 workdays left, about 30% per workday", out)
        self.assertIn("Cloud warm-up: CRON_TZ=Europe/Rome 7 6 * * 1-5", out)

    def test_auth_failure_exits_with_login_hint(self):
        self.outcome = {"ok": False, "error": "Not logged in", "error_kind": "auth", "status": None, "rejected": None, "windows": {}, "tokens": None}
        with mock.patch.object(cli, "notify") as notify:
            code, out, _ = self.run_cli("warm")
        self.assertEqual(code, cli.EXIT_AUTH)
        self.assertIn("Fix: CLAUDE_CONFIG_DIR=/profiles/2/cli claude auth login", out)
        notify.assert_called_once()

    def test_weekly_drift_is_reported(self):
        week = 7 * 24 * 3600
        self.assertIsNone(cli.weekly_drift([0, week, 2 * week]))
        self.assertEqual(cli.weekly_drift([0, week + 86400]), (0, week + 86400))


class FullStreamTests(CliCase):
    def test_warm_parses_a_real_stream_shape(self):
        self.setup_workday()
        stream = "\n".join([rate_limit_line("five_hour", epoch("2026-11-19T11:00"), 0.02), result_line()])

        def runner(command, **kwargs):
            text = "  --safe-mode  x\n" if command[1:] == ["--help"] else stream
            return mock.Mock(stdout=text, stderr="", returncode=0)

        with mock.patch.object(cli.subprocess, "run", side_effect=runner):
            code, out, _ = self.run_cli("warm")
        self.assertEqual(code, 0)
        self.assertIn("WARMED", out)
        self.assertEqual(store.load_state("default")["five_hour_resets_at"], epoch("2026-11-19T11:00"))


if __name__ == "__main__":
    unittest.main()
