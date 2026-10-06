"""Test-only IPC. A Windows venv Popen PID may identify a redirector, not Python."""
from pathlib import Path
import re


def control_path(root, control_id):
    if not isinstance(control_id, str) or not re.fullmatch(r"[0-9a-f]{32}", control_id):
        raise ValueError("invalid fixture control ID")
    return Path(root) / "artifacts" / "control" / (control_id + ".json")


def matches_process(record, pid, created):
    """Only accept this run's ready worker, matched by PID AND creation time."""
    if not record or record.get("status") != "RUNNING" or not created:
        return False
    if not record.get("create_time") or not record.get("pid"):
        return False
    return ((record["pid"], record["create_time"]) == (pid, created)
            or (record.get("parent_pid"), record.get("parent_create_time")) == (pid, created))
