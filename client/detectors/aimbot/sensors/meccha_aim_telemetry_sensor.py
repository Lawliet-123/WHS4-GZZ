"""
sensors/meccha_aim_telemetry_sensor.py
실제 MECCHA 데이터를 Python으로 가져오는 연결부.

전제: UE4SS(DamageLogger.lua 등)가 명중 이벤트를 JSONL 파일로
한 줄씩 계속 append하고 있다. 이 클래스는 그 파일을 계속 읽어
(마지막으로 읽은 지점부터 이어서) HitEvent로 변환해 내놓는다.

JSONL에는 두 종류의 이벤트가 섞인다.
- shot_attempt: 헌터의 모든 발사 시도 (정확도 계산의 분모)
- confirmed_outcome: 실제 술래 탈락/헌터 전환 결과 (분자·LOS·표적전환 근거)
"""

import json
from pathlib import Path
from typing import Iterator

from core.models import AimSample, HitEvent, ShotEvent, TelemetryEvent
from sensors.aim_telemetry_sensor import AimTelemetrySensor


class MecchaAimTelemetrySensor(AimTelemetrySensor):
    def __init__(self, jsonl_path: Path):
        self.jsonl_path = Path(jsonl_path)
        self._offset = 0
        self._first_line = None

    def read_events(self) -> Iterator[TelemetryEvent]:
        # Lua가 아직 로그를 만들지 않았다면 빈 파일을 대신 만들지 않는다. 경로가
        # 잘못됐는데도 연결된 것처럼 보이는 문제를 피하기 위함이다.
        if not self.jsonl_path.exists():
            return

        # byte offset을 사용하면 UTF-8 다중 바이트 문자와 Windows text cookie에
        # 영향을 받지 않는다. 마지막 줄이 아직 쓰이는 중이면 다음 poll에서 다시 읽는다.
        with self.jsonl_path.open("rb") as f:
            # truncate 후 새 로그가 이전 파일과 크기가 같거나 더 커도 첫 이벤트가
            # 달라지면 새 세션으로 판단할 수 있다.
            first_line = f.readline()
            if self._first_line is not None and first_line != self._first_line:
                self._offset = 0
            if first_line.endswith(b"\n"):
                self._first_line = first_line

            if self.jsonl_path.stat().st_size < self._offset:
                self._offset = 0
            f.seek(self._offset)
            while True:
                line_start = f.tell()
                raw_line = f.readline()
                if not raw_line:
                    break
                if not raw_line.endswith(b"\n"):
                    self._offset = line_start
                    break

                self._offset = f.tell()
                try:
                    line = raw_line.decode("utf-8").strip()
                except UnicodeDecodeError:
                    continue
                if not line:
                    continue
                try:
                    raw = json.loads(line)
                except json.JSONDecodeError:
                    continue
                event_type = raw.get("event_type")
                if event_type == "shot_attempt":
                    yield ShotEvent(
                        session_id=raw["session_id"],
                        timestamp_ms=raw["timestamp_ms"],
                        attacker_id=raw["attacker_id"],
                        attacker_pos=tuple(raw["attacker_pos"]),
                        aim_trace=self._parse_aim_trace(raw.get("aim_trace", [])),
                        timestamp_source=raw.get("timestamp_source"),
                        round_id=raw.get("round_id"),
                        aimed_candidate_id=raw.get("aimed_candidate_id"),
                        aimed_candidate_error_deg=(
                            float(raw["aimed_candidate_error_deg"])
                            if raw.get("aimed_candidate_error_deg") is not None else None
                        ),
                        aimed_candidate_los_clear=raw.get("aimed_candidate_los_clear"),
                    )
                    continue

                # event_type이 없는 과거 JSONL은 읽기 호환성만 유지한다. 새 main.lua는
                # KillPlayer가 확인된 경우에만 confirmed_outcome을 기록한다.
                if event_type not in (None, "confirmed_outcome"):
                    continue
                yield HitEvent(
                    session_id=raw["session_id"],
                    timestamp_ms=raw["timestamp_ms"],
                    attacker_id=raw["attacker_id"],
                    victim_id=raw["victim_id"],
                    attacker_pos=tuple(raw["attacker_pos"]),
                    victim_pos=tuple(raw["victim_pos"]),
                    other_candidates={
                        k: tuple(v) for k, v in raw.get("other_candidates", {}).items()
                    },
                    los_clear=raw.get("los_clear"),
                    timestamp_source=raw.get("timestamp_source"),
                    round_id=raw.get("round_id"),
                )

    @staticmethod
    def _parse_aim_trace(raw_trace: object) -> list[AimSample]:
        """Lua ring buffer JSON을 Detector가 쓰는 조준 표본으로 변환한다.

        잘린 로그·구버전 로그·런타임에서 한 샘플을 못 읽은 경우는 탐지를
        중단시키지 않고 해당 샘플만 건너뛴다.
        """
        if not isinstance(raw_trace, list):
            return []

        samples: list[AimSample] = []
        for raw in raw_trace:
            if not isinstance(raw, dict):
                continue
            view_pos = raw.get("view_pos")
            rotation = raw.get("control_rotation")
            if not (
                isinstance(view_pos, list)
                and len(view_pos) == 3
                and isinstance(rotation, list)
                and len(rotation) == 2
            ):
                continue
            candidates_raw = raw.get("candidates", {})
            candidates = {
                player_id: tuple(position)
                for player_id, position in candidates_raw.items()
                if isinstance(player_id, str)
                and isinstance(position, list)
                and len(position) == 3
            } if isinstance(candidates_raw, dict) else {}
            try:
                samples.append(
                    AimSample(
                        timestamp_ms=int(raw["timestamp_ms"]),
                        view_pos=tuple(float(value) for value in view_pos),
                        control_rotation=tuple(float(value) for value in rotation),
                        candidates=candidates,
                    )
                )
            except (KeyError, TypeError, ValueError):
                continue
        return samples
