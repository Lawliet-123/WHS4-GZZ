"""현재 생산자 → Shared Event → HTTP Receiver → SQLite 회귀 검증.

실게임/외부 클라우드가 아니라 실제 생산 코드와 임시 로그·서버를 사용한다.
"""
from contextlib import contextmanager
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from server.scoring.policies.hide_anywhere import evaluate as hide_policy
from server.scoring.policies.whistle import evaluate as whistle_policy
from server.scoring.policy import inspect_event
from server.scoring.tests import test_receiver_scoring_e2e as fixtures

ROOT = Path(__file__).resolve().parents[3]


def load(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@contextmanager
def rpc_producer():
    # 생산자가 legacy core import와 sys.path 변경을 사용하므로 테스트 밖으로 누출하지 않는다.
    with patch.dict(sys.modules), patch.object(sys, "path", list(sys.path)):
        for name in list(sys.modules):
            if name == "core" or name.startswith("core."):
                del sys.modules[name]
        producer = load("_rpc_a_e2e", "client/detectors/whistle-spoofing/whistle_rpc.py")
        from core.result import to_team_event, to_shared_event
        try:
            yield producer, to_team_event, to_shared_event
        finally:
            producer.end_watch()


class ASenderReceiverE2ETests(unittest.TestCase):
    setUp = fixtures.ReceiverScoringE2ETests.setUp
    tearDown = fixtures.ReceiverScoringE2ETests.tearDown
    post = fixtures.ReceiverScoringE2ETests.post

    def initialise_log(self, producer):
        path = Path(self.tmp.name) / "hook.jsonl"
        state = Path(self.tmp.name) / "rpc.watch.json"
        path.write_text('\n'.join(json.dumps(row) for row in (
            {"event": "start"}, {"event": "exec_hook", "fn": "Provocation"},
            {"event": "stats", "calls": 1},
        )) + '\n', encoding="utf-8")
        producer.begin_watch(str(path), state_file=str(state), t0=100)
        return path, state

    def append_violation(self, path):
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"event": "violation", "codes": ["cooldown_violation"],
                                     "t": 42.5, "fn": "Provocation"}) + '\n')

    def rpc_event(self, producer, to_team, to_shared, window):
        return to_shared(to_team(producer.scan(), "e2e_session", player_id="player_1",
                                 timestamp_ms=window * 1000, window_id=window, sample_id=0))

    def test_final_stale_hook_positive_survives_http_retry_and_semantic_duplicate(self):
        with rpc_producer() as (producer, to_team, to_shared):
            path, _ = self.initialise_log(producer)
            self.append_violation(path)
            with patch.object(producer, "_hook_state", return_value=("stale", "game stopped")):
                payload = self.rpc_event(producer, to_team, to_shared, 10)
            self.assertEqual(payload["raw_score"], 40)
            self.assertIs(payload["evidence"]["meta"]["hook_live"], False)
            self.assertNotEqual(inspect_event(payload).state, "MEASUREMENT_UNAVAILABLE")
            self.assertTrue(any("sample_id=0" in note for note in whistle_policy(payload, inspect_event(payload)).notes))
            eid = fixtures.uid()
            self.assertEqual(self.post(payload, eid).status_code, 200)
            self.assertEqual(self.post(payload, eid).json()["status"], "duplicate")
            duplicate = deepcopy(payload)
            duplicate["timestamp_ms"] += 1000
            self.assertEqual(self.post(duplicate, fixtures.uid()).status_code, 200)
            history = self.store.get_window_history("e2e_session", "player_1")
            self.assertEqual(len(history), 1)
            self.assertEqual((history[0].window_id, history[0].sample_id, history[0].raw_score), (10, 0, 40))
            self.assertIs(history[0].evidence["meta"]["hook_live"], False)

    def test_final_live_normal_then_stale_error_do_not_erase_positive_history(self):
        with rpc_producer() as (producer, to_team, to_shared):
            path, _ = self.initialise_log(producer)
            self.append_violation(path)
            with patch.object(producer, "_hook_state", return_value=(None, None)):
                positive = self.rpc_event(producer, to_team, to_shared, 10)
                normal = self.rpc_event(producer, to_team, to_shared, 11)
            with patch.object(producer, "_hook_state", return_value=("stale", "game stopped")):
                error = self.rpc_event(producer, to_team, to_shared, 12)
            self.assertEqual(normal["evidence"]["status"], "NORMAL")
            self.assertEqual(error["evidence"]["status"], "ERROR")
            self.assertEqual(inspect_event(error).state, "MEASUREMENT_UNAVAILABLE")
            for payload in (positive, normal, error):
                self.assertEqual(self.post(payload, fixtures.uid()).status_code, 200)
            history = self.store.get_window_history("e2e_session", "player_1")
            self.assertEqual([row.raw_score for row in history], [40, 0, 0])
            self.assertEqual([row.sample_id for row in history], [0, 0, 0])

    def test_previous_game_violation_cannot_be_salvaged_as_final_window(self):
        with rpc_producer() as (producer, to_team, to_shared):
            path, _ = self.initialise_log(producer)
            self.append_violation(path)
            with patch.object(producer, "_hook_state", return_value=("before_game", "previous game")):
                payload = self.rpc_event(producer, to_team, to_shared, 10)
            self.assertEqual(payload["raw_score"], 0)
            self.assertEqual(inspect_event(payload).state, "MEASUREMENT_UNAVAILABLE")
            self.assertEqual(self.post(payload, fixtures.uid()).status_code, 200)

    def test_cursor_resume_reads_once_but_overwrite_starts_at_new_end(self):
        with rpc_producer() as (producer, to_team, to_shared):
            path, state = self.initialise_log(producer)
            self.append_violation(path)
            producer.end_watch()
            producer.begin_watch(str(path), state_file=str(state), t0=100)
            with patch.object(producer, "_hook_state", return_value=(None, None)):
                payload = self.rpc_event(producer, to_team, to_shared, 10)
            self.assertEqual(payload["raw_score"], 40)
            producer.end_watch()
            self.append_violation(path)
            producer.begin_watch(str(path), state_file=str(state), t0=200)
            with patch.object(producer, "_hook_state", return_value=(None, None)):
                fresh = self.rpc_event(producer, to_team, to_shared, 11)
            self.assertEqual(fresh["raw_score"], 0)
            self.assertEqual(fresh["evidence"]["status"], "NORMAL")

    def test_current_hide_confirmation_and_failed_read_reach_receiver_unchanged(self):
        producer = load("_hide_a_e2e", "client/detectors/mecha_detector_shared/mecha_detector_v9.py")
        rule = producer.Rule(required=3)
        events = [producer.make_common_event("e2e_session", "player_1", "hide_anywhere", idx * 1000,
                  values, injected_module=False, viewport_hook=False, rule=rule, identity="pawn")
                  for idx, values in enumerate((producer.EXPECTED,) * 3 + ({},), 1)]
        self.assertEqual([row["raw_score"] for row in events], [0, 0, 3, 0])
        for payload in events:
            self.assertEqual(self.post(payload, fixtures.uid()).status_code, 200)
        self.assertEqual([row.result for row in self.writer.iter_stored()], events)
        self.assertEqual(hide_policy(events[2], inspect_event(events[2])).overlap_tags, ("hide_anywhere_value_tamper",))
        self.assertEqual(inspect_event(events[3]).state, "MEASUREMENT_UNAVAILABLE")
        self.assertEqual(self.store.get_module_state("e2e_session", "player_1", "hide_anywhere").evidence["status"], "ERROR")

    def test_current_hide_and_captured_localguard_evidence_make_only_matching_candidates(self):
        producer = load("_hide_overlap_e2e", "client/detectors/mecha_detector_shared/mecha_detector_v9.py")
        rule = producer.Rule(required=3)
        for _ in range(3):
            hide = producer.make_common_event("e2e_session", "player_1", "hide_anywhere", 4000,
                producer.EXPECTED, injected_module=True, viewport_hook=False, rule=rule, identity="pawn")
        self.assertEqual(self.post(hide, fixtures.uid()).status_code, 200)
        # 원본 파일은 수정하지 않는다. 캡처를 같은 시험 세션으로 재생한 합성 E2E이다.
        source = ROOT / "ReplayAnalyzer/replay-data/hide-anywhere/hide_hack_001/events.jsonl"
        with rpc_producer() as (_, _, to_shared):
            for line in source.read_text(encoding="utf-8-sig").splitlines()[:2]:
                payload = to_shared(json.loads(line))
                payload.update(session_id="e2e_session", player_id="player_1")
                self.assertEqual(self.post(payload, fixtures.uid()).status_code, 200)
        snapshot = fixtures.scoring_main.get_player_policy_snapshot("e2e_session", "player_1", max_time_distance_ms=5000)
        candidates = {frozenset(row.modules): row.overlap_tags for row in snapshot.correlation_candidates}
        self.assertEqual(candidates, {
            frozenset(("hide_anywhere", "injection")): ("hide_anywhere_injection",),
            frozenset(("hide_anywhere", "value_tamper")): ("hide_anywhere_value_tamper",),
        })
        self.assertEqual({row.state.module: row.state.raw_score for row in snapshot.modules},
                         {"hide_anywhere": 3, "injection": 40, "value_tamper": 100})


if __name__ == "__main__":
    unittest.main()
