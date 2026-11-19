#!/usr/bin/env python3
import argparse
import datetime
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import urllib.request
import uuid
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, HERE)

import planner  # noqa: E402
import service  # noqa: E402
import store  # noqa: E402
import warmup  # noqa: E402

REPO = "Spoki-App/claude-limits-window-shift"
EXIT_CONFIG, EXIT_WARMUP, EXIT_AUTH = 2, 3, 4
WEEK_SECONDS = 7 * 24 * 3600
DRIFT_TOLERANCE_SECONDS = 3600
CLOUD_TOOLS = ["Read"]
CLOUD_MODEL = "claude-haiku-5-5"
CLOUD_PROMPT = "Reply with the single word OK. Do not use any tool."
ROUTINE_NAMES = {
    service.GUARD: "claude-limits-window-shift night guard",
    service.WARM_UP: "claude-limits-window-shift warm-up",
    service.LATE: "claude-limits-window-shift late warm-up",
}
ROUTINE_PURPOSES = {
    service.GUARD: "keeps night sessions inside a window that ends before the warm-up",
    service.WARM_UP: "opens the first window of the workday",
    service.LATE: "opens the window when a night session still had one open at the warm-up",
}
INSTALL_CLAUDE = "install it with curl -fsSL https://claude.ai/install.sh | bash"
CLAUDE_CANDIDATES = ("~/.local/bin/claude", "/opt/homebrew/bin/claude", "/usr/local/bin/claude", "~/.claude/local/claude")


class CliError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def version():
    try:
        with open(os.path.join(HERE, "VERSION")) as handle:
            return handle.read().strip()
    except OSError:
        return "unknown"


def latest_release():
    request = urllib.request.Request(f"https://api.github.com/repos/{REPO}/releases/latest", headers={"Accept": "application/vnd.github+json"})
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return json.load(response).get("tag_name", "").lstrip("v") or None
    except Exception:
        return None


def system_timezone():
    if os.environ.get("TZ"):
        return os.environ["TZ"]
    try:
        target = os.path.realpath("/etc/localtime")
    except OSError:
        return "UTC"
    marker = "zoneinfo/"
    return target.split(marker, 1)[1] if marker in target else "UTC"


def zone(name):
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        raise CliError(EXIT_CONFIG, f"unknown timezone {name!r}, use an IANA name like Europe/Rome")


def find_claude(preferred=None):
    for candidate in (preferred, shutil.which("claude"), *CLAUDE_CANDIDATES):
        if candidate and os.access(os.path.expanduser(candidate), os.X_OK):
            return os.path.expanduser(candidate)
    return None


def load_config(required=True):
    try:
        config = store.load_config()
    except store.StoreError as error:
        raise CliError(EXIT_CONFIG, str(error))
    if required and not config.get("workday"):
        raise CliError(EXIT_CONFIG, f"not set up yet: run {self_command('setup --start 09:00 --end 18:00')}")
    return config


def cloud_fires(workday, plan):
    return service.cloud_fires(plan, planner.parse_hhmm(workday.get("not_before", "00:00")))


def workday_plan(workday):
    try:
        return planner.plan_from_workday(workday), planner.parse_days(workday.get("days", "mon-fri"))
    except planner.PlanError as error:
        raise CliError(EXIT_CONFIG, str(error))


def selected_profiles(config, name=None):
    profiles = config.get("profiles") or [{"name": "default", "config_dir": None}]
    if not name:
        return profiles
    chosen = [profile for profile in profiles if profile["name"] == name]
    if not chosen:
        raise CliError(EXIT_CONFIG, f"unknown profile {name!r}; known: {', '.join(p['name'] for p in profiles)}")
    return chosen


def claude_path(config):
    path = find_claude(config.get("claude"))
    if not path:
        raise CliError(EXIT_CONFIG, f"claude CLI not found: {INSTALL_CLAUDE}, or {self_command('setup --claude /path/to/claude')}")
    return path


def self_command(arguments):
    return f"python3 -I {shlex.quote(os.path.realpath(__file__))} {arguments}"


