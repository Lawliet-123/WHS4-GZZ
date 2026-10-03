"""Watchdog adapter for Launcher acc9d2a; downloaded registry is never modified.

The real registry is not shipped here. It owns locking, process identity,
CONTINUOUS/ONESHOT selection, stopping, restart budgets and process creation.
"""
from __future__ import annotations

import importlib
import importlib.util
import sys
from pathlib import Path


STATUSES = frozenset({"alive", "restarted", "backoff", "gave_up", "stopping", "orphaned", "skip"})


class RegistryError(RuntimeError):
    """Public error code only; never log arbitrary registry exception text."""


class LauncherRegistry:
    def __init__(self, directory: Path, *, expected_session=None):
        self.directory = directory.resolve()
        self._module = None
        self.expected_session = expected_session

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
        self._check_session(module)
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
            return  # registry itself suppresses restarts; owner may already be cleared.
        pid, created = state.get("launcher_pid"), state.get("launcher_create_time")
        if type(pid) is not int or pid <= 0 or type(created) is not int or created <= 0:
            raise RegistryError("REGISTRY_INVALID_OWNER")
        if not module.is_alive(pid, created):
            raise RegistryError("LAUNCHER_ORPHANED")
