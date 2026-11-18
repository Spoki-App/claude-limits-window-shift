import contextlib
import datetime
import fcntl
import json
import os
import re
import stat

APP = "claude-limits-window-shift"
LOG_LIMIT_BYTES = 512 * 1024
SEVEN_DAY_HISTORY = 8


class StoreError(Exception):
    pass


def config_path():
    return os.environ.get("CLWS_CONFIG") or os.path.join(os.path.expanduser("~"), ".config", APP, "config.json")


def data_dir():
    return os.path.dirname(config_path())


def ensure_dir(path):
    os.makedirs(path, exist_ok=True)
    os.chmod(path, 0o700)
    return path


def load_config():
    path = config_path()
    if not os.path.exists(path):
        return {}
    mode = stat.S_IMODE(os.stat(path).st_mode)
    if mode & 0o077:
        raise StoreError(f"CONFIG_INSECURE: {path} is readable by others ({oct(mode)}). Fix: chmod 600 {path}")
    try:
        with open(path) as handle:
            return json.load(handle)
    except (OSError, ValueError) as error:
        raise StoreError(f"CONFIG_INVALID: cannot parse {path} ({type(error).__name__})")


def write_private(path, data):
    ensure_dir(os.path.dirname(path))
    temporary = path + ".tmp"
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w") as handle:
        json.dump(data, handle, indent=2, sort_keys=True)
    os.replace(temporary, path)
    os.chmod(path, 0o600)
    return path


def save_config(config):
    return write_private(config_path(), config)


def safe_name(name):
    return re.sub(r"[^A-Za-z0-9_.-]", "_", name) or "default"


def state_path(profile):
    return os.path.join(data_dir(), "state", safe_name(profile) + ".json")


def load_state(profile):
    try:
        with open(state_path(profile)) as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return {}


def save_state(profile, state):
    return write_private(state_path(profile), state)


def log_path():
    return os.path.join(data_dir(), "log.jsonl")


def append_log(entry):
    path = log_path()
    ensure_dir(os.path.dirname(path))
    if os.path.exists(path) and os.path.getsize(path) > LOG_LIMIT_BYTES:
        os.replace(path, path + ".1")
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(descriptor, "a") as handle:
        handle.write(json.dumps({"at": stamp, **entry}, sort_keys=True) + "\n")


def read_log(limit=20):
    try:
        with open(log_path()) as handle:
            lines = handle.readlines()[-limit:]
    except OSError:
        return []
    entries = []
    for line in lines:
        try:
            entries.append(json.loads(line))
        except ValueError:
            continue
    return entries


def workdir():
    return ensure_dir(os.path.join(data_dir(), "empty"))


@contextlib.contextmanager
def exclusive():
    ensure_dir(data_dir())
    descriptor = os.open(os.path.join(data_dir(), "tick.lock"), os.O_WRONLY | os.O_CREAT, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(descriptor)
        yield False
        return
    try:
        yield True
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def record(state, outcome, now_epoch, window_seconds):
    state["last_attempt_at"] = int(now_epoch)
    state["last_ok"] = bool(outcome.get("ok"))
    state["last_error"] = outcome.get("error")
    state["last_error_kind"] = outcome.get("error_kind")
    if outcome.get("tokens") is not None:
        state["last_tokens"] = outcome["tokens"]
    if outcome.get("ok"):
        state["last_ok_at"] = int(now_epoch)
    record_windows(state, outcome.get("windows") or {}, now_epoch, window_seconds, outcome.get("ok"))
    record_rejection(state, outcome, now_epoch)
    return state


def record_windows(state, windows, now_epoch, window_seconds, ok):
    five = windows.get("five_hour") or {}
    if five.get("resets_at"):
        state["five_hour_resets_at"] = five["resets_at"]
        state["five_hour_estimated"] = False
    elif ok and (state.get("five_hour_resets_at") or 0) <= now_epoch:
        state["five_hour_resets_at"] = int(now_epoch + window_seconds)
        state["five_hour_estimated"] = True
    if five.get("used_percent") is not None:
        state["five_hour_used_percent"] = five["used_percent"]
    seven = windows.get("seven_day") or {}
    if seven.get("used_percent") is not None:
        state["seven_day_used_percent"] = seven["used_percent"]
        state["seven_day_observed_at"] = int(now_epoch)
    if seven.get("resets_at"):
        history = [value for value in state.get("seven_day_resets", []) if value != seven["resets_at"]]
        state["seven_day_resets"] = (history + [seven["resets_at"]])[-SEVEN_DAY_HISTORY:]
    if ok and windows:
        state["limits_seen"] = sorted(windows)


def record_rejection(state, outcome, now_epoch):
    if outcome.get("status") != "rejected":
        state.pop("blocked_until", None)
        return
    window = (outcome.get("windows") or {}).get(outcome.get("rejected") or "", {})
    state["blocked_until"] = window.get("resets_at") or int(now_epoch + 3600)
