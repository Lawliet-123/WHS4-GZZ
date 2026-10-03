"""Platform sensors used by the LocalGuard process.

Sensors acquire facts.  They intentionally do not assign suspicion scores or
decide that an observation proves cheating.
"""

from .module_sensor import (
    ModuleChange,
    ModuleChangeDetector,
    ModuleDiff,
    ModuleEnumerationError,
    ModuleInfo,
    ModuleSnapshot,
    ToolhelpModuleSensor,
    diff_module_snapshots,
    enumerate_process_modules,
)
from .file_identity import FileIdentityEnricher
from .handle_sensor import CurrentProcessHandleSensor
from .identity import PseudonymousIdentity
from .module_events import LoadedModuleSensor
from .process_access import SysmonProcessAccessSensor
from .signature import SignatureVerifier
from .window_overlap import WindowOverlapSensor

__all__ = [
    "FileIdentityEnricher",
    "CurrentProcessHandleSensor",
    "LoadedModuleSensor",
    "ModuleChange",
    "ModuleChangeDetector",
    "ModuleDiff",
    "ModuleEnumerationError",
    "ModuleInfo",
    "ModuleSnapshot",
    "PseudonymousIdentity",
    "SignatureVerifier",
    "SysmonProcessAccessSensor",
    "ToolhelpModuleSensor",
    "WindowOverlapSensor",
    "diff_module_snapshots",
    "enumerate_process_modules",
]
