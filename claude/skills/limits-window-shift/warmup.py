import json
import os
import re
import subprocess

PROMPT = "OK"
SYSTEM_PROMPT = "Reply with the single word OK."
TIMEOUT_SECONDS = 120
FIVE_HOUR = "five_hour"
SEVEN_DAY = "seven_day"
ENV_KEEP = (
    "HOME", "USER", "LOGNAME", "PATH", "SHELL", "LANG", "LC_ALL", "LC_CTYPE", "TMPDIR", "TZ",
    "HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY", "SSL_CERT_FILE", "NODE_EXTRA_CA_CERTS",
)
OPTIONAL_FLAGS = (
    ("--tools", ""),
    ("--strict-mcp-config",),
    ("--setting-sources", ""),
    ("--safe-mode",),
    ("--disable-slash-commands",),
    ("--no-session-persistence",),
)
AUTH_PATTERN = re.compile(r"not logged in|/login|failed to authenticate|oauth|invalid api key|unauthori[sz]ed", re.IGNORECASE)
TOKENISH = re.compile(r"sk-ant-[A-Za-z0-9_-]+|Bearer\s+[A-Za-z0-9._-]+|[A-Za-z0-9_-]{40,}")


def redact(text):
    return TOKENISH.sub("[redacted]", str(text or ""))[:400]


def clean_env(config_dir, source=None):
    source = os.environ if source is None else source
    env = {key: source[key] for key in ENV_KEEP if key in source}
    if config_dir:
        env["CLAUDE_CONFIG_DIR"] = os.path.expanduser(config_dir)
    return env


def supported_flags(help_text):
    return [flag for flag in OPTIONAL_FLAGS if re.search(rf"(^|\s){re.escape(flag[0])}(\s|,|$)", help_text, re.MULTILINE)]


def build_command(claude, help_text, model="haiku"):
    command = [claude, "-p", PROMPT, "--model", model, "--system-prompt", SYSTEM_PROMPT]
    for flag in supported_flags(help_text):
        command.extend(flag)
    command += ["--max-turns", "1", "--output-format", "stream-json", "--verbose"]
    return command


def pick(mapping, *names):
    for name in names:
        if isinstance(mapping, dict) and mapping.get(name) is not None:
            return mapping[name]
    return None


def percent(value):
    if value is None:
        return None
    value = float(value)
    return round(value * 100, 1) if value <= 1 else round(value, 1)


def merge_window(windows, name, resets_at, utilization):
    window = windows.setdefault(name, {})
    if resets_at is not None:
        window["resets_at"] = int(resets_at)
    if utilization is not None:
        window["used_percent"] = percent(utilization)


def absorb_rate_limit(outcome, info):
    limit_type = pick(info, "rateLimitType", "rate_limit_type")
    status = pick(info, "status")
    if limit_type:
        merge_window(outcome["windows"], limit_type, pick(info, "resetsAt", "resets_at"), pick(info, "utilization"))
    for name, window in (pick(info, "unifiedWindows", "unified_windows") or {}).items():
        merge_window(outcome["windows"], name, pick(window, "resetsAt", "resets_at"), pick(window, "utilization"))
    if status == "rejected":
        outcome["rejected"] = limit_type or "unknown"
    if status:
        outcome["status"] = "rejected" if outcome.get("rejected") else status


def absorb_result(outcome, event):
    usage = event.get("usage") or {}
    keys = ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens")
    outcome["tokens"] = sum(int(usage.get(key) or 0) for key in keys)
    if event.get("is_error") or event.get("subtype") not in (None, "success"):
        message = redact(event.get("result") or event.get("subtype"))
        outcome["error"] = message
        outcome["error_kind"] = "auth" if AUTH_PATTERN.search(message) else "claude"
    else:
        outcome["ok"] = True


def parse_stream(text):
    outcome = {"ok": False, "error": None, "error_kind": None, "status": None, "rejected": None, "windows": {}, "tokens": None}
    for line in (text or "").splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        if event.get("type") == "rate_limit_event":
            absorb_rate_limit(outcome, event.get("rate_limit_info") or {})
        elif event.get("type") == "result":
            absorb_result(outcome, event)
    return outcome


def claude_help(claude, env, runner=None):
    runner = runner or subprocess.run
    try:
        done = runner([claude, "--help"], capture_output=True, text=True, timeout=30, env=env, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return ""
    return done.stdout or ""


def run(claude, config_dir, workdir, runner=None, timeout=TIMEOUT_SECONDS):
    runner = runner or subprocess.run
    env = clean_env(config_dir)
    command = build_command(claude, claude_help(claude, env, runner))
    try:
        done = runner(command, capture_output=True, text=True, timeout=timeout, env=env, cwd=workdir, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return failure("timeout", f"claude did not answer within {timeout}s")
    except OSError as error:
        return failure("claude", f"cannot run {claude}: {error.strerror or error}")
    outcome = parse_stream(done.stdout)
    if not outcome["ok"] and not outcome["error"]:
        outcome["error"] = redact(done.stderr.strip() or f"claude exited with code {done.returncode}")
        outcome["error_kind"] = "auth" if AUTH_PATTERN.search(outcome["error"]) else "claude"
    return outcome


def failure(kind, message):
    return {"ok": False, "error": redact(message), "error_kind": kind, "status": None, "rejected": None, "windows": {}, "tokens": None}


def auth_status(claude, config_dir, runner=None):
    runner = runner or subprocess.run
    try:
        done = runner([claude, "auth", "status"], capture_output=True, text=True, timeout=30, env=clean_env(config_dir), stdin=subprocess.DEVNULL)
        data = json.loads(done.stdout)
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    return {"logged_in": bool(data.get("loggedIn")), "method": data.get("authMethod")}
