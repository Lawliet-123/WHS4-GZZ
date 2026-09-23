import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.models import AimSample, HitEvent, ShotEvent
from detector.aimbot_detector import AimbotDetector
from sensors.fake_aim_telemetry_sensor import FakeAimTelemetrySensor
from sensors.meccha_aim_telemetry_sensor import MecchaAimTelemetrySensor


def shot(session, timestamp, player="hunter", round_id="round_1", aim_trace=None,
         aimed_candidate_id=None, aimed_candidate_error_deg=None, aimed_candidate_los_clear=None):
    return ShotEvent(
        session, timestamp, player, (0, 0, 0), aim_trace=aim_trace or [], round_id=round_id,
        aimed_candidate_id=aimed_candidate_id,
        aimed_candidate_error_deg=aimed_candidate_error_deg,
        aimed_candidate_los_clear=aimed_candidate_los_clear,
    )


def outcome(session, timestamp, victim, candidates=None, los_clear=None, player="hunter", round_id="round_1"):
    return HitEvent(session, timestamp, player, victim, (0, 0, 0), (100, 0, 0), candidates or {}, los_clear, round_id=round_id)


def run_pipeline(events):
    sensor = FakeAimTelemetrySensor(events)
    detector = AimbotDetector()
    for event in sensor.read_events():
        result = detector.ingest_event(event)
    return result


def uniform_convergence_trace(victim):
    """자체 F8 핵 기본 속도(약 120 deg/s)로 목표까지 붙는 궤적."""
    values = (24.0, 20.0, 16.0, 12.0, 8.0, 4.0, 0.0)
    return [
        AimSample(
            timestamp_ms=index * 33,
            view_pos=(0, 0, 0),
            control_rotation=(0.0, yaw),
            candidates={victim: (100, 0, 0)},
        )
        for index, yaw in enumerate(values)
    ]


