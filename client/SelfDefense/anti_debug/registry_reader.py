"""Read the Launcher PID snapshot, never import registry or request restarts."""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
import time

if __package__:
    from .probe import valid_identity
else:
    from probe import valid_identity

MAX_REGISTRY_BYTES, MAX_ENTRIES = 1024 * 1024, 128


class RegistryError(ValueError):
    """Public code only; never serialize argv, credentials or arbitrary OS messages."""


@dataclass(frozen=True)
class Target:
    name: str
    pid: int
    created: int


@dataclass(frozen=True)
class Snapshot:
    stopping: bool
    owner: Target | None
    targets: tuple[Target, ...]


def unique(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise RegistryError("REGISTRY_DUPLICATE_KEY")
        value[key] = item
    return value


def parse(data, expected_session):
    if len(data) > MAX_REGISTRY_BYTES:
        raise RegistryError("REGISTRY_TOO_LARGE")
    try:
        state = json.loads(data.decode("utf-8"), object_pairs_hook=unique)
    except RegistryError:
        raise
    except (ValueError, UnicodeError, RecursionError):
        raise RegistryError("REGISTRY_INVALID_JSON") from None
    if type(state) is not dict or type(state.get("stopping")) is not bool or type(state.get("entries")) is not dict:
        raise RegistryError("REGISTRY_INVALID_STATE")
    if state.get("session_id") != expected_session:
        raise RegistryError("REGISTRY_SESSION_MISMATCH")
    if len(state["entries"]) > MAX_ENTRIES:
        raise RegistryError("REGISTRY_TOO_MANY_TARGETS")
    if state["stopping"]:
        # A normal Launcher shutdown may have cleared its PID and child entries.
        return Snapshot(True, None, ())
    if not valid_identity(state.get("launcher_pid"), state.get("launcher_create_time")):
        raise RegistryError("REGISTRY_INVALID_OWNER")
    targets = []
    for name, entry in sorted(state["entries"].items()):
        if (not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", name) or name in {".", ".."}
                or type(entry) is not dict or not valid_identity(entry.get("pid"), entry.get("create_time"))):
            raise RegistryError("REGISTRY_INVALID_TARGET")
        targets.append(Target(name, entry["pid"], entry["create_time"]))
    # restartable=False/ONESHOT are still observable while they are running.
    return Snapshot(False, Target("launcher", state["launcher_pid"], state["launcher_create_time"]), tuple(targets))


class RegistryReader:
    def __init__(self, path: Path, expected_session: str):
        self.path, self.expected_session = path, expected_session

    def load(self):
        for attempt in range(3):
            try:
                with self.path.open("rb") as stream:
                    data = stream.read(MAX_REGISTRY_BYTES + 1)
                return parse(data, self.expected_session)
            except FileNotFoundError:
                raise RegistryError("REGISTRY_UNAVAILABLE") from None
            except PermissionError:
                if attempt == 2:
                    raise RegistryError("REGISTRY_ACCESS_DENIED") from None
                time.sleep(0.02)  # Launcher publishes snapshots with os.replace.
            except OSError:
                raise RegistryError("REGISTRY_READ_FAILED") from None
