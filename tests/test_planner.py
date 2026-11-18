import datetime
import unittest
from zoneinfo import ZoneInfo

import helpers  # noqa: F401
import planner

ROME = ZoneInfo("Europe/Rome")
WEEKDAYS = [0, 1, 2, 3, 4]


def at(text):
    return datetime.datetime.fromisoformat(text).replace(tzinfo=ROME)


def nine_to_six():
    return planner.best_plan(planner.parse_hhmm("09:00"), planner.parse_hhmm("18:00"))


class ParsingTests(unittest.TestCase):
    def test_times(self):
        self.assertEqual(planner.parse_hhmm("09:30"), 570)
        self.assertEqual(planner.parse_hhmm("7:05"), 425)
        for bad in ("24:00", "9", "09:60", "nine"):
            with self.assertRaises(planner.PlanError):
                planner.parse_hhmm(bad)

    def test_days(self):
        self.assertEqual(planner.parse_days("mon-fri"), WEEKDAYS)
        self.assertEqual(planner.parse_days("Monday, wed-thu, sun"), [0, 2, 3, 6])
        self.assertEqual(planner.format_days([0, 1, 2, 3, 4, 6]), "mon-fri,sun")
        for bad in ("fri-mon", "", "xyz"):
            with self.assertRaises(planner.PlanError):
                planner.parse_days(bad)

    def test_formatting(self):
        self.assertEqual(planner.format_hhmm(360), "06:00")
        self.assertEqual(planner.format_hhmm(1500), "01:00 (+1d)")
        self.assertEqual(planner.format_duration(125), "2h05")


class BestPlanTests(unittest.TestCase):
    def test_nine_to_six_anchors_at_six(self):
        plan = nine_to_six()
        self.assertEqual(planner.format_hhmm(plan.anchor), "06:00")
        self.assertEqual([planner.format_hhmm(r) for r in plan.resets], ["11:00", "16:00"])
        self.assertEqual((len(plan.baseline), len(plan.windows), plan.gain), (2, 3, 1))
        self.assertEqual((planner.format_hhmm(plan.earliest), planner.format_hhmm(plan.latest)), ("04:30", "07:30"))

    def test_balances_first_and_last_window(self):
        plan = nine_to_six()
        self.assertEqual([overlap for _, _, overlap in plan.windows], [120, 300, 120])

    def test_ten_hour_day(self):
        plan = planner.best_plan(planner.parse_hhmm("08:00"), planner.parse_hhmm("18:00"))
        self.assertEqual(planner.format_hhmm(plan.anchor), "05:30")
        self.assertEqual(len(plan.windows), 3)

    def test_short_day_still_gains_a_reset(self):
        plan = planner.best_plan(planner.parse_hhmm("09:00"), planner.parse_hhmm("13:00"))
        self.assertEqual((len(plan.baseline), len(plan.windows)), (1, 2))
        self.assertEqual(planner.format_hhmm(plan.resets[0]), "11:00")

    def test_tiny_day_has_nothing_to_gain(self):
        plan = planner.best_plan(planner.parse_hhmm("09:00"), planner.parse_hhmm("09:45"), margin=30)
        self.assertEqual(plan.gain, 0)
        self.assertIn("adds nothing", planner.render_plan(plan, WEEKDAYS))

    def test_not_before_limits_the_anchor(self):
        plan = planner.best_plan(planner.parse_hhmm("09:00"), planner.parse_hhmm("18:00"), not_before=planner.parse_hhmm("07:00"))
        self.assertEqual(planner.format_hhmm(plan.anchor), "07:00")
        self.assertEqual(planner.format_hhmm(plan.earliest), "07:00")
        self.assertEqual(len(plan.windows), 3)

    def test_early_workday_never_anchors_before_midnight(self):
        plan = planner.best_plan(planner.parse_hhmm("02:00"), planner.parse_hhmm("11:00"))
        self.assertGreaterEqual(plan.anchor, 0)

    def test_invalid_workdays(self):
        with self.assertRaises(planner.PlanError):
            planner.best_plan(600, 540)
        with self.assertRaises(planner.PlanError):
            planner.best_plan(540, 600, margin=300)

    def test_render_mentions_both_grids(self):
        text = planner.render_plan(nine_to_six(), WEEKDAYS)
        self.assertIn("With a warm-up at 06:00: 3 windows", text)
        self.assertIn("Without a warm-up: 2 windows", text)
        self.assertIn("between 04:30 and 07:30", text)


class DecideTests(unittest.TestCase):
    def decide(self, when, state=None, skip=()):
        return planner.decide(at(when), nine_to_six(), WEEKDAYS, skip, state or {})

    def test_weekend_and_days_off(self):
        self.assertEqual(self.decide("2026-11-21T06:05")[0], planner.SKIP)
        self.assertEqual(self.decide("2026-11-19T06:05", skip=["2026-11-19"]), (planner.SKIP, "day off"))

    def test_before_anchor_and_after_workday(self):
        self.assertEqual(self.decide("2026-11-19T05:55")[1], "before the 06:00 warm-up")
        self.assertEqual(self.decide("2026-11-19T17:31")[1], "workday over")

    def test_warms_at_anchor_without_state(self):
        self.assertEqual(self.decide("2026-11-19T06:00"), (planner.WARM, "no open window"))

    def test_open_window_is_left_alone(self):
        resets = at("2026-11-19T11:00").timestamp()
        decision, reason = self.decide("2026-11-19T09:30", {"five_hour_resets_at": resets})
        self.assertEqual(decision, planner.SKIP)
        self.assertIn("window open until", reason)

    def test_warms_just_after_the_reset(self):
        resets = at("2026-11-19T11:00").timestamp()
        self.assertEqual(self.decide("2026-11-19T11:00", {"five_hour_resets_at": resets})[0], planner.SKIP)
        self.assertEqual(self.decide("2026-11-19T11:05", {"five_hour_resets_at": resets})[0], planner.WARM)

    def test_rejected_limit_is_never_retried_early(self):
        blocked = at("2026-11-19T14:00").timestamp()
        decision, reason = self.decide("2026-11-19T12:00", {"blocked_until": blocked})
        self.assertEqual(decision, planner.SKIP)
        self.assertIn("limit reached", reason)

    def test_failed_attempt_backs_off(self):
        last = at("2026-11-19T06:00").timestamp()
        self.assertEqual(self.decide("2026-11-19T06:05", {"last_attempt_at": last, "last_ok": False})[0], planner.SKIP)
        self.assertEqual(self.decide("2026-11-19T06:20", {"last_attempt_at": last, "last_ok": False})[0], planner.WARM)


class WorkdaysUntilTests(unittest.TestCase):
    def test_counts_remaining_workdays_before_the_reset(self):
        now = at("2026-11-18T10:00")
        reset = at("2026-11-23T06:00").timestamp()
        self.assertEqual(planner.workdays_until(now, reset, nine_to_six(), WEEKDAYS, ()), 3)

    def test_skips_days_off_and_finished_today(self):
        now = at("2026-11-18T19:00")
        reset = at("2026-11-23T06:00").timestamp()
        self.assertEqual(planner.workdays_until(now, reset, nine_to_six(), WEEKDAYS, ["2026-11-20"]), 1)

    def test_counts_the_reset_day_when_work_starts_before_it(self):
        now = at("2026-11-18T10:00")
        reset = at("2026-11-19T12:00").timestamp()
        self.assertEqual(planner.workdays_until(now, reset, nine_to_six(), WEEKDAYS, ()), 2)


if __name__ == "__main__":
    unittest.main()