class AimbotPipelineTests(unittest.TestCase):
    def test_unsuccessful_shots_do_not_score(self):
        result = run_pipeline([shot("normal", 0), shot("normal", 300), shot("normal", 600)])
        self.assertEqual(result["raw_score"], 0)
        self.assertEqual(result["evidence"]["shot_attempt_count"], 3)
        self.assertEqual(result["evidence"]["confirmed_outcome_count"], 0)

    def test_find_rate_and_streak_are_evidence_not_a_standalone_score(self):
        events = []
        for timestamp, victim in zip((0, 300, 600, 900), ("A", "B", "C", "D")):
            events.extend((shot("perfect", timestamp), outcome("perfect", timestamp + 40, victim)))
        result = run_pipeline(events)
        self.assertEqual(result["raw_score"], 0)
        self.assertEqual(result["evidence"]["success_rate"], 1.0)
        self.assertEqual(result["evidence"]["max_success_streak"], 4)

    def test_unscoped_round_never_scores_rate_or_switch_signals(self):
        events = []
        for timestamp, victim in zip((0, 300, 600, 900), ("A", "B", "C", "D")):
            events.extend((shot("unscoped", timestamp, round_id=None), outcome("unscoped", timestamp + 40, victim, round_id=None)))
        result = run_pipeline(events)
        self.assertEqual(result["raw_score"], 0)

    def test_outcome_without_a_shot_is_not_false_accuracy(self):
        result = run_pipeline([outcome("orphan", 0, "A")])
        self.assertEqual(result["raw_score"], 0)
        self.assertEqual(result["evidence"]["unpaired_outcome_count"], 1)
        self.assertEqual(result["evidence"]["success_rate"], None)

    def test_distance_priority_uses_confirmed_outcomes_only(self):
        events = [
            shot("switch", 0), outcome("switch", 40, "A"),
            shot("switch", 300), outcome("switch", 340, "B", {"A": (200, 0, 0)}),
            shot("switch", 600), outcome("switch", 640, "C", {"B": (200, 0, 0)}),
        ]
        result = run_pipeline(events)
        self.assertEqual(result["raw_score"], 0)
        self.assertEqual(result["evidence"]["target_switches"], 2)
        self.assertEqual(result["evidence"]["distance_evaluable_switches"], 2)
        self.assertEqual(result["evidence"]["distance_priority_switches"], 2)

    def test_consistent_target_convergence_scores_for_one_confirmed_outcome(self):
        result = run_pipeline([
            shot("f8", 0, aim_trace=uniform_convergence_trace("A")),
            outcome("f8", 40, "A"),
        ])
        self.assertEqual(result["raw_score"], 3)
        self.assertEqual(result["evidence"]["target_convergence_outcome_count"], 1)
        self.assertIn("Consistent Target Convergence Before Confirmed Find", result["reasons"])

    def test_convergence_with_terminal_lock_adds_one_point(self):
        # 마지막 0.3초에는 이미 목표 오차가 2도 이하이고, 거의 흔들리지 않는다.
        values = (36.0, 30.0, 24.0, 18.0, 12.0, 6.0, 0.08, 0.06, 0.05, 0.06, 0.05, 0.06, 0.05, 0.06, 0.05, 0.06)
        trace = [
            AimSample(index * 33, (0, 0, 0), (0.0, yaw), {"A": (100, 0, 0)})
            for index, yaw in enumerate(values)
        ]
        result = run_pipeline([shot("f8-lock", 0, aim_trace=trace), outcome("f8-lock", 40, "A")])
        self.assertEqual(result["raw_score"], 4)
        self.assertEqual(result["evidence"]["target_convergence_lock_outcome_count"], 1)
        self.assertEqual(result["evidence"]["mean_terminal_lock_duration_ms"], 297)
        self.assertIn("Target Lock Maintained Before Confirmed Find", result["reasons"])

    def test_terminal_lock_without_convergence_does_not_score(self):
        # 이미 잘 조준한 채 멈춘 정상 플레이는 lock만으로 점수화하지 않는다.
        values = (1.5,) * 12
        trace = [
            AimSample(index * 33, (0, 0, 0), (0.0, yaw), {"A": (100, 0, 0)})
            for index, yaw in enumerate(values)
        ]
        result = run_pipeline([shot("manual-lock", 0, aim_trace=trace), outcome("manual-lock", 40, "A")])
        self.assertEqual(result["raw_score"], 0)
        self.assertEqual(result["evidence"]["target_convergence_lock_outcome_count"], 0)

    def test_target_convergence_allows_brief_early_error_growth(self):
        # 실제 F8 로그처럼 후보 위치/카메라 표본이 갱신되는 초반에는 오차가
        # 잠시 커질 수 있다. 이후 80% 이상 지속 수렴하면 탐지한다.
        values = (30.0, 32.0, 35.0, 35.0, 34.0, 30.0, 25.0, 20.0, 15.0, 10.0, 5.0, 0.0)
        trace = [
            AimSample(index * 33, (0, 0, 0), (0.0, yaw), {"A": (100, 0, 0)})
            for index, yaw in enumerate(values)
        ]
        result = run_pipeline([shot("f8-early-growth", 0, aim_trace=trace), outcome("f8-early-growth", 40, "A")])
        self.assertEqual(result["raw_score"], 3)
        self.assertEqual(result["evidence"]["target_convergence_outcome_count"], 1)

    def test_irregular_manual_aim_does_not_match_target_convergence(self):
        manual_trace = [
            AimSample(index * 33, (0, 0, 0), (0.0, yaw), {"A": (100, 0, 0)})
            for index, yaw in enumerate((24.0, 8.0, 17.0, 5.0, 10.0, 2.0, 0.0))
        ]
        result = run_pipeline([shot("manual", 0, aim_trace=manual_trace), outcome("manual", 40, "A")])
        self.assertEqual(result["raw_score"], 0)
        self.assertEqual(result["evidence"]["target_convergence_outcome_count"], 0)

    def test_empty_candidates_do_not_count_as_distance_priority(self):
        events = [
            shot("empty", 0), outcome("empty", 40, "A"),
            shot("empty", 300), outcome("empty", 340, "B"),
            shot("empty", 600), outcome("empty", 640, "C"),
        ]
        result = run_pipeline(events)
        self.assertEqual(result["evidence"]["target_switches"], 2)
        self.assertEqual(result["evidence"]["distance_evaluable_switches"], 0)
        self.assertEqual(result["raw_score"], 0)

    def test_spectate_pawn_is_not_a_distance_candidate(self):
        spectate = "BP_SpectatePawn_cLeon_C /Game/stage_level/TestSpectatePawn"
        events = [
            shot("spectate", 0), outcome("spectate", 40, "A"),
            shot("spectate", 300), outcome("spectate", 340, "B", {spectate: (95, 0, 0)}),
            shot("spectate", 600), outcome("spectate", 640, "C", {spectate: (85, 0, 0)}),
        ]
        result = run_pipeline(events)
        self.assertEqual(result["evidence"]["distance_evaluable_switches"], 0)
        self.assertNotIn("Distance-Priority Target Switching", result["reasons"])

    def test_outcome_los_is_evidence_not_a_score(self):
        result = run_pipeline([
            shot("los", 0), outcome("los", 40, "A", los_clear=False),
            shot("los", 300), outcome("los", 340, "B", los_clear=False),
        ])
        self.assertEqual(result["raw_score"], 0)
        self.assertEqual(result["evidence"]["outcome_no_los_count"], 2)

    def test_repeated_precise_shots_toward_same_hidden_target_score(self):
        events = [
            shot("blind", 0, aimed_candidate_id="hidden", aimed_candidate_error_deg=1.2, aimed_candidate_los_clear=False),
            shot("blind", 400, aimed_candidate_id="hidden", aimed_candidate_error_deg=2.3, aimed_candidate_los_clear=False),
            shot("blind", 800, aimed_candidate_id="hidden", aimed_candidate_error_deg=0.8, aimed_candidate_los_clear=False),
        ]
        result = run_pipeline(events)
        self.assertEqual(result["raw_score"], 3)
        self.assertEqual(result["evidence"]["max_same_target_blind_miss_count"], 3)
        self.assertIn("Repeated Precise Shots Toward Hidden Target", result["reasons"])

    def test_precise_hidden_tracking_of_moving_target_adds_one_point(self):
        # 같은 숨은 술래가 매 발사 사이 1.5m 이상 이동했는데도, 매번 3도 안쪽으로
        # 겨냥했다. 고정 대상 벽 쏘기와 구분되는 신호 12의 보조 근거다.
        positions = ((0, 0, 0), (220, 0, 0), (460, 0, 0))
        events = []
        for index, position in enumerate(positions):
            trace = [AimSample(index * 400, (0, 0, 0), (0, 0), {"hidden": position})]
            events.append(shot(
                "blind-moving", index * 400, aim_trace=trace,
                aimed_candidate_id="hidden", aimed_candidate_error_deg=1.0,
                aimed_candidate_los_clear=False,
            ))
        result = run_pipeline(events)
        self.assertEqual(result["raw_score"], 4)
        self.assertEqual(result["evidence"]["blind_tracking_moving_target_id"], "hidden")
        self.assertEqual(result["evidence"]["blind_tracking_movement_step_count"], 2)
        self.assertIn("Precise Tracking Maintained While Hidden Target Moved", result["reasons"])

    def test_static_hidden_target_does_not_get_movement_bonus(self):
        trace = [AimSample(0, (0, 0, 0), (0, 0), {"hidden": (100, 0, 0)})]
        events = [
            shot("blind-static", timestamp, aim_trace=trace, aimed_candidate_id="hidden",
                 aimed_candidate_error_deg=1.0, aimed_candidate_los_clear=False)
            for timestamp in (0, 400, 800)
        ]
        result = run_pipeline(events)
        self.assertEqual(result["raw_score"], 3)
        self.assertIsNone(result["evidence"]["blind_tracking_moving_target_id"])

    def test_one_or_imprecise_hidden_shot_does_not_score(self):
        events = [
            shot("blind-normal", 0, aimed_candidate_id="hidden", aimed_candidate_error_deg=1.0, aimed_candidate_los_clear=False),
            shot("blind-normal", 400, aimed_candidate_id="hidden", aimed_candidate_error_deg=5.0, aimed_candidate_los_clear=False),
        ]
        result = run_pipeline(events)
        self.assertEqual(result["raw_score"], 0)
        self.assertEqual(result["evidence"]["max_same_target_blind_miss_count"], 1)

    def test_unknown_los_does_not_score(self):
        result = run_pipeline([shot("unknown", 0), outcome("unknown", 40, "A")])
        self.assertEqual(result["raw_score"], 0)
        self.assertEqual(result["evidence"]["outcome_los_checked_count"], 0)

    def test_result_uses_telemetry_server_schema(self):
        detector = AimbotDetector()
        first = detector.ingest_event(shot("ids", 100, "p"))
        self.assertEqual(
            set(first),
            {"session_id", "player_id", "module", "timestamp_ms", "evidence", "reasons", "raw_score"},
        )
        self.assertNotIn("round_scope_available", first["evidence"])

    def test_new_round_starts_new_window_even_with_same_player_id(self):
        detector = AimbotDetector()
        for timestamp in (10000, 10200, 10400, 10600):
            old = detector.ingest_event(shot("rollback", timestamp))
        fresh = detector.ingest_event(shot("rollback", 0, round_id="round_2"))
        self.assertEqual(old["evidence"]["shot_attempt_count"], 4)
        self.assertEqual(fresh["evidence"]["shot_attempt_count"], 1)

    def test_same_player_id_does_not_mix_sessions(self):
        detector = AimbotDetector()
        detector.ingest_event(shot("old", 0, "p"))
        result = detector.ingest_event(shot("new", 0, "p"))
        self.assertEqual(result["evidence"]["shot_attempt_count"], 1)
        self.assertEqual(result["raw_score"], 0)


