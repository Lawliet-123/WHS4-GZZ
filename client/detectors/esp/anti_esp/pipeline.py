"""Sensor → Detector → persistence pipeline for the ESP LocalGuard module."""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Callable, Iterable, Mapping

from .core.events import SensorBatch, TeamDetectionEvent
from .core.interfaces import EvidenceDetector
from .core.session import SessionTelemetryWriter
from .models import EvidenceEvent
from .scoring import SuspicionEngine
from .store import SQLiteEvidenceStore
from .team_format import TeamEventAdapter


TeamEventSink = Callable[[Mapping[str, Any], str], bool]


@dataclass(frozen=True, slots=True)
class PipelineResult:
    raw_event_count: int
    accepted_evidence_count: int
    team_events: tuple[TeamDetectionEvent, ...]


class EspDetectionPipeline:
    """Orchestrate pure detector calls; sensors remain independently testable."""

    def __init__(
        self,
        *,
        detectors: Iterable[EvidenceDetector],
        store: SQLiteEvidenceStore,
        scoring: SuspicionEngine,
        team_adapter: TeamEventAdapter,
        telemetry: SessionTelemetryWriter | None = None,
        team_event_sink: TeamEventSink | None = None,
    ) -> None:
        self.detectors = tuple(detectors)
        if not self.detectors:
            raise ValueError("at least one detector is required")
        self.store = store
        self.scoring = scoring
        self.team_adapter = team_adapter
        self.telemetry = telemetry
        self.team_event_sink = team_event_sink

    def flush_team_outbox(self, session_id: str) -> tuple[TeamDetectionEvent, ...]:
        """Deliver committed team events and acknowledge them in SQLite."""

        if self.telemetry is None:
            return ()

        delivered: list[TeamDetectionEvent] = []
        for outbox_id, payload_json in self.store.pending_team_events(
            session_id=session_id
        ):
            decoded = json.loads(payload_json)
            event = TeamDetectionEvent.from_dict(decoded)
            self.telemetry.append_team_event(
                event,
                idempotency_key=outbox_id,
            )
            if self.team_event_sink is not None and not self.team_event_sink(
                event.to_dict(), outbox_id
            ):
                # Local JSONL is durable, but keep the detector outbox pending
                # so a later poll can retry the shared queue operation.
                continue
            self.store.mark_team_event_delivered(outbox_id)
            delivered.append(event)
        return tuple(delivered)

    def process_batch(self, batch: SensorBatch) -> PipelineResult:
        if not isinstance(batch, SensorBatch):
            raise TypeError("batch must be SensorBatch")
        accepted = 0
        team_events: list[TeamDetectionEvent] = []
        for raw_event in batch.events:
            if self.telemetry is not None:
                self.telemetry.append_raw_event(raw_event)

            detected: list[EvidenceEvent] = []
            for detector in self.detectors:
                accepted_types = getattr(detector, "accepted_event_types", None)
                if accepted_types is not None and raw_event.event_type not in accepted_types:
                    continue
                detected.extend(detector.detect(raw_event))

            staged_team_event: TeamDetectionEvent | None = None

            def build_outbox(
                staged: tuple[EvidenceEvent, ...],
            ) -> tuple[str, str] | None:
                nonlocal staged_team_event
                staged_team_event = self.team_adapter.convert(raw_event, staged)
                if staged_team_event is None or self.telemetry is None:
                    return None
                return staged_team_event.session_id, staged_team_event.to_json()

            accepted_for_event, _outbox_id = self.store.append_many_with_outbox(
                detected, build_outbox
            )
            team_event = staged_team_event

            for evidence in accepted_for_event:
                self.scoring.add_event(evidence)
            accepted += len(accepted_for_event)
            if team_event is not None:
                team_events.append(team_event)
            if self.telemetry is not None:
                for delivered in self.flush_team_outbox(raw_event.session_id):
                    if delivered not in team_events:
                        team_events.append(delivered)

        return PipelineResult(len(batch.events), accepted, tuple(team_events))


__all__ = ["EspDetectionPipeline", "PipelineResult"]
