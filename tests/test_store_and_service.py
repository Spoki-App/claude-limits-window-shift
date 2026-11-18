import os
import plistlib
import stat
import tempfile
import unittest
from unittest import mock

import helpers  # noqa: F401
import planner
import service
import store

NOW = 1795000000


class TempHome(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        patcher = mock.patch.dict(os.environ, {"CLWS_CONFIG": os.path.join(self.folder.name, "cfg", "config.json")})
        patcher.start()
        self.addCleanup(patcher.stop)


class StoreTests(TempHome):
    def test_config_is_private(self):
        path = store.save_config({"workday": {"start": "09:00"}})
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        self.assertEqual(store.load_config()["workday"]["start"], "09:00")

    def test_insecure_config_is_refused(self):
        path = store.save_config({})
        os.chmod(path, 0o644)
        with self.assertRaises(store.StoreError):
            store.load_config()

    def test_state_and_log_round_trip(self):
        store.save_state("work profile", {"a": 1})
        self.assertEqual(store.load_state("work profile"), {"a": 1})
        store.append_log({"action": "warm"})
        self.assertEqual(store.read_log()[-1]["action"], "warm")

    def test_lock_is_exclusive(self):
        with store.exclusive() as first:
            with store.exclusive() as second:
                self.assertEqual((first, second), (True, False))


class RecordTests(unittest.TestCase):
    def ok(self, windows, status="allowed"):
        return {"ok": True, "windows": windows, "status": status, "tokens": 240}

    def test_observed_reset_wins(self):
        state = store.record({}, self.ok({"five_hour": {"resets_at": NOW + 100, "used_percent": 3.0}}), NOW, 18000)
        self.assertEqual((state["five_hour_resets_at"], state["five_hour_estimated"]), (NOW + 100, False))

    def test_estimates_when_no_window_is_reported(self):
        state = store.record({}, self.ok({}), NOW, 18000)
        self.assertEqual((state["five_hour_resets_at"], state["five_hour_estimated"]), (NOW + 18000, True))

    def test_estimate_never_extends_an_open_window(self):
        state = store.record({"five_hour_resets_at": NOW + 50}, self.ok({}), NOW, 18000)
        self.assertEqual(state["five_hour_resets_at"], NOW + 50)

    def test_failure_does_not_invent_a_window(self):
        state = store.record({}, {"ok": False, "error": "x", "error_kind": "auth", "windows": {}}, NOW, 18000)
        self.assertNotIn("five_hour_resets_at", state)
        self.assertFalse(state["last_ok"])

    def test_weekly_history_keeps_distinct_resets(self):
        state = {}
        for resets in (NOW, NOW, NOW + 604800):
            store.record(state, self.ok({"seven_day": {"resets_at": resets, "used_percent": 10.0}}), NOW, 18000)
        self.assertEqual(state["seven_day_resets"], [NOW, NOW + 604800])

    def test_rejection_blocks_until_reset_then_clears(self):
        windows = {"five_hour": {"resets_at": NOW + 900}}
        state = store.record({}, {"ok": False, "status": "rejected", "rejected": "five_hour", "windows": windows}, NOW, 18000)
        self.assertEqual(state["blocked_until"], NOW + 900)
        store.record(state, self.ok({}), NOW + 1000, 18000)
        self.assertNotIn("blocked_until", state)


class Recorder:
    def __init__(self):
        self.commands = []

    def __call__(self, command, **kwargs):
        self.commands.append(command)
        return mock.Mock(returncode=0, stdout="", stderr="")


class ServiceTests(unittest.TestCase):
    def plan(self):
        return planner.best_plan(540, 1080)

    def test_plist_runs_tick_every_five_minutes(self):
        data = plistlib.loads(service.launchd_plist("/usr/bin/python3", "/x/window_shift.py", "/bin", "/tmp/log", {"CLWS_CONFIG": "/c"}))
        self.assertEqual(data["ProgramArguments"], ["/usr/bin/python3", "-I", "/x/window_shift.py", "tick"])
        self.assertEqual((data["StartInterval"], data["RunAtLoad"]), (300, True))
        self.assertEqual(data["EnvironmentVariables"], {"PATH": "/bin", "CLWS_CONFIG": "/c"})

    def test_systemd_timer_is_persistent(self):
        unit, timer = service.systemd_units("/usr/bin/python3", "/x/window_shift.py", "/bin")
        self.assertIn("ExecStart=/usr/bin/python3 -I /x/window_shift.py tick", unit)
        self.assertIn("Persistent=true", timer)

    def test_install_on_macos_bootstraps_the_agent(self):
        with tempfile.TemporaryDirectory() as home:
            runner = Recorder()
            ok, path, _ = service.install("py", "s.py", "/bin", "/tmp/log", runner=runner, platform="darwin", home=home)
            self.assertTrue(ok and os.path.exists(path))
            self.assertEqual(runner.commands[-1][:2], ["launchctl", "bootstrap"])
            self.assertEqual(service.scheduled_script("darwin", home), "s.py")
            removed, _ = service.uninstall(runner=runner, platform="darwin", home=home)
            self.assertTrue(removed)
            self.assertFalse(os.path.exists(path))

    def test_install_on_linux_enables_the_timer(self):
        with tempfile.TemporaryDirectory() as home:
            runner = Recorder()
            ok, folder, _ = service.install("py", "s.py", "/bin", "/tmp/log", runner=runner, platform="linux", home=home)
            self.assertTrue(ok)
            self.assertTrue(os.path.exists(os.path.join(folder, "claude-limits-window-shift.timer")))
            self.assertEqual(service.scheduled_script("linux", home), "s.py")
            self.assertIn(["systemctl", "--user", "enable", "--now", "claude-limits-window-shift.timer"], runner.commands)

    def test_cloud_cron_is_off_the_hour_and_local(self):
        self.assertEqual(service.cloud_cron(self.plan(), [0, 1, 2, 3, 4], "Europe/Rome"), "CRON_TZ=Europe/Rome 7 6 * * 1-5")
        self.assertEqual(service.cron_days([0, 2, 6]), "0-1,3")

    def test_cloud_cron_stays_inside_the_useful_range(self):
        plan = planner.best_plan(540, 1080, not_before=450)
        self.assertLessEqual(service.cloud_minute(plan), plan.latest)


if __name__ == "__main__":
    unittest.main()
