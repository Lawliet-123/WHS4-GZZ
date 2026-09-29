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

    def skip_existing(self) -> int:
        """지금 파일에 있는 내용은 건너뛰고, 이후에 붙는 줄만 읽게 한다. 건너뛴 바이트 수.

        런처 아래에서 쓴다. 이 파일은 모드가 로드될 때만 비워지므로 이전 게임의
        기록이 남아 있을 수 있다. 처음부터 읽으면 그 기록이 **지금 런처 세션과
        이 PC 의 id 로** 나가고, 재시작할 때마다 같은 결과를 새 event_id 로 또 보낸다.

        첫 줄은 기억해 둔다. 그래야 모드가 다시 로드돼 파일이 비워지면 read_events 의
        '첫 줄이 바뀌면 처음부터' 규칙이 그대로 작동해 새 게임 기록을 놓치지 않는다.
        아직 다 안 쓰인 마지막 줄은 건너뛰지 않는다(쓰이는 중인 지금 기록이다).
        """
        if not self.jsonl_path.exists():
            return 0
        with self.jsonl_path.open("rb") as f:
            first_line = f.readline()
            # 마지막 완성된 줄의 끝을 뒤에서부터 찾는다. 긴 세션 로그를 통째로 읽지 않는다.
            pos = f.seek(0, 2)
            end = 0
            while pos > 0:
                step = min(65536, pos)
                pos -= step
                f.seek(pos)
                nl = f.read(step).rfind(b"\n")
                if nl >= 0:
                    end = pos + nl + 1
                    break
        self._offset = end
        self._first_line = first_line if first_line.endswith(b"\n") else None
        return end

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
                # 과거 Lua 버전은 JSON null 대신 문자열 "nil"을 기록했다.
                # 새 로그는 Lua에서 null로 고쳤지만, 기존 리플레이도 라운드
                # 미확정 상태에서 점수화되지 않도록 여기서 함께 정규화한다.
                round_id = raw.get("round_id")
                if round_id == "nil":
                    round_id = None
                # 함수명에 Local이 붙어도 멀티플레이에서 원격 Pawn이 들어올
                # 가능성을 배제하지 않는다. Lua가 명시적으로 false로 남긴 것은
                # 이 PC의 판정 표본에서 제외한다. 없는 구버전 필드는 유지한다.
                if raw.get("is_local") is False:
                    continue
                if event_type == "shot_attempt":
                    yield ShotEvent(
                        session_id=raw["session_id"],
                        timestamp_ms=raw["timestamp_ms"],
                        attacker_id=raw["attacker_id"],
                        attacker_pos=tuple(raw["attacker_pos"]),
                        aim_trace=self._parse_aim_trace(raw.get("aim_trace", [])),
                        timestamp_source=raw.get("timestamp_source"),
                        source_timestamp_ms=raw.get("timestamp_ms"),
                        round_id=round_id,
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
                    source_timestamp_ms=raw.get("timestamp_ms"),
                    round_id=round_id,
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
