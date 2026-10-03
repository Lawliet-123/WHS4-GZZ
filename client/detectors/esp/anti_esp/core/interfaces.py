"""Small contracts that keep acquisition separate from interpretation."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..models import EvidenceEvent
from .context import SensorContext
from .events import SensorBatch, SensorEvent


@runtime_checkable
class Sensor(Protocol):
    sensor_id: str

    def poll(self, context: SensorContext) -> SensorBatch:
        """Acquire facts without assigning suspicion or guilt."""


@runtime_checkable
class EvidenceDetector(Protocol):
    detector_id: str
    accepted_event_types: frozenset[str]

    def detect(self, event: SensorEvent) -> tuple[EvidenceEvent, ...]:
        """Interpret normalized facts without performing acquisition I/O."""


__all__ = ["EvidenceDetector", "Sensor"]
