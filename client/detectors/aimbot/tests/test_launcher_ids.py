"""main.to_launcher_ids — 런처 식별자로 바꿔야 중앙 전송 형식을 통과한다.

탐지기 결과의 player_id 는 UE 액터 전체 경로라 shared 형식 검사에서 로컬 거절된다.
런처가 준 session/player id 로 바꾸고 원래 값은 evidence 로 옮기는지 확인한다.
실제 게임 데이터(ReplayAnalyzer/replay-data/aimbot) 34건으로 전후를 비교한다.
"""
import copy
import json
import sys
import unittest
from pathlib import Path

AIMBOT = Path(__file__).resolve().parent.parent
REPO = AIMBOT.parents[2]
sys.path.insert(0, str(AIMBOT))
sys.path.insert(0, str(REPO))

import main as aimbot_main                      # noqa: E402
from shared.errors import SharedError           # noqa: E402
from shared.schema import encode_event          # noqa: E402

REPLAY = REPO / "ReplayAnalyzer" / "replay-data" / "aimbot"


def _replay_events():
    out = []
    for path in sorted(REPLAY.glob("*/events.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                out.append(json.loads(line))
    return out


def _passes(event):
    try:
        encode_event(event)
        return True
    except SharedError:
        return False


SAMPLE = {
    "session_id": "session_20260923_041022",
    "player_id": "BP_Hunter_C /Game/stage_level/cLeon_game.cLeon_game:PersistentLevel.BP_Hunter_C_2147479755",
    "module": "aimbot",
    "timestamp_ms": 1234,
    "evidence": {"shot_attempt_count": 3},
    "reasons": ["Target Lock Maintained Before Confirmed Find"],
    "raw_score": 1,
}


class LauncherIdsTest(unittest.TestCase):
    def test_without_ids_returns_result_unchanged(self):
        self.assertIs(aimbot_main.to_launcher_ids(SAMPLE), SAMPLE)

    def test_moves_ue_values_into_evidence(self):
        out = aimbot_main.to_launcher_ids(SAMPLE, "ac_20260930_010000", "pc_10e46c29")
        self.assertEqual(out["session_id"], "ac_20260930_010000")
        self.assertEqual(out["player_id"], "pc_10e46c29")
        self.assertEqual(out["evidence"]["source_attacker_id"], SAMPLE["player_id"])
        self.assertEqual(out["evidence"]["source_session_id"], SAMPLE["session_id"])

    def test_detection_fields_untouched(self):
        out = aimbot_main.to_launcher_ids(SAMPLE, "ac_x", "pc_x")
        for key in ("module", "timestamp_ms", "reasons", "raw_score"):
            self.assertEqual(out[key], SAMPLE[key])
        self.assertEqual(out["evidence"]["shot_attempt_count"], 3)

    def test_does_not_mutate_input(self):
        before = copy.deepcopy(SAMPLE)
        aimbot_main.to_launcher_ids(SAMPLE, "ac_x", "pc_x")
        self.assertEqual(SAMPLE, before)

    def test_player_only(self):
        out = aimbot_main.to_launcher_ids(SAMPLE, None, "pc_x")
        self.assertEqual(out["session_id"], SAMPLE["session_id"])
        self.assertNotIn("source_session_id", out["evidence"])

    @unittest.skipUnless(REPLAY.is_dir(), "replay-data 없음")
    def test_real_events_rejected_before_and_accepted_after(self):
        events = _replay_events()
        self.assertGreater(len(events), 0)
        self.assertEqual(sum(_passes(e) for e in events), 0,
                         "UE 액터 경로 그대로면 전부 로컬에서 거절돼야 한다(기존 문제 재현)")
        mapped = [aimbot_main.to_launcher_ids(e, "ac_20260930_010000", "pc_10e46c29")
                  for e in events]
        self.assertEqual(sum(_passes(e) for e in mapped), len(events),
                         "런처 식별자로 바꾸면 전부 통과해야 한다")
        self.assertTrue(all(e["evidence"]["source_attacker_id"] for e in mapped))


class _Ev:
    def __init__(self, session_id, timestamp_ms):
        self.session_id = session_id
        self.timestamp_ms = timestamp_ms


class LauncherTimelineTest(unittest.TestCase):
    """UE 시계가 다시 시작되면 런처 시간 기준을 다시 잡는다."""

    def setUp(self):
        self.now = 1000.0
        self._real = aimbot_main.time.time
        aimbot_main.time.time = lambda: self.now
        self.tl = aimbot_main.LauncherTimeline(t0=990.0)       # 세션 시작 10초 뒤부터

    def tearDown(self):
        aimbot_main.time.time = self._real

    def align(self, session, ms):
        e = _Ev(session, ms)
        self.tl.align(e)
        return e

    def test_first_event_anchored_and_intervals_kept(self):
        a = self.align("S1", 5000)
        b = self.align("S1", 6500)
        self.assertEqual((a.timestamp_ms, b.timestamp_ms), (10000, 11500))
        self.assertEqual((a.source_timestamp_ms, b.source_timestamp_ms), (5000, 6500))

    def test_new_ue_session_reanchors(self):
        self.align("S1", 80000)
        self.now = 1030.0                                      # 30초 뒤 모드 재로드
        e = self.align("S2", 500)                              # 새 세션, 시계 처음부터
        self.assertEqual(e.timestamp_ms, 40000)                # 그 순간(t0+40초)에 맞춘다
        self.assertEqual(self.tl.rebased, 1)

    def test_backward_jump_in_same_session_reanchors(self):
        self.align("S1", 80000)
        self.now = 1020.0
        e = self.align("S1", 2000)                             # 같은 세션인데 78초 거꾸로
        self.assertEqual(e.timestamp_ms, 30000)
        self.assertEqual(self.tl.rebased, 1)

    def test_small_backward_jitter_is_not_a_restart(self):
        self.align("S1", 5000)
        e = self.align("S1", 4500)                             # 0.5초 역전 — 같은 순간 기록
        self.assertEqual(self.tl.rebased, 0)
        self.assertEqual(e.timestamp_ms, 9500)

    def test_never_negative(self):
        self.now = 990.0                                       # t0 와 같은 순간에 첫 이벤트
        self.align("S1", 5000)
        e = self.align("S1", 4200)                             # 기준보다 0.8초 이르다
        self.assertGreaterEqual(e.timestamp_ms, 0)

    def test_without_t0_leaves_ue_time(self):
        tl = aimbot_main.LauncherTimeline(t0=None)
        e = _Ev("S1", 5000)
        tl.align(e)
        self.assertEqual(e.timestamp_ms, 5000)


RAW = REPLAY / "aimbot_002" / "raw" / "meccha_aim_telemetry.jsonl"


@unittest.skipUnless(RAW.is_file(), "raw 텔레메트리 없음")
class SkipExistingTest(unittest.TestCase):
    """--from-end: 이전 게임 기록이 지금 세션 이름으로 다시 나가지 않게 한다."""

    def setUp(self):
        import tempfile
        from sensors.meccha_aim_telemetry_sensor import MecchaAimTelemetrySensor
        self.Sensor = MecchaAimTelemetrySensor
        self.lines = [ln for ln in RAW.read_bytes().splitlines(keepends=True) if ln.strip()]
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "meccha_aim_telemetry.jsonl"

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, data, mode="wb"):
        with open(self.path, mode) as f:
            f.write(data)

    def test_old_records_are_not_read(self):
        self.write(b"".join(self.lines))
        s = self.Sensor(self.path)
        self.assertEqual(s.skip_existing(), self.path.stat().st_size)
        self.assertEqual(list(s.read_events()), [])

    def test_lines_added_after_start_are_read(self):
        self.write(b"".join(self.lines[:-2]))
        s = self.Sensor(self.path)
        s.skip_existing()
        self.write(b"".join(self.lines[-2:]), "ab")
        self.assertEqual(len(list(s.read_events())), 2)

    def test_mod_reload_truncation_is_still_detected(self):
        # 이전 게임 기록 위에서 시작했는데, 모드가 다시 로드되며 파일이 비워지고 새로 쓰인다
        self.write(b"".join(self.lines))
        s = self.Sensor(self.path)
        s.skip_existing()
        self.write(b"".join(self.lines[5:8]))            # 첫 줄이 다른 새 파일
        self.assertEqual(len(list(s.read_events())), 3)

    def test_half_written_last_line_is_not_skipped(self):
        last = self.lines[-1]
        self.write(b"".join(self.lines[:-1]) + last[:20])  # 쓰이는 중인 줄
        s = self.Sensor(self.path)
        s.skip_existing()
        self.write(last[20:], "ab")
        self.assertEqual(len(list(s.read_events())), 1)

    def test_missing_file(self):
        s = self.Sensor(self.path)
        self.assertEqual(s.skip_existing(), 0)
        self.write(self.lines[0])
        self.assertEqual(len(list(s.read_events())), 1)


if __name__ == "__main__":
    unittest.main()
