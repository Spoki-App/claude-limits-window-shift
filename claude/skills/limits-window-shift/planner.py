import dataclasses
import datetime
import re

DAY_NAMES = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
WINDOW_MINUTES = 300
DEFAULT_MARGIN = 30
GRACE_SECONDS = 60
RETRY_SECONDS = 900
WARM = "warm"
SKIP = "skip"


class PlanError(ValueError):
    pass


def parse_hhmm(text):
    match = re.fullmatch(r"([01]?\d|2[0-3])(?:[:.h]([0-5]\d))?", str(text).strip().lower())
    if not match:
        raise PlanError(f"invalid time {text!r}, expected HH:MM")
    return int(match.group(1)) * 60 + int(match.group(2) or 0)


def format_hhmm(minutes):
    day, rest = divmod(int(minutes), 1440)
    text = f"{rest // 60:02d}:{rest % 60:02d}"
    return text if day == 0 else f"{text} ({day:+d}d)"


def format_duration(minutes):
    return f"{int(minutes) // 60}h{int(minutes) % 60:02d}"


def day_index(name):
    key = name.strip().lower()[:3]
    if key not in DAY_NAMES:
        raise PlanError(f"invalid day {name!r}, expected one of {', '.join(DAY_NAMES)}")
    return DAY_NAMES.index(key)


def parse_days(text):
    days = set()
    for part in str(text).lower().replace(" ", "").split(","):
        if not part:
            continue
        first, _, last = part.partition("-")
        low, high = day_index(first), day_index(last or first)
        if high < low:
            raise PlanError(f"invalid day range {part!r}")
        days.update(range(low, high + 1))
    if not days:
        raise PlanError("no work days given")
    return sorted(days)


def runs(numbers):
    groups = []
    for number in sorted(numbers):
        if groups and number == groups[-1][1] + 1:
            groups[-1][1] = number
        else:
            groups.append([number, number])
    return groups


def format_days(days):
    return ",".join(DAY_NAMES[a] if a == b else f"{DAY_NAMES[a]}-{DAY_NAMES[b]}" for a, b in runs(days))


@dataclasses.dataclass(frozen=True)
class Plan:
    start: int
    end: int
    window: int
    margin: int
    anchor: int
    earliest: int
    latest: int
    windows: tuple
    baseline: tuple

    @property
    def gain(self):
        return len(self.windows) - len(self.baseline)

    @property
    def resets(self):
        return tuple(begin for begin, _, _ in self.windows[1:])


def useful_windows(anchor, start, end, window, margin):
    spans = []
    begin = anchor
    while begin < end:
        overlap = min(begin + window, end) - max(begin, start)
        if overlap >= margin and overlap > 0:
            spans.append((begin, begin + window, overlap))
        begin += window
    return tuple(spans)


def balance(spans):
    return min(spans[0][2], spans[-1][2]) if spans else 0


def candidate_anchors(start, window, margin, not_before, step):
    low = max(not_before, start - window + max(margin, 1))
    low = -(-low // step) * step
    return range(low, start + 1, step)


def best_plan(start, end, window=WINDOW_MINUTES, margin=DEFAULT_MARGIN, not_before=0, step=5):
    if end <= start:
        raise PlanError("the workday must end after it starts, on the same day")
    if not 0 <= margin < min(window, end - start + 1):
        raise PlanError("the margin must be at least 0 and shorter than the workday and the window")
    scored = []
    for anchor in candidate_anchors(start, window, margin, not_before, step):
        spans = useful_windows(anchor, start, end, window, margin)
        scored.append((len(spans), balance(spans), anchor, spans))
    best = max(scored, key=lambda item: (item[0], item[1], item[2]))
    tied = [item[2] for item in scored if item[0] == best[0]]
    baseline = useful_windows(start, start, end, window, margin)
    return Plan(start, end, window, margin, best[2], min(tied), max(tied), best[3], baseline)


def plan_from_workday(workday):
    return best_plan(
        parse_hhmm(workday["start"]),
        parse_hhmm(workday["end"]),
        margin=int(workday.get("margin_minutes", DEFAULT_MARGIN)),
        not_before=parse_hhmm(workday.get("not_before", "00:00")),
    )


def describe_windows(spans):
    return [f"  {format_hhmm(begin)}-{format_hhmm(end)}  {format_duration(overlap)} inside your workday" for begin, end, overlap in spans]


def render_plan(plan, days):
    lines = [f"Workday {format_hhmm(plan.start)}-{format_hhmm(plan.end)} ({format_days(days)}), {plan.window // 60}h windows", ""]
    lines.append(f"Without a warm-up: {len(plan.baseline)} windows touch your workday")
    lines += describe_windows(plan.baseline)
    lines.append("")
    if plan.gain <= 0:
        lines.append("A warm-up adds nothing for this workday: your first message already gives the best grid.")
        return "\n".join(lines)
    lines.append(f"With a warm-up at {format_hhmm(plan.anchor)}: {len(plan.windows)} windows touch your workday")
    lines += describe_windows(plan.windows)
    lines.append("")
    lines.append(f"Any warm-up between {format_hhmm(plan.earliest)} and {format_hhmm(plan.latest)} keeps {len(plan.windows)} windows.")
    return "\n".join(lines)


def minute_of_day(moment):
    return moment.hour * 60 + moment.minute


def clock(epoch, tz):
    return datetime.datetime.fromtimestamp(epoch, tz).strftime("%a %H:%M")


def decide(now, plan, days, skip_dates, state):
    epoch = now.timestamp()
    minute = minute_of_day(now)
    if now.weekday() not in days:
        return SKIP, "not a workday"
    if now.date().isoformat() in set(skip_dates or ()):
        return SKIP, "day off"
    if plan.gain <= 0:
        return SKIP, "a warm-up adds nothing for this workday"
    if minute < plan.anchor:
        return SKIP, f"before the {format_hhmm(plan.anchor)} warm-up"
    if minute > plan.end - plan.margin:
        return SKIP, "workday over"
    blocked = state.get("blocked_until")
    if blocked and epoch < blocked:
        return SKIP, f"limit reached, resets {clock(blocked, now.tzinfo)}"
    resets = state.get("five_hour_resets_at")
    if resets and epoch < resets + GRACE_SECONDS:
        return SKIP, f"window open until {clock(resets, now.tzinfo)}"
    last = state.get("last_attempt_at")
    if last and not state.get("last_ok") and epoch - last < RETRY_SECONDS:
        return SKIP, "last warm-up failed, retrying later"
    return WARM, "no open window"


def at_minute(day, minute, tz):
    return datetime.datetime.combine(day, datetime.time(), tz) + datetime.timedelta(minutes=minute)


def workdays_until(now, until, plan, days, skip_dates):
    count = 0
    day = now.date()
    skip = set(skip_dates or ())
    while at_minute(day, plan.start, now.tzinfo).timestamp() < until:
        if day.weekday() in days and day.isoformat() not in skip and at_minute(day, plan.end, now.tzinfo) > now:
            count += 1
        day += datetime.timedelta(days=1)
    return count
