"""Watchdog polling step, independent of files, clocks, shared and process APIs."""
from __future__ import annotations

from dataclasses import dataclass
from itertools import islice

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


class Watchdog:
    def __init__(self, registry, *, self_name="self_defense", excluded=("autopaint",)):
        self.registry = registry
        self.excluded = {name.casefold() for name in (*excluded, self_name, "selfdefense", "self_defense")}
        self._states = {}

    def _observation(self, target, status, pid=None, error=None, scope="module"):
        key = (scope, target)
        old = self._states.get(key)
        current = (status, pid, error)
        changed = old != current
        # restarted represents an action, not a persistent health state.
        report = (status == "restarted" or
                  (changed and status in {"gave_up", "orphaned", "error"}) or
                  (changed and old is not None and old[0] in {"gave_up", "orphaned", "error"}))
        self._states[key] = current
        return Observation(target, status, pid, error, report, scope)

    def poll(self) -> list[Observation]:
        try:
            supplied = self.registry.restartable_names()
            if isinstance(supplied, (str, bytes, dict)):
                raise RegistryError("REGISTRY_INVALID_NAMES")
            names = list(islice(iter(supplied), 1025))
            if (len(names) > 1024 or any(not isinstance(n, str) or not n or len(n) > 128 for n in names)
                    or len({n.casefold() for n in names}) != len(names)):
                raise RegistryError("REGISTRY_INVALID_NAMES")
        except Exception as exc:
            code = str(exc) if isinstance(exc, RegistryError) else "REGISTRY_LIST_FAILED"
            status = "orphaned" if code == "LAUNCHER_ORPHANED" else "error"
            return [self._observation("registry", status, error=code, scope="registry")]
        result = [self._observation("registry", "alive", scope="registry")]
        active = {("registry", "registry")}
        for name in names:
            active.add(("module", name))
            if name.casefold() in self.excluded:
                result.append(self._observation(name, "skip"))
                continue
            try:
                answer = self.registry.restart_if_dead(name, by="watchdog")
                if not isinstance(answer, (tuple, list)) or len(answer) != 3:
                    raise RegistryError("REGISTRY_INVALID_RESULT")
                status, pid, _ = answer  # Actual registry returns Popen or None; never serialize it.
                if (not isinstance(status, str) or status not in STATUSES or
                        (pid is not None and (type(pid) is not int or pid <= 0)) or
                        (status in {"alive", "restarted"} and pid is None)):
                    raise RegistryError("REGISTRY_INVALID_RESULT")
                result.append(self._observation(name, status, pid))
            except Exception as exc:
                code = str(exc) if isinstance(exc, RegistryError) else "REGISTRY_CALL_FAILED"
                result.append(self._observation(name, "error", error=code))
        self._states = {key: value for key, value in self._states.items() if key in active}
        return result
