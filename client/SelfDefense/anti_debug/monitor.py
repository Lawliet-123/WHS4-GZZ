"""Observation-only state aggregation. No response/kill/restart policy."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import os

if __package__:
    from .probe import Observation
    from .registry_reader import RegistryError
else:
    from probe import Observation
    from registry_reader import RegistryError


@dataclass
class Scan:
    scope: str
    targets: list[dict] = field(default_factory=list)
    registry_state: str = "NOT_REQUESTED"
    error_code: str | None = None
    registered_targets: int | None = None
    skipped_registered_targets: int = 0

    def summary(self):
        counts = {s: sum(t["state"] == s for t in self.targets)
                  for s in ("CLEAR", "DEBUGGER_PRESENT", "ERROR", "EXITED")}
        failed = self.error_code is not None or counts["ERROR"] > 0
        return {"status": "DETECTED" if counts["DEBUGGER_PRESENT"] else "ERROR" if failed else "NORMAL",
                "scan_complete": not failed and self.registry_state != "STOPPING",
                "scope": self.scope, "registry_state": self.registry_state, "error_code": self.error_code,
                "registered_targets": self.registered_targets,
                "skipped_registered_targets": self.skipped_registered_targets,
                "checked_targets": counts["CLEAR"] + counts["DEBUGGER_PRESENT"],
                "debugger_targets": counts["DEBUGGER_PRESENT"], "error_targets": counts["ERROR"],
                "exited_targets": counts["EXITED"]}

    def reasons(self):
        reasons = {t["error_code"] for t in self.targets if t["error_code"]}
        if any(t["debugger_present"] is True for t in self.targets):
            reasons.add("DEBUGGER_PRESENT")
        if self.error_code:
            reasons.add(self.error_code)
        if self.registry_state == "STOPPING":
            reasons.add("LAUNCHER_STOPPING")
        return sorted(reasons)


class Monitor:
    def __init__(self, probe, registry=None):
        self.probe, self.registry = probe, registry

    def poll(self, stop=None):
        result = Scan("self_only" if self.registry is None else "launcher_registered")

        def inspect(name, pid, created, role):
            try:
                answer = self.probe.inspect(pid, created)
                if (not isinstance(answer, Observation)
                        or answer.state not in {"CLEAR", "DEBUGGER_PRESENT", "EXITED", "ERROR"}
                        or (answer.state == "CLEAR" and answer.debugger_present is not False)
                        or (answer.state == "DEBUGGER_PRESENT" and answer.debugger_present is not True)
                        or (answer.state in {"EXITED", "ERROR"} and answer.debugger_present is not None)):
                    raise ValueError()
            except Exception:
                answer = Observation("ERROR", error_code="PROBE_FAILED")
            item = {"target_module": name, "role": role, "pid": pid,
                    "expected_create_time": None if created is None else str(created), **asdict(answer)}
            # Windows FILETIME exceeds the exact integer range of browser JavaScript.
            if item["observed_create_time"] is not None:
                item["observed_create_time"] = str(item["observed_create_time"])
            result.targets.append(item)
            return answer

        inspect("selfdefense_anti_debug", os.getpid(), None, "self")
        if self.registry is None:
            return result
        try:
            snapshot = self.registry.load()
        except RegistryError as exc:
            result.registry_state, result.error_code = "ERROR", str(exc)
            return result
        except Exception:
            result.registry_state, result.error_code = "ERROR", "REGISTRY_READ_FAILED"
            return result
        result.registered_targets = len(snapshot.targets)
        if snapshot.stopping:
            result.registry_state = "STOPPING"
            return result
        result.registry_state = "VALID"
        owner = inspect(snapshot.owner.name, snapshot.owner.pid, snapshot.owner.created, "launcher")
        if owner.state not in {"CLEAR", "DEBUGGER_PRESENT"}:
            result.registry_state = "OWNER_UNAVAILABLE"
            result.error_code = "LAUNCHER_NOT_RUNNING" if owner.state == "EXITED" else "LAUNCHER_IDENTITY_UNVERIFIED"
            result.skipped_registered_targets = len(snapshot.targets)
            return result
        for i, target in enumerate(snapshot.targets):
            if stop is not None and stop.is_set():
                result.error_code = "SCAN_CANCELLED"
                result.skipped_registered_targets = len(snapshot.targets) - i
                break
            inspect(target.name, target.pid, target.created, "module")
        return result