def login_hint(profile, config):
    prefix = f"CLAUDE_CONFIG_DIR={shlex.quote(profile['config_dir'])} " if profile.get("config_dir") else ""
    return f"{prefix}{shlex.quote(find_claude(config.get('claude')) or 'claude')} auth login"


def upsert_profile(config, name, config_dir):
    profiles = [p for p in config.get("profiles", []) if p["name"] != name]
    profiles.append({"name": name, "config_dir": config_dir})
    config["profiles"] = profiles


def real_config_dir(config_dir):
    return os.path.realpath(os.path.expanduser(config_dir or "~/.claude"))


def profile_name_for(config, config_dir):
    if not config.get("profiles"):
        return "default"
    relative = os.path.relpath(real_config_dir(config_dir), os.path.realpath(os.path.expanduser("~")))
    base = re.sub(r"[^a-z0-9]+", "-", relative.lower()).strip("-") or "profile"
    names = {p["name"] for p in config["profiles"]}
    return next(name for name in (base, *(f"{base}-{n}" for n in range(2, 100))) if name not in names)


def ensure_profile(config, config_dir):
    if any(real_config_dir(p.get("config_dir")) == real_config_dir(config_dir) for p in config.get("profiles", [])):
        return
    upsert_profile(config, profile_name_for(config, config_dir), config_dir)


def apply_workday_args(workday, args):
    for key, value in (("start", args.start), ("end", args.end), ("days", args.days), ("timezone", args.timezone), ("not_before", args.not_before)):
        if value is not None:
            workday[key] = value
    if args.margin is not None:
        workday["margin_minutes"] = args.margin
    if args.clear_skip_dates:
        workday["skip_dates"] = []
    if args.skip_date:
        workday["skip_dates"] = sorted(set(workday.get("skip_dates", [])) | set(args.skip_date))
    workday.setdefault("days", "mon-fri")
    workday.setdefault("timezone", system_timezone())
    workday.setdefault("margin_minutes", planner.DEFAULT_MARGIN)
    if not workday.get("start") or not workday.get("end"):
        raise CliError(EXIT_CONFIG, "first setup needs --start and --end, e.g. --start 09:00 --end 18:00")


def cmd_setup(args):
    config = load_config(required=False)
    workday = dict(config.get("workday") or {})
    apply_workday_args(workday, args)
    zone(workday["timezone"])
    plan, days = workday_plan(workday)
    config["workday"] = workday
    config_dir = args.config_dir or os.environ.get("CLAUDE_CONFIG_DIR")
    if args.remove_profile:
        config["profiles"] = [p for p in config.get("profiles", []) if p["name"] != args.remove_profile]
    elif args.profile:
        upsert_profile(config, args.profile, config_dir)
    else:
        ensure_profile(config, config_dir)
    config["claude"] = find_claude(args.claude or config.get("claude"))
    config["version"] = 1
    path = store.save_config(config)
    print(f"SAVED {path} profiles={','.join(p['name'] for p in config['profiles'])}")
    print()
    print(planner.render_plan(plan, days))
    print()
    print(f"Next: {self_command('install')}, then create the cloud warm-up shown by {self_command('cloud-spec')}")


def cmd_plan(args):
    config = load_config(required=False)
    workday = dict(config.get("workday") or {})
    for key, value in (("start", args.start), ("end", args.end), ("days", args.days), ("not_before", args.not_before)):
        if value is not None:
            workday[key] = value
    if args.margin is not None:
        workday["margin_minutes"] = args.margin
    if not workday.get("start") or not workday.get("end"):
        raise CliError(EXIT_CONFIG, "pass --start and --end, or run setup first")
    plan, days = workday_plan(workday)
    print(planner.render_plan(plan, days))
    if args.night_run:
        print()
        print(planner.render_night(plan, cloud_fires(workday, plan), night_sessions(args.night_run, plan)))


def night_sessions(times, plan):
    try:
        return [planner.night_minute(text, plan) for text in times]
    except planner.PlanError as error:
        raise CliError(EXIT_CONFIG, str(error))


