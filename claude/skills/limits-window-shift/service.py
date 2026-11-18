import os
import plistlib
import subprocess
import sys

import planner

LABEL = "com.spoki.claude-limits-window-shift"
UNIT = "claude-limits-window-shift"
INTERVAL_SECONDS = 300
CLOUD_OFFSET_MINUTES = 7


def launch_agent_path(home=None):
    return os.path.join(home or os.path.expanduser("~"), "Library", "LaunchAgents", LABEL + ".plist")


def systemd_dir(home=None):
    return os.path.join(home or os.path.expanduser("~"), ".config", "systemd", "user")


def tick_command(python, script):
    return [python, "-I", script, "tick"]


def launchd_plist(python, script, path_env, log_file, environment=None):
    env = {"PATH": path_env}
    env.update(environment or {})
    return plistlib.dumps({
        "Label": LABEL,
        "ProgramArguments": tick_command(python, script),
        "StartInterval": INTERVAL_SECONDS,
        "RunAtLoad": True,
        "ProcessType": "Background",
        "EnvironmentVariables": env,
        "StandardOutPath": log_file,
        "StandardErrorPath": log_file,
    })


def systemd_units(python, script, path_env, environment=None):
    env = {"PATH": path_env}
    env.update(environment or {})
    lines = "".join(f'Environment="{key}={value}"\n' for key, value in sorted(env.items()))
    command = " ".join(tick_command(python, script))
    service = f"[Unit]\nDescription=Claude limits window shift tick\n\n[Service]\nType=oneshot\n{lines}ExecStart={command}\n"
    timer = (
        "[Unit]\nDescription=Claude limits window shift every 5 minutes\n\n"
        f"[Timer]\nOnBootSec=1min\nOnUnitActiveSec={INTERVAL_SECONDS}s\nPersistent=true\n\n[Install]\nWantedBy=timers.target\n"
    )
    return service, timer


def run(command, runner):
    runner = runner or subprocess.run
    try:
        done = runner(command, capture_output=True, text=True)
    except OSError as error:
        return False, str(error)
    return done.returncode == 0, (done.stderr or done.stdout or "").strip()


def write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    mode = "wb" if isinstance(data, bytes) else "w"
    with open(path, mode) as handle:
        handle.write(data)


def install(python, script, path_env, log_file, environment=None, runner=None, platform=sys.platform, home=None):
    if platform == "darwin":
        path = launch_agent_path(home)
        write(path, launchd_plist(python, script, path_env, log_file, environment))
        domain = f"gui/{os.getuid()}"
        run(["launchctl", "bootout", f"{domain}/{LABEL}"], runner)
        ok, detail = run(["launchctl", "bootstrap", domain, path], runner)
        return ok, path, detail
    if platform.startswith("linux"):
        service, timer = systemd_units(python, script, path_env, environment)
        folder = systemd_dir(home)
        write(os.path.join(folder, UNIT + ".service"), service)
        write(os.path.join(folder, UNIT + ".timer"), timer)
        run(["systemctl", "--user", "daemon-reload"], runner)
        ok, detail = run(["systemctl", "--user", "enable", "--now", UNIT + ".timer"], runner)
        return ok, folder, detail
    return False, "", f"unsupported platform {platform}: run '{' '.join(tick_command(python, script))}' every 5 minutes"


def uninstall(runner=None, platform=sys.platform, home=None):
    if platform == "darwin":
        run(["launchctl", "bootout", f"gui/{os.getuid()}/{LABEL}"], runner)
        path = launch_agent_path(home)
        removed = remove(path)
        return removed, path
    if platform.startswith("linux"):
        run(["systemctl", "--user", "disable", "--now", UNIT + ".timer"], runner)
        folder = systemd_dir(home)
        removed = [remove(os.path.join(folder, UNIT + suffix)) for suffix in (".service", ".timer")]
        run(["systemctl", "--user", "daemon-reload"], runner)
        return any(removed), folder
    return False, ""


def remove(path):
    try:
        os.remove(path)
        return True
    except FileNotFoundError:
        return False


def installed(runner=None, platform=sys.platform, home=None):
    if platform == "darwin":
        ok, _ = run(["launchctl", "print", f"gui/{os.getuid()}/{LABEL}"], runner)
        return ok
    if platform.startswith("linux"):
        ok, _ = run(["systemctl", "--user", "is-active", "--quiet", UNIT + ".timer"], runner)
        return ok
    return False


def scheduled_script(platform=sys.platform, home=None):
    try:
        if platform == "darwin":
            with open(launch_agent_path(home), "rb") as handle:
                return plistlib.load(handle)["ProgramArguments"][2]
        if platform.startswith("linux"):
            with open(os.path.join(systemd_dir(home), UNIT + ".service")) as handle:
                line = next(text for text in handle if text.startswith("ExecStart="))
            return line.split()[2]
    except (OSError, KeyError, IndexError, StopIteration, plistlib.InvalidFileException):
        return None
    return None


def cron_days(days):
    return ",".join(str(a) if a == b else f"{a}-{b}" for a, b in planner.runs((day + 1) % 7 for day in days))


def cloud_minute(plan):
    minute = plan.anchor + CLOUD_OFFSET_MINUTES
    return minute if minute <= plan.latest else plan.anchor


def cloud_cron(plan, days, timezone):
    minute = cloud_minute(plan)
    return f"CRON_TZ={timezone} {minute % 60} {minute // 60} * * {cron_days(days)}"
