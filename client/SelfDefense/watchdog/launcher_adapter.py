"""Read all registered processes; delegate only permitted restarts to Launcher.

The real registry is not shipped here. It owns locking, process identity,
CONTINUOUS/ONESHOT selection, stopping, restart budgets and process creation.
"""
from __future__ import annotations

import importlib
import importlib.util
from dataclasses import dataclass
import re
import sys
from pathlib import Path

if __package__:
    from .process_probe import ProcessProbe, ProcessState
else:
    from process_probe import ProcessProbe, ProcessState


STATUSES = frozenset({"alive", "restarted", "backoff", "gave_up", "stopping", "orphaned", "skip"})


class RegistryError(RuntimeError):
    """Public error code only; never log arbitrary registry exception text."""


@dataclass(frozen=True)
class Target:
    name: str
    pid: int | None
    mode: str
    restartable: bool
    process: ProcessState
    create_time: str | None = None


@dataclass(frozen=True)
class Snapshot:
    stopping: bool
    targets: tuple[Target, ...]


class LauncherRegistry:
    def __init__(self, directory: Path, *, expected_session=None, probe=None):
        self.directory = directory.resolve()
        self._module = None
        self.expected_session = expected_session
        self.probe = probe or ProcessProbe()
        self._modes = None

    def _definitions(self):
        if self._modes is not None:
            return self._modes
        source = self.directory / "modules.py"
        if not source.is_file():
            raise RegistryError("MODULE_DEFINITIONS_UNAVAILABLE")
        key = "_gzz_watchdog_launcher_definitions"
        cached = sys.modules.get(key)
        if cached is not None and Path(getattr(cached, "__file__", "")).resolve() != source:
            raise RegistryError("MODULE_DEFINITIONS_CONFLICT")
        try:
            spec = importlib.util.spec_from_file_location(key, source)
            module = importlib.util.module_from_spec(spec)
            sys.modules[key] = module
            spec.loader.exec_module(module)
            values = module.MODULES
            if not isinstance(values, (tuple, list)) or len(values) > 1024:
                raise ValueError
            modes = {}
            for item in values:
                if (not isinstance(item.name, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", item.name)
                        or item.mode not in {"continuous", "oneshot"} or type(item.restart) is not bool
                        or item.name.casefold() in {n.casefold() for n in modes}):
                    raise ValueError
                modes[item.name] = (item.mode, item.restart)
        except Exception:
            sys.modules.pop(key, None)
            raise RegistryError("MODULE_DEFINITIONS_INVALID") from None
        self._modes = modes
        return modes

    def inspect(self):
        module = self._load()
        state = self._check_session(module)
        if state is None:
            raise RegistryError("REGISTRY_SESSION_REQUIRED")
        entries = state["entries"]
        if (len(entries) > 1024 or any(not isinstance(n, str) or
                not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", n) for n in entries) or
                len({n.casefold() for n in entries}) != len(entries)):
            raise RegistryError("REGISTRY_INVALID_NAMES")
        modes = self._definitions()
        targets = []
        for name, entry in entries.items():
            mode, configured_restart = modes.get(name, ("unknown", False))
            if (not isinstance(entry, dict) or type(entry.get("restartable")) is not bool or
                    type(entry.get("pid")) is not int or not 0 < entry["pid"] <= 0xFFFFFFFF or
                    type(entry.get("create_time")) is not int or not 0 < entry["create_time"] <= 0xFFFFFFFFFFFFFFFF):
                targets.append(Target(name, None, mode, False, ProcessState("error", error_code="REGISTRY_INVALID_ENTRY")))
                continue
            # Do not invent restart authority from definitions; both must permit it.
            # Unknown definitions are observed but never restarted.
            restartable = entry["restartable"] and mode == "continuous" and configured_restart
            try:
                process = (ProcessState("stopping") if state["stopping"] else
                           self.probe.read(name, entry["pid"], entry["create_time"]))
            except Exception:
                process = ProcessState("error", error_code="PROCESS_PROBE_FAILED")
            targets.append(Target(name, entry["pid"], mode, restartable, process, str(entry["create_time"])))
        self.probe.retain(set(entries) | {"@launcher"})
        return Snapshot(state["stopping"], tuple(targets))

    def close(self):
        self.probe.close()

    def _load(self):
        if self._module is not None:
            return self._module
        source = self.directory / "registry.py"
        if not source.is_file():
            raise RegistryError("REGISTRY_UNAVAILABLE")
        cached = sys.modules.get("registry")
        if cached is not None and Path(getattr(cached, "__file__", "")).resolve() != source:
            raise RegistryError("REGISTRY_IMPORT_CONFLICT")
        # Load only from the explicitly selected, trusted Launcher directory.
        # Keep it on sys.path: registry may import siblings inside later calls.
        location = str(self.directory)
        if location in sys.path:
            sys.path.remove(location)
        sys.path.insert(0, location)
        importlib.invalidate_caches()
        try:
            if cached is not None:
                module = cached
            else:
                spec = importlib.util.spec_from_file_location("registry", source)
                module = importlib.util.module_from_spec(spec)
                sys.modules["registry"] = module
                try:
                    spec.loader.exec_module(module)
                except BaseException:
                    sys.modules.pop("registry", None)
                    raise
        except Exception:
            raise RegistryError("REGISTRY_IMPORT_FAILED") from None
        if Path(getattr(module, "__file__", "")).resolve() != source:
            raise RegistryError("REGISTRY_IMPORT_CONFLICT")
        if not all(callable(getattr(module, name, None)) for name in ("restartable_names", "restart_if_dead")):
            raise RegistryError("REGISTRY_API_MISMATCH")
        self._module = module
        return module

    def restartable_names(self):
        module = self._load()
        self._check_session(module)
        return module.restartable_names()

    def restart_if_dead(self, name, *, by):
        module = self._load()
        state = self._check_session(module)
        if state is not None:
            if state["stopping"]:
                return "stopping", None, None
            entry = state["entries"].get(name)
            mode, configured_restart = self._definitions().get(name, ("unknown", False))
            if (not isinstance(entry, dict) or entry.get("restartable") is not True
                    or mode != "continuous" or not configured_restart):
                return "skip", None, None
            current = self.probe.read(name, entry.get("pid"), entry.get("create_time"))
            if current.status == "error":
                raise RegistryError(current.error_code)
            if current.status == "alive":
                return "alive", entry["pid"], None
        return module.restart_if_dead(name, by=by)

    def _check_session(self, module):
        """Reject missing/wrong sessions and orphaned owners before requesting any restart.

        This is a preflight, not a substitute for the registry's locked decisions.
        Registry trust/locking and concurrent session replacement remain Launcher concerns.
        """
        if self.expected_session is None:  # Low-level compatibility tests only.
            return
        if not all(callable(getattr(module, name, None)) for name in ("load", "is_alive")):
            raise RegistryError("REGISTRY_API_MISMATCH")
        try:
            state = module.load()
        except Exception:
            raise RegistryError("REGISTRY_READ_FAILED") from None
        if not isinstance(state, dict) or not state:
            raise RegistryError("REGISTRY_SESSION_UNAVAILABLE")
        if state.get("session_id") != self.expected_session:
            raise RegistryError("REGISTRY_SESSION_MISMATCH")
        if type(state.get("stopping")) is not bool or not isinstance(state.get("entries"), dict):
            raise RegistryError("REGISTRY_INVALID_STATE")
        if state["stopping"]:
            return state  # registry itself suppresses restarts; owner may already be cleared.
        pid, created = state.get("launcher_pid"), state.get("launcher_create_time")
        if type(pid) is not int or pid <= 0 or type(created) is not int or created <= 0:
            raise RegistryError("REGISTRY_INVALID_OWNER")
        owner = self.probe.read("@launcher", pid, created)
        if owner.status == "error":
            raise RegistryError("LAUNCHER_" + owner.error_code)
        if owner.status != "alive":
            raise RegistryError("LAUNCHER_ORPHANED")
        return state
