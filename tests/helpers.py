import importlib.util
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
SKILL = ROOT / "claude" / "skills" / "limits-window-shift"
sys.path.insert(0, str(SKILL))


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def rate_limit_line(limit_type, resets_at, utilization=0.1, status="allowed", **extra):
    info = {"status": status, "resetsAt": resets_at, "rateLimitType": limit_type, "utilization": utilization}
    info.update(extra)
    return json.dumps({"type": "rate_limit_event", "rate_limit_info": info, "uuid": "u", "session_id": "s"})


def result_line(text="OK", is_error=False, input_tokens=120, output_tokens=2):
    usage = {"input_tokens": input_tokens, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0, "output_tokens": output_tokens}
    return json.dumps({"type": "result", "subtype": "success", "is_error": is_error, "result": text, "usage": usage})


def init_line():
    return json.dumps({"type": "system", "subtype": "init", "model": "claude-haiku", "tools": []})
