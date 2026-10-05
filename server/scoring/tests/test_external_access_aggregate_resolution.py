from __future__ import annotations

import unittest

from server.scoring.aggregate import build_aggregate_evidence
from server.scoring.external_access_summary import ExternalAccessChannelSummary
from server.scoring.risk_input import PlayerRiskInput, RiskSignalInput


def external_signal():
    return RiskSignalInput(
        module="external_access",
        event_id="00000000-0000-0000-0000-000000000001",
        raw_score=0,
        emission="snapshot",
        policy_state="POLICY_NOT_CALIBRATED",
        raw_fraction_pct=None,
        measurement_available=True,
        requires_event_history=False,
        requires_entity_scope=False,
        entity_key=None,
        overlap_tags=(),
        issues=(),
        notes=(),
        calibration_version="replay-v1",
        calibration_mode="pending",
        calibration_threshold=None,
        threshold_met=None,
    )


def player():
    return PlayerRiskInput(
        session_id="s",
        player_id="p",
        signals=(external_signal(),),
        measurement_unavailable_modules=(),
        unresolved_policy_modules=("external_access",),
        event_history_modules=(),
        entity_scoped_modules=(),
        correlation_candidates=(),
    )


def summary(
    submodule,
    *,
    total,
    available,
    positives,
    max_positive,
    latest_raw,
    latest_status,
    latest_available,
):
    return ExternalAccessChannelSummary(
        session_id="s",
        player_id="p",
        submodule=submodule,
        total_events=total,
        available_events=available,
        unavailable_events=total - available,
        positive_events=positives,
        max_positive_raw_score=max_positive,
        has_positive_history=positives > 0,
        latest_event_id="event-1" if total else None,
        latest_raw_score=latest_raw,
        latest_status=latest_status,
        latest_timestamp_ms=1000 if total else None,
        latest_sequence=1 if total else None,
        latest_measurement_available=latest_available,
        latest_is_normal_zero=(
            latest_available is True
            and latest_raw == 0
            and latest_status == "NORMAL"
        ),
    )


class ExternalAccessAggregateResolutionTests(unittest.TestCase):
    def test_external_process_current_positive_is_active(self):
        result = build_aggregate_evidence(
            player(),
            external_access_summaries={
                "external_process": summary(
                    "external_process",
                    total=3,
                    available=3,
                    positives=1,
                    max_positive=3,
                    latest_raw=3,
                    latest_status="SUSPICIOUS",
                    latest_available=True,
                ),
                "module_integrity": summary(
                    "module_integrity",
                    total=3,
                    available=3,
                    positives=0,
                    max_positive=None,
                    latest_raw=0,
                    latest_status="NORMAL",
                    latest_available=True,
                ),
            },
        )

        self.assertEqual(result.active_modules, ("external_access",))
        self.assertEqual(result.unresolved_modules, ())

    def test_external_process_old_positive_does_not_persist(self):
        result = build_aggregate_evidence(
            player(),
            external_access_summaries={
                "external_process": summary(
                    "external_process",
                    total=4,
                    available=4,
                    positives=1,
                    max_positive=3,
                    latest_raw=0,
                    latest_status="NORMAL",
                    latest_available=True,
                ),
                "module_integrity": summary(
                    "module_integrity",
                    total=4,
                    available=4,
                    positives=0,
                    max_positive=None,
                    latest_raw=0,
                    latest_status="NORMAL",
                    latest_available=True,
                ),
            },
        )

        self.assertEqual(result.inactive_modules, ("external_access",))

    def test_module_integrity_positive_history_survives_later_normal(self):
        result = build_aggregate_evidence(
            player(),
            external_access_summaries={
                "external_process": summary(
                    "external_process",
                    total=4,
                    available=4,
                    positives=0,
                    max_positive=None,
                    latest_raw=0,
                    latest_status="NORMAL",
                    latest_available=True,
                ),
                "module_integrity": summary(
                    "module_integrity",
                    total=5,
                    available=5,
                    positives=1,
                    max_positive=2,
                    latest_raw=0,
                    latest_status="NORMAL",
                    latest_available=True,
                ),
            },
        )

        self.assertEqual(result.active_modules, ("external_access",))
        self.assertEqual(result.unresolved_modules, ())

    def test_both_clean_channels_are_inactive(self):
        result = build_aggregate_evidence(
            player(),
            external_access_summaries={
                "external_process": summary(
                    "external_process",
                    total=3,
                    available=3,
                    positives=0,
                    max_positive=None,
                    latest_raw=0,
                    latest_status="NORMAL",
                    latest_available=True,
                ),
                "module_integrity": summary(
                    "module_integrity",
                    total=3,
                    available=3,
                    positives=0,
                    max_positive=None,
                    latest_raw=0,
                    latest_status="NORMAL",
                    latest_available=True,
                ),
            },
        )

        self.assertEqual(result.inactive_modules, ("external_access",))
        self.assertEqual(result.unresolved_modules, ())

    def test_missing_channel_remains_unresolved(self):
        result = build_aggregate_evidence(
            player(),
            external_access_summaries={
                "external_process": summary(
                    "external_process",
                    total=3,
                    available=3,
                    positives=0,
                    max_positive=None,
                    latest_raw=0,
                    latest_status="NORMAL",
                    latest_available=True,
                ),
            },
        )

        self.assertEqual(result.unresolved_modules, ("external_access",))

    def test_failed_latest_measurement_is_unavailable_without_active_history(self):
        result = build_aggregate_evidence(
            player(),
            external_access_summaries={
                "external_process": summary(
                    "external_process",
                    total=3,
                    available=2,
                    positives=0,
                    max_positive=None,
                    latest_raw=0,
                    latest_status="ERROR",
                    latest_available=False,
                ),
                "module_integrity": summary(
                    "module_integrity",
                    total=3,
                    available=3,
                    positives=0,
                    max_positive=None,
                    latest_raw=0,
                    latest_status="NORMAL",
                    latest_available=True,
                ),
            },
        )

        self.assertEqual(result.unavailable_modules, ("external_access",))


if __name__ == "__main__":
    unittest.main()