class JsonlSensorTests(unittest.TestCase):
    @staticmethod
    def _outcome_dict(session_id="sensor_session"):
        return {"event_type": "confirmed_outcome", "session_id": session_id, "timestamp_ms": 123,
                "attacker_id": "attacker", "victim_id": "victim", "attacker_pos": [0, 0, 0],
                "victim_pos": [100, 0, 0], "other_candidates": {}, "los_clear": True}

    def test_partial_line_is_retried_after_newline_arrives(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "events.jsonl"
            path.write_bytes(json.dumps(self._outcome_dict()).encode("utf-8"))
            sensor = MecchaAimTelemetrySensor(path)
            self.assertEqual(list(sensor.read_events()), [])
            with path.open("ab") as handle:
                handle.write(b"\n")
            events = list(sensor.read_events())
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0].session_id, "sensor_session")

    def test_shot_attempt_is_parsed_separately(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "events.jsonl"
            path.write_text(json.dumps({"event_type": "shot_attempt", "session_id": "s", "timestamp_ms": 1,
                                        "attacker_id": "a", "attacker_pos": [0, 0, 0]}) + "\n", encoding="utf-8")
            events = list(MecchaAimTelemetrySensor(path).read_events())
            self.assertIsInstance(events[0], ShotEvent)

    def test_aim_trace_parser_skips_invalid_samples_and_keeps_valid_one(self):
        parsed = MecchaAimTelemetrySensor._parse_aim_trace([
            {"timestamp_ms": 10, "view_pos": [0, 0, 0], "control_rotation": [0, 5],
             "input_dx": 1.5, "input_dy": -2.0, "candidates": {"target": [100, 0, 0]}},
            {"timestamp_ms": "broken"},
        ])
        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0].control_rotation, (0.0, 5.0))
        self.assertEqual(parsed[0].candidates["target"], (100, 0, 0))

    def test_truncated_log_restarts_from_zero(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "events.jsonl"
            path.write_text(json.dumps(self._outcome_dict("first")) + "\n", encoding="utf-8")
            sensor = MecchaAimTelemetrySensor(path)
            self.assertEqual([event.session_id for event in sensor.read_events()], ["first"])
            path.write_text(json.dumps(self._outcome_dict("second")) + "\n", encoding="utf-8")
            self.assertEqual([event.session_id for event in sensor.read_events()], ["second"])


if __name__ == "__main__":
    unittest.main()
