import unittest

import helpers  # noqa: F401
import planner
import service

WEEKDAYS = [0, 1, 2, 3, 4]
SESSION = "session"


def nine_to_six():
    return planner.best_plan(planner.parse_hhmm("09:00"), planner.parse_hhmm("18:00"))


def clock(fires):
    return [(role, planner.format_hhmm(minute)) for role, minute in fires]


def night(plan, fires, *sessions):
    return [(minute, role) for role, minute in fires] + [(planner.night_minute(text, plan), SESSION) for text in sessions]


def windows_at_work(plan, messages):
    return len(planner.useful_windows(planner.anchor_after(messages, plan), plan.start, plan.end, plan.window, plan.margin))


def only(fires, *roles):
    return [fire for fire in fires if fire[0] in roles]


class CloudFiresTests(unittest.TestCase):
    def test_a_nine_to_six_day_gets_a_guard_a_warm_up_and_a_late_warm_up(self):
        self.assertEqual(clock(service.cloud_fires(nine_to_six())), [("night guard", "00:52"), ("warm-up", "06:07"), ("late warm-up", "07:25")])

    def test_the_guard_window_is_over_before_the_warm_up(self):
        plan = nine_to_six()
        fires = dict(service.cloud_fires(plan))
        self.assertLess(fires[service.GUARD] + plan.window, fires[service.WARM_UP])
        self.assertGreaterEqual(fires[service.GUARD] + plan.window, plan.earliest)

    def test_the_late_warm_up_still_keeps_every_window(self):
        plan = nine_to_six()
        late = dict(service.cloud_fires(plan))[service.LATE]
        self.assertLessEqual(late, plan.latest)
        self.assertEqual(len(planner.useful_windows(late, plan.start, plan.end, plan.window, plan.margin)), len(plan.windows))

    def test_a_guard_before_midnight_runs_the_evening_before(self):
        plan = planner.best_plan(planner.parse_hhmm("06:00"), planner.parse_hhmm("15:00"))
        guard = dict(service.cloud_fires(plan))[service.GUARD]
        self.assertEqual(planner.format_hhmm(plan.anchor), "03:00")
        self.assertEqual(service.cron_at(guard, WEEKDAYS, "Europe/Rome"), "CRON_TZ=Europe/Rome 52 21 * * 0-4")

    def test_a_day_with_nothing_to_gain_gets_the_warm_up_alone(self):
        plan = planner.best_plan(planner.parse_hhmm("09:00"), planner.parse_hhmm("09:45"), margin=30)
        self.assertEqual([role for role, _ in service.cloud_fires(plan)], [service.WARM_UP])

    def test_not_before_drops_an_earlier_guard_and_a_late_warm_up_with_no_room(self):
        not_before = planner.parse_hhmm("07:30")
        plan = planner.best_plan(planner.parse_hhmm("09:00"), planner.parse_hhmm("18:00"), not_before=not_before)
        self.assertEqual(clock(service.cloud_fires(plan, not_before)), [("warm-up", "07:30")])


class NightReplayTests(unittest.TestCase):
    def setUp(self):
        self.plan = nine_to_six()
        self.fires = service.cloud_fires(self.plan)

    def test_a_quiet_night_anchors_at_the_warm_up(self):
        self.assertEqual(planner.format_hhmm(planner.anchor_after(night(self.plan, self.fires), self.plan)), "06:07")

    def test_a_session_at_two_swallows_the_warm_up_and_the_late_one_recovers(self):
        sessions = ("20:40", "22:14", "02:16")
        messages = night(self.plan, self.fires, *sessions)
        self.assertEqual(planner.format_hhmm(planner.anchor_after(messages, self.plan)), "07:25")
        self.assertEqual(windows_at_work(self.plan, messages), 3)
        self.assertEqual(windows_at_work(self.plan, night(self.plan, only(self.fires, service.WARM_UP), *sessions)), 2)

    def test_the_guard_keeps_a_three_o_clock_session_out_of_the_morning(self):
        sessions = ("19:15", "23:16", "03:17", "07:19")
        messages = night(self.plan, self.fires, *sessions)
        self.assertEqual(planner.format_hhmm(planner.anchor_after(messages, self.plan)), "06:07")
        self.assertEqual(windows_at_work(self.plan, messages), 3)
        self.assertEqual(windows_at_work(self.plan, night(self.plan, only(self.fires, service.WARM_UP, service.LATE), *sessions)), 2)

    def test_an_evening_window_then_a_three_o_clock_session_cannot_be_fixed(self):
        messages = night(self.plan, self.fires, "21:30", "03:00")
        self.assertEqual(planner.anchor_after(messages, self.plan), self.plan.start)
        self.assertEqual(windows_at_work(self.plan, messages), 2)

    def test_replay_says_who_opened_each_window(self):
        steps = planner.replay(night(self.plan, self.fires, "02:16"))
        told = [(planner.format_hhmm(minute), label, opened, planner.format_hhmm(end)) for minute, label, opened, end in steps]
        self.assertEqual(told, [
            ("00:52", "night guard", True, "05:52"),
            ("02:16", SESSION, False, "05:52"),
            ("06:07", "warm-up", True, "11:07"),
            ("07:25", "late warm-up", False, "11:07"),
        ])

    def test_evening_times_belong_to_the_day_before(self):
        self.assertEqual(planner.night_minute("20:40", self.plan), planner.parse_hhmm("20:40") - 1440)
        self.assertEqual(planner.night_minute("02:16", self.plan), planner.parse_hhmm("02:16"))

    def test_render_night_tells_the_story_and_the_result(self):
        text = planner.render_night(self.plan, self.fires, [planner.night_minute(t, self.plan) for t in ("20:40", "02:16")])
        self.assertIn("20:40 (-1d)  session opens a window until 01:40", text)
        self.assertIn("00:52  night guard joins the window open until 01:40", text)
        self.assertIn("06:07  warm-up joins the window open until 07:16", text)
        self.assertIn("07:25  late warm-up opens a window until 12:25", text)
        self.assertIn("3 windows touch your workday", text)

    def test_render_night_names_the_hours_no_warm_up_can_fix(self):
        text = planner.render_night(self.plan, self.fires, [planner.night_minute(t, self.plan) for t in ("21:30", "03:00")])
        self.assertIn("2 windows touch your workday", text)
        self.assertIn("between 02:30 and 04:30", text)


if __name__ == "__main__":
    unittest.main()
