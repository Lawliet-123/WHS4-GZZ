"""Normalize factual changes in a game's Toolhelp module snapshots."""

from __future__ import annotations

import time
from typing import Any, Callable, Mapping

from ..core.context import ProcessTarget, SensorContext
from ..core.events import SensorBatch, SensorEvent
from .module_sensor import ModuleChangeDetector, ModuleInfo, ToolhelpModuleSensor


ModuleIdentityProvider = Callable[[str], Mapping[str, Any]]


class LoadedModuleSensor:
    """Emit baseline-safe module presence/change facts for current game PIDs."""

    sensor_id = "loaded_modules"

    def __init__(
        self,
        *,
        snapshot_sensor: ToolhelpModuleSensor | None = None,
        change_tracker: ModuleChangeDetector | None = None,
        identity_provider: ModuleIdentityProvider | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._snapshot_sensor = snapshot_sensor or ToolhelpModuleSensor()
        self._changes = change_tracker or ModuleChangeDetector()
        self._identity_provider = identity_provider
        self._clock = clock
        self._active_subjects: dict[int, str] = {}

    def _identity(self, module: ModuleInfo) -> dict[str, Any]:
        if self._identity_provider is None:
            return {}
        try:
            values = dict(self._identity_provider(module.path))
        except Exception as exc:
            return {"identity_status": "error", "identity_error": str(exc)}
        return values

    def _event(
        self,
        *,
        context: SensorContext,
        target: ProcessTarget,
        event_type: str,
        module: ModuleInfo,
        timestamp: float,
        extra: Mapping[str, Any] | None = None,
    ) -> SensorEvent:
        payload = {
            "target_pid": target.pid,
            "module_name": module.name,
            "module_path": module.path,
            "base_address": module.base_address,
            "base_address_hex": f"0x{module.base_address:X}",
            "image_size": module.image_size,
            **self._identity(module),
            **dict(extra or {}),
        }
        return SensorEvent(
            session_id=context.session_id,
            sensor_id=self.sensor_id,
            event_type=event_type,
            subject_id=target.subject_id,
            timestamp_ms=int(round(timestamp * 1000.0)),
            payload=payload,
        )

    def _observed_module_events(
        self,
        *,
        context: SensorContext,
        target: ProcessTarget,
        event_type: str,
        module: ModuleInfo,
        timestamp: float,
        extra: Mapping[str, Any] | None = None,
    ) -> tuple[SensorEvent, ...]:
        """Build one module fact and, when checked, a separate trust fact."""

        observed = self._event(
            context=context,
            target=target,
            event_type=event_type,
            module=module,
            timestamp=timestamp,
            extra=extra,
        )
        events = [observed]
        if "signature_status" in observed.payload:
            events.append(
                SensorEvent(
                    session_id=context.session_id,
                    sensor_id=self.sensor_id,
                    event_type="module_trust",
                    subject_id=target.subject_id,
                    timestamp_ms=observed.timestamp_ms,
                    payload=dict(observed.payload),
                )
            )
        return tuple(events)

    def poll(self, context: SensorContext) -> SensorBatch:
        if not isinstance(context, SensorContext):
            raise TypeError("context must be SensorContext")
        current_subjects = {target.pid: target.subject_id for target in context.targets}
        for retired_pid in self._active_subjects.keys() - current_subjects.keys():
            self._changes.reset(retired_pid)
        for pid, subject_id in current_subjects.items():
            if (
                pid in self._active_subjects
                and self._active_subjects[pid] != subject_id
            ):
                self._changes.reset(pid)
        self._active_subjects = current_subjects
        if not context.targets:
            return SensorBatch(
                sensor_id=self.sensor_id,
                status="waiting",
                message="waiting for the game process",
            )

        emitted: list[SensorEvent] = []
        errors: list[dict[str, Any]] = []
        baselines = 0
        successful = 0
        for target in context.targets:
            try:
                snapshot = self._snapshot_sensor.capture(target.pid)
                diff = self._changes.observe(snapshot)
                successful += 1
                if diff.baseline_created:
                    baselines += 1
                    for module in snapshot.modules:
                        emitted.extend(
                            self._observed_module_events(
                                context=context,
                                target=target,
                                event_type="module_present",
                                module=module,
                                timestamp=diff.current_captured_at,
                                extra={
                                    "observation_phase": "baseline",
                                    "baseline_created": True,
                                },
                            )
                        )
                    continue
                for module in diff.added:
                    emitted.extend(
                        self._observed_module_events(
                            context=context,
                            target=target,
                            event_type="module_added",
                            module=module,
                            timestamp=diff.current_captured_at,
                        )
                    )
                for module in diff.removed:
                    emitted.append(
                        self._event(
                            context=context,
                            target=target,
                            event_type="module_removed",
                            module=module,
                            timestamp=diff.current_captured_at,
                        )
                    )
                for change in diff.changed:
                    emitted.extend(
                        self._observed_module_events(
                            context=context,
                            target=target,
                            event_type="module_changed",
                            module=change.after,
                            timestamp=diff.current_captured_at,
                            extra={
                                "previous_base_address": change.before.base_address,
                                "previous_image_size": change.before.image_size,
                            },
                        )
                    )
            except Exception as exc:
                errors.append({"pid": target.pid, "error": str(exc)})

        status = "online" if successful else "error"
        message = (
            f"captured {successful} process module snapshot(s)"
            if successful
            else "module snapshots failed"
        )
        return SensorBatch(
            sensor_id=self.sensor_id,
            status=status,
            events=tuple(emitted),
            message=message,
            details={
                "successful_targets": successful,
                "baseline_created": baselines,
                "errors": errors,
            },
            observed_at_ms=int(round(self._clock() * 1000.0)),
        )


__all__ = ["LoadedModuleSensor", "ModuleIdentityProvider"]
