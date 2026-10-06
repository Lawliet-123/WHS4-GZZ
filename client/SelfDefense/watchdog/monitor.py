"""Watchdog polling step, independent of files, clocks, shared and process APIs."""
from __future__ import annotations

from dataclasses import dataclass, replace

if __package__:
    from .launcher_adapter import RegistryError, STATUSES
else:
    from launcher_adapter import RegistryError, STATUSES


@dataclass(frozen=True)
class Observation:
    target: str
    status: str
    pid: int | None = None
    error_code: str | None = None
    report: bool = False
    scope: str = "module"
    mode: str | None = None
    restart_allowed: bool = False
    exit_code: int | None = None
    create_time: str | None = None


PROBLEMS = {"gave_up", "orphaned", "error", "exited", "scan_failed", "crashed", "unknown", "unregistered"}


class Watchdog:
    def __init__(self, registry, *, self_name="self_defense", excluded=("autopaint",)):
        self.registry = registry
        self.excluded = {name.casefold() for name in (*excluded, self_name, "selfdefense", "self_defense")}
        self._states = {}
        self._seen = set()

    def _observation(self, target, status, pid=None, error=None, scope="module", *, item=None, allowed=False):
        key = (scope, target)
        old = self._states.get(key)
        current = (status, pid, error, item.create_time if item else None,
                   item.process.exit_code if item else None)
        changed = old != current
        # restarted represents an action, not a persistent health state.
        report = (status == "restarted" or (changed and scope == "module" and status != "skip") or
                  (changed and status in PROBLEMS) or
                  (changed and old is not None and old[0] in PROBLEMS))
        self._states[key] = current
        return Observation(target, status, pid, error, report, scope,
                           item.mode if item else None, allowed,
                           item.process.exit_code if item else None, item.create_time if item else None)

    def poll(self) -> list[Observation]:
        try:
            snapshot = self.registry.inspect()
        except Exception as exc:
            code = str(exc) if isinstance(exc, RegistryError) else "REGISTRY_LIST_FAILED"
            status = "orphaned" if code == "LAUNCHER_ORPHANED" else "error"
            return [self._observation("registry", status, error=code, scope="registry")]
        result = [self._observation("registry", "stopping" if snapshot.stopping else "alive", scope="registry")]
        names = {item.name for item in snapshot.targets}
        if len(self._seen | names) > 4096:
            return [self._observation("registry", "error", error="REGISTRY_NAME_HISTORY_LIMIT", scope="registry")]
        for name in sorted(self._seen - names):
            result.append(self._observation(name, "stopping" if snapshot.stopping else "unregistered",
                                            error=None if snapshot.stopping else "REGISTRY_ENTRY_REMOVED"))
        self._seen |= names
        for item in snapshot.targets:
            name, process = item.name, item.process
            allowed = item.restartable and name.casefold() not in self.excluded
            try:
                status, pid, error = process.status, item.pid, process.error_code
                if snapshot.stopping:
                    status, error = "stopping", None
                elif process.status == "error":
                    pass  # Access denied / uncertain identity must never trigger restart.
                elif process.status == "alive":
                    status = "alive"
                elif allowed:
                    answer = self.registry.restart_if_dead(name, by="watchdog")
                    if not isinstance(answer, (tuple, list)) or len(answer) != 3:
                        raise RegistryError("REGISTRY_INVALID_RESULT")
                    status, pid, _ = answer
                    if (not isinstance(status, str) or status not in STATUSES or
                            (pid is not None and (type(pid) is not int or pid <= 0)) or
                            (status in {"alive", "restarted"} and pid is None)):
                        raise RegistryError("REGISTRY_INVALID_RESULT")
                    error = None
                    if pid != item.pid or status == "restarted":
                        # A restart result identifies a new process. Do not attach the
                        # previous process's creation time / exit code to its PID.
                        item = replace(item, pid=pid, create_time=None,
                                       process=type(process)(status))
                elif item.mode == "oneshot":
                    if process.status != "exited":
                        status, error = "unknown", "ONESHOT_EXIT_UNOBSERVED"
                    elif process.exit_code in (0, 1):
                        status, error = "completed", None  # 1 = findings, NOT process failure.
                    elif process.exit_code == 2:
                        status, error = "scan_failed", "ONESHOT_SCAN_FAILED"
                    else:
                        status, error = "crashed", "ONESHOT_ABNORMAL_EXIT"
                elif item.mode == "continuous":
                    # Code zero is still termination, not proof the service is running.
                    # Game-exit intent is unavailable until Launcher sets stopping.
                    status, error = "exited", "CONTINUOUS_PROCESS_EXITED"
                else:
                    status, error = "unknown", "MODULE_MODE_UNKNOWN"
                result.append(self._observation(name, status, pid, error, item=item, allowed=allowed))
            except Exception as exc:
                code = str(exc) if isinstance(exc, RegistryError) else "REGISTRY_CALL_FAILED"
                result.append(self._observation(name, "error", error=code, item=item, allowed=allowed))
        return result