def now_in(config, text=None):
    tz = zone(config["workday"].get("timezone", "UTC"))
    if not text:
        return datetime.datetime.now(tz)
    moment = datetime.datetime.fromisoformat(text)
    return moment.replace(tzinfo=tz) if moment.tzinfo is None else moment.astimezone(tz)


def notify(title, message, runner=None):
    if sys.platform != "darwin":
        return
    runner = runner or subprocess.run
    script = f"display notification {json.dumps(message)} with title {json.dumps(title)}"
    try:
        runner(["osascript", "-e", script], capture_output=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        pass


def warm_profile(config, profile, now, runner=None):
    plan, _ = workday_plan(config["workday"])
    outcome = warmup.run(claude_path(config), profile.get("config_dir"), store.workdir(), runner=runner)
    state = store.record(store.load_state(profile["name"]), outcome, now.timestamp(), plan.window * 60)
    if outcome.get("error_kind") == "auth" and state.get("notified_on") != now.date().isoformat():
        state["notified_on"] = now.date().isoformat()
        notify("Claude window shift", f"Profile {profile['name']} is logged out. Run: {login_hint(profile, config)}")
    store.save_state(profile["name"], state)
    store.append_log({"profile": profile["name"], "action": "warm", "ok": outcome["ok"], "error": outcome.get("error"),
                      "status": outcome.get("status"), "tokens": outcome.get("tokens"), "windows": outcome.get("windows")})
    return outcome, state


def outcome_line(profile, outcome, tz, config):
    if outcome["ok"]:
        five = (outcome.get("windows") or {}).get("five_hour", {})
        until = f", window until {planner.clock(five['resets_at'], tz)}" if five.get("resets_at") else ""
        return f"WARMED profile={profile['name']} tokens={outcome.get('tokens')}{until}"
    hint = f" Fix: {login_hint(profile, config)}" if outcome.get("error_kind") == "auth" else ""
    return f"WARMUP_FAILED profile={profile['name']} kind={outcome.get('error_kind')}: {outcome.get('error')}{hint}"


def cmd_warm(args):
    config = load_config()
    now = now_in(config)
    worst = 0
    for profile in selected_profiles(config, args.profile):
        if args.dry_run:
            print(" ".join(warmup.build_command(claude_path(config), "")))
            continue
        outcome, _ = warm_profile(config, profile, now)
        print(outcome_line(profile, outcome, now.tzinfo, config))
        if not outcome["ok"]:
            worst = max(worst, EXIT_AUTH if outcome.get("error_kind") == "auth" else EXIT_WARMUP)
    return worst


def cmd_tick(args):
    config = load_config()
    plan, days = workday_plan(config["workday"])
    now = now_in(config, args.now)
    with store.exclusive() as locked:
        if not locked:
            print("SKIP another tick is running")
            return 0
        for profile in selected_profiles(config, args.profile):
            state = store.load_state(profile["name"])
            decision, reason = planner.decide(now, plan, days, config["workday"].get("skip_dates"), state)
            if decision != planner.WARM or args.dry_run:
                print(f"{decision.upper()} profile={profile['name']} {reason}")
                continue
            outcome, _ = warm_profile(config, profile, now)
            print(outcome_line(profile, outcome, now.tzinfo, config))
    return 0


def describe_five_hour(state, now, tz):
    resets = state.get("five_hour_resets_at")
    if not resets:
        return "unknown until the first warm-up"
    if now.timestamp() >= resets:
        return f"closed since {planner.clock(resets, tz)}, the next message opens a new one"
    used = state.get("five_hour_used_percent")
    detail = f", {used:g}% used" if used is not None else ""
    estimated = " (estimated)" if state.get("five_hour_estimated") else ""
    return f"open until {planner.clock(resets, tz)}{estimated}{detail}"


def weekly_drift(resets):
    for earlier, later in zip(resets, resets[1:]):
        offset = (later - earlier) % WEEK_SECONDS
        if DRIFT_TOLERANCE_SECONDS < offset < WEEK_SECONDS - DRIFT_TOLERANCE_SECONDS:
            return earlier, later
    return None


def describe_weekly(state, now, plan, days, skip_dates):
    resets = state.get("seven_day_resets") or []
    used = state.get("seven_day_used_percent")
    if not resets and used is None:
        return ["weekly: not observed yet"]
    parts = []
    if used is not None:
        parts.append(f"{used:g}% used")
    upcoming = resets[-1] if resets else None
    if upcoming and upcoming > now.timestamp():
        left = planner.workdays_until(now, upcoming, plan, days, skip_dates)
        parts.append(f"resets {planner.clock(upcoming, now.tzinfo)}")
        parts.append(f"{left} workday{'s' if left != 1 else ''} left")
        if used is not None and left:
            parts.append(f"about {max(0.0, 100 - used) / left:.0f}% per workday")
    lines = ["weekly: " + ", ".join(parts)]
    drift = weekly_drift(resets)
    if drift:
        lines.append(f"weekly reset moved from {planner.clock(drift[0], now.tzinfo)} to {planner.clock(drift[1], now.tzinfo)}: it may be rolling again")
    return lines


def describe_last(state, profile, tz, config):
    last = state.get("last_attempt_at")
    if not last:
        return "last warm-up: none yet"
    when = planner.clock(last, tz)
    if state.get("last_ok"):
        return f"last warm-up: {when} ok, {state.get('last_tokens')} tokens"
    hint = f" Fix: {login_hint(profile, config)}" if state.get("last_error_kind") == "auth" else ""
    return f"last warm-up: {when} failed ({state.get('last_error_kind')}): {state.get('last_error')}{hint}"


def cmd_status(args):
    config = load_config()
    plan, days = workday_plan(config["workday"])
    workday = config["workday"]
    now = now_in(config)
    resets = " ".join(planner.format_hhmm(r) for r in plan.resets)
    print(f"claude-limits-window-shift {version()}")
    print(f"Workday {workday['start']}-{workday['end']} {planner.format_days(days)} ({workday['timezone']}), warm-up {planner.format_hhmm(plan.anchor)}, resets {resets}")
    print(f"Scheduler: {'installed' if service.installed() else 'not installed, run ' + self_command('install')}")
    print(f"Cloud warm-up: {service.cloud_cron(plan, days, workday['timezone'])} (create it once, see cloud-spec)")
    helpers = [f"{role} {planner.format_hhmm(minute % 1440)}" for role, minute in cloud_fires(workday, plan) if role != service.WARM_UP]
    if helpers:
        print(f"Cloud helpers: {', '.join(helpers)} (create them once too, see cloud-spec)")
    for profile in selected_profiles(config, args.profile):
        state = store.load_state(profile["name"])
        decision, reason = planner.decide(now, plan, days, workday.get("skip_dates"), state)
        print()
        print(f"[{profile['name']}] {profile.get('config_dir') or '~/.claude'}")
        print(f"  5h window: {describe_five_hour(state, now, now.tzinfo)}")
        print(f"  next tick: {'warm-up' if decision == planner.WARM else 'skip'} ({reason})")
        print(f"  {describe_last(state, profile, now.tzinfo, config)}")
        for line in describe_weekly(state, now, plan, days, workday.get("skip_dates")):
            print(f"  {line}")
    return 0


def launch_path_env(claude):
    folders = [os.path.dirname(claude)] if claude else []
    folders += ["/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin", "/usr/sbin", "/sbin"]
    return ":".join(dict.fromkeys(folders))


def cmd_install(args):
    config = load_config()
    claude = claude_path(config)
    python = shutil.which("python3") or sys.executable
    environment = {"CLWS_CONFIG": store.config_path()} if os.environ.get("CLWS_CONFIG") else {}
    log_file = os.path.join(store.ensure_dir(store.data_dir()), "scheduler.log")
    ok, where, detail = service.install(python, os.path.realpath(__file__), launch_path_env(claude), log_file, environment)
    if not ok:
        raise CliError(EXIT_CONFIG, f"INSTALL_FAILED {where} {detail}".strip())
    print(f"INSTALLED {where} (tick every {service.INTERVAL_SECONDS // 60} minutes)")
    print(f"CLI {os.path.realpath(__file__)}")
    print(f"The laptop only ticks while awake: add the cloud warm-up for the early anchor, see {self_command('cloud-spec')}")
    return 0


def cmd_uninstall(_args):
    removed, where = service.uninstall()
    print(f"{'UNINSTALLED' if removed else 'NOT_INSTALLED'} {where}".strip())
    return 0


def cloud_spec(config):
    workday = config["workday"]
    plan, days = workday_plan(workday)
    timezone = workday["timezone"]
    minute = service.cloud_minute(plan)
    routines = cloud_routines(cloud_fires(workday, plan), days, timezone)
    return {
        "cron": service.cloud_cron(plan, days, timezone),
        "local_time": planner.format_hhmm(minute),
        "days": planner.format_days(days),
        "timezone": timezone,
        "prompt": CLOUD_PROMPT,
        "model": "the smallest available (Haiku)",
        "repository": None,
        "tools": CLOUD_TOOLS,
        "connectors": [],
        "useful_until": planner.format_hhmm(plan.latest),
        "routine": next(entry["routine"] for entry in routines if entry["role"] == service.WARM_UP),
        "routines": routines,
    }


def cloud_routines(fires, days, timezone):
    routines = []
    for role, minute in fires:
        cron = service.cron_at(minute, days, timezone)
        routines.append({
            "role": role,
            "local_time": planner.format_hhmm(minute % 1440),
            "cron": cron,
            "purpose": ROUTINE_PURPOSES[role],
            "routine": routine_body(ROUTINE_NAMES[role], cron),
        })
    return routines


def routine_body(name, cron):
    message = {"role": "user", "content": CLOUD_PROMPT}
    event = {"uuid": str(uuid.uuid4()), "session_id": "", "type": "user", "parent_tool_use_id": None, "message": message}
    context = {"model": CLOUD_MODEL, "sources": [], "allowed_tools": CLOUD_TOOLS}
    return {
        "name": name,
        "cron_expression": cron,
        "enabled": True,
        "clear_mcp_connections": True,
        "job_config": {"ccr": {"environment_id": "ENVIRONMENT_ID", "session_context": context, "events": [{"data": event}]}},
    }


def cmd_cloud_spec(args):
    spec = cloud_spec(load_config())
    if args.json:
        print(json.dumps(spec, indent=2))
        return 0
    print("Cloud warm-up: Claude Code scheduled tasks that open the first window while your laptop sleeps.")
    for entry in spec["routines"]:
        print(f"  {entry['routine']['name']}")
        print(f"    Schedule: {entry['cron']}  ({entry['local_time']})")
        print(f"    Why:      {entry['purpose']}")
    print("  Each one:")
    print(f"    Prompt:     {spec['prompt']}")
    print(f"    Model:      {spec['model']}")
    print(f"    Tools:      {', '.join(spec['tools'])} only (an empty list is replaced by the full default set)")
    print("    Repository, connectors: none")
    print(f"  A warm-up that starts late still helps until {spec['useful_until']}.")
    print("Create them once from a Claude Code session logged in to the same account (/schedule),")
    print("or from the scheduled tasks page of Claude Code on the web. Then check their tools and connectors.")
    return 0


def check(ok, label, fix="", soft=False):
    status = "OK  " if ok else "WARN" if soft else "FAIL"
    print(f"{status} {label}{'' if ok or not fix else '. Fix: ' + fix}")
    return ok or soft


def doctor_profiles(config, claude):
    results = []
    for profile in selected_profiles(config):
        auth = warmup.auth_status(claude, profile.get("config_dir"))
        logged = bool(auth and auth["logged_in"])
        method = f" ({auth['method']})" if logged and auth.get("method") else ""
        results.append(check(logged, f"profile {profile['name']} logged in{method}", login_hint(profile, config)))
    return results


def cmd_doctor(_args):
    config = load_config()
    results = [check(sys.version_info >= (3, 9), f"python {sys.version.split()[0]}", "install Python 3.9 or newer")]
    try:
        zone(config["workday"]["timezone"])
        results.append(check(True, f"timezone {config['workday']['timezone']}"))
    except CliError as error:
        results.append(check(False, str(error), self_command("setup --timezone Europe/Rome")))
    claude = find_claude(config.get("claude"))
    results.append(check(bool(claude), f"claude CLI {claude or 'not found'}", INSTALL_CLAUDE))
    if claude:
        help_text = warmup.claude_help(claude, warmup.clean_env(None))
        missing = [flag[0] for flag in warmup.OPTIONAL_FLAGS if flag not in warmup.supported_flags(help_text)]
        results.append(check(not missing, "minimal warm-up flags supported" + (f", missing {' '.join(missing)}" if missing else ""), "update Claude Code (warm-ups still work, they just load more context)", soft=True))
        results += doctor_profiles(config, claude)
    results.append(check(service.installed(), "scheduler installed", self_command("install")))
    scheduled = service.scheduled_script()
    if scheduled:
        current = scheduled == os.path.realpath(__file__)
        results.append(check(current, f"scheduler runs {scheduled}", self_command("install") + ", from the copy you keep"))
    return 0 if all(results) else 1


def cmd_version(_args):
    latest = latest_release()
    print(f"claude-limits-window-shift {version()}, latest release {latest or 'unknown'}")
    if latest and latest != version():
        print(f"UPDATE_AVAILABLE: curl -fsSL https://github.com/{REPO}/releases/latest/download/install.sh | sh")
    return 0


def workday_arguments(parser):
    parser.add_argument("--start", help="work start, HH:MM")
    parser.add_argument("--end", help="work end, HH:MM")
    parser.add_argument("--days", help="work days, e.g. mon-fri or mon,wed-fri")
    parser.add_argument("--margin", type=int, help="minutes a window must spend inside the workday to count (default 30)")
    parser.add_argument("--not-before", help="earliest warm-up time, HH:MM")


def build_parser():
    parser = argparse.ArgumentParser(prog="window_shift.py", description="Start Claude's 5-hour usage windows at the times that fit your workday.")
    sub = parser.add_subparsers(dest="command", required=True)
    setup = sub.add_parser("setup", help="save your workday and the Claude profiles to warm up")
    workday_arguments(setup)
    setup.add_argument("--timezone", help="IANA timezone, default the system one")
    setup.add_argument("--skip-date", action="append", help="a day off, YYYY-MM-DD (repeatable)")
    setup.add_argument("--clear-skip-dates", action="store_true")
    setup.add_argument("--profile", help="profile name to add or update")
    setup.add_argument("--config-dir", help="CLAUDE_CONFIG_DIR of that profile, default the current one")
    setup.add_argument("--remove-profile")
    setup.add_argument("--claude", help="path to the claude CLI")
    plan = sub.add_parser("plan", help="show the best warm-up time for a workday")
    workday_arguments(plan)
    plan.add_argument("--night-run", action="append", help="another session before work, HH:MM (repeatable): shows what the cloud tasks do about it")
    for name, text in (("status", "show windows, next action and weekly pace"), ("warm", "send one warm-up now"), ("tick", "what the scheduler runs every 5 minutes")):
        command = sub.add_parser(name, help=text)
        command.add_argument("--profile")
        if name in ("warm", "tick"):
            command.add_argument("--dry-run", action="store_true")
        if name == "tick":
            command.add_argument("--now", help="pretend it is this local time, ISO format")
    sub.add_parser("install", help="run tick every 5 minutes (launchd or systemd)")
    sub.add_parser("uninstall", help="remove the scheduler")
    sub.add_parser("cloud-spec", help="the cloud scheduled task to create for the early warm-up").add_argument("--json", action="store_true")
    sub.add_parser("doctor", help="check claude, login, flags and scheduler")
    sub.add_parser("version", help="print the version and check for updates")
    return parser


COMMANDS = {
    "setup": cmd_setup, "plan": cmd_plan, "status": cmd_status, "warm": cmd_warm, "tick": cmd_tick, "install": cmd_install,
    "uninstall": cmd_uninstall, "cloud-spec": cmd_cloud_spec, "doctor": cmd_doctor, "version": cmd_version,
}


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return COMMANDS[args.command](args) or 0
    except CliError as error:
        print(f"ERROR {error}", file=sys.stderr)
        return error.code


if __name__ == "__main__":
    sys.exit(main())
