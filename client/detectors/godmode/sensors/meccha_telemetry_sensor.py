import json
import re
from collections import deque
from pathlib import Path
from typing import Deque, Optional, Tuple

from core.models import PlayerSnapshot
from sensors.game_state_sensor import GameStateSensor


class MecchaTelemetrySensor(GameStateSensor):
    """
    MECCHA CHAMELEON telemetry JSONL을 읽어서
    PlayerSnapshot으로 변환하는 Sensor.

    영상/실험 환경에서는 GodModeHost402.log의
    "blocked server death call" 로그도 함께 관찰한다.

    해당 로그는 서버 사망 호출이 GodMode에 의해
    차단된 시점을 의미하므로 다음 Snapshot에
    kill_event=True로 전달한다.

    기존 read_snapshot() / read_snapshot_with_raw()
    인터페이스는 그대로 유지한다.
    """

    BLOCKED_DEATH_PATTERN = re.compile(
        r"blocked server death call;\s*total=(\d+)",
        re.IGNORECASE,
    )

    def __init__(
        self,
        telemetry_path: Optional[str] = None,
        start_at_end: bool = True,
        native_log_path: Optional[str] = None,
    ):
        project_root = Path(__file__).resolve().parents[1]

        if telemetry_path is None:
            self.telemetry_path = (
                project_root
                / "logs"
                / "meccha_telemetry.jsonl"
            )
        else:
            self.telemetry_path = Path(
                telemetry_path
            )

        if native_log_path is None:
            self.native_log_path = (
                Path.home()
                / "Desktop"
                / "godmode 영상 테스트"
                / "native"
                / "GodModeHost402.log"
            )
        else:
            self.native_log_path = Path(
                native_log_path
            )

        self.start_at_end = start_at_end

        self.file = None
        self.connected = False

        self.native_file = None

        # 새 server-death block을 발견했지만
        # 아직 Snapshot에 전달하지 않은 상태.
        self.native_kill_pending = False

        self.last_native_total: Optional[int] = None

        self.pending_snapshots: Deque[
            Tuple[PlayerSnapshot, str]
        ] = deque()

    def connect(self) -> bool:
        """
        Telemetry JSONL 파일에 연결한다.

        Native GodMode 로그 연결 실패는
        기본 Telemetry 연결 실패로 취급하지 않는다.
        """

        if not self.telemetry_path.exists():
            self.connected = False
            return False

        try:
            self.file = self.telemetry_path.open(
                "r",
                encoding="utf-8",
            )

            if self.start_at_end:
                self.file.seek(
                    0,
                    2,
                )

            self.connected = True

        except OSError:
            self.file = None
            self.connected = False
            return False

        self._connect_native_log()

        return True

    def _connect_native_log(self) -> bool:
        """
        GodMode native 로그에 연결한다.

        파일이 아직 존재하지 않으면 나중에 다시 시도한다.
        """

        if self.native_file is not None:
            return True

        if not self.native_log_path.exists():
            return False

        try:
            self.native_file = self.native_log_path.open(
                "r",
                encoding="utf-8",
                errors="replace",
            )

            if self.start_at_end:
                self.native_file.seek(
                    0,
                    2,
                )

            return True

        except OSError:
            self.native_file = None
            return False

    def disconnect(self) -> None:
        """
        모든 로그 파일 연결을 종료한다.
        """

        if self.file is not None:
            self.file.close()

        if self.native_file is not None:
            self.native_file.close()

        self.file = None
        self.native_file = None

        self.connected = False

        self.native_kill_pending = False
        self.last_native_total = None

        self.pending_snapshots.clear()

    def is_connected(self) -> bool:
        return self.connected

    def _read_native_events(self) -> None:
        """
        GodModeHost402.log에 새로 추가된 줄을 읽는다.

        blocked server death call이 하나 이상 새로 발생하면
        다음 PlayerSnapshot에 kill_event=True를 전달한다.

        한 프레임/한 공격에서 여러 block 로그가 발생하더라도
        여러 점수로 중복 계산되지 않도록 pending bool 하나로
        합쳐서 전달한다.
        """

        if self.native_file is None:
            self._connect_native_log()

        if self.native_file is None:
            return

        detected = False

        while True:
            line = self.native_file.readline()

            if not line:
                break

            match = self.BLOCKED_DEATH_PATTERN.search(
                line
            )

            if match is None:
                continue

            try:
                self.last_native_total = int(
                    match.group(1)
                )
            except ValueError:
                self.last_native_total = None

            detected = True

        if detected:
            self.native_kill_pending = True

    def _parse_snapshot(
        self,
        data: dict,
    ) -> Optional[PlayerSnapshot]:
        """
        JSON 데이터를 PlayerSnapshot으로 변환한다.
        """

        try:
            return PlayerSnapshot(
                timestamp=float(
                    data["timestamp"]
                ),

                health=float(
                    data["health"]
                ),

                max_health=float(
                    data["max_health"]
                ),

                dead=bool(
                    data["dead"]
                ),

                invincible=bool(
                    data["invincible"]
                ),

                change_before_health=float(
                    data.get(
                        "change_before_health",
                        0.0,
                    )
                ),

                damage_event=bool(
                    data.get(
                        "damage_event",
                        False,
                    )
                ),

                kill_event=bool(
                    data.get(
                        "kill_event",
                        False,
                    )
                ),

                death_event=bool(
                    data.get(
                        "death_event",
                        False,
                    )
                ),

                heal_event=bool(
                    data.get(
                        "heal_event",
                        False,
                    )
                ),

                respawn_event=bool(
                    data.get(
                        "respawn_event",
                        False,
                    )
                ),
            )

        except (
            KeyError,
            TypeError,
            ValueError,
        ):
            return None

    def _read_new_lines(self) -> None:
        """
        새 Telemetry와 Native GodMode 이벤트를 읽는다.
        """

        if (
            not self.connected
            or self.file is None
        ):
            return

        # Telemetry Snapshot을 만들기 전에
        # native 사망 차단 이벤트부터 확인.
        self._read_native_events()

        while True:
            line = self.file.readline()

            if not line:
                break

            raw_line = line.rstrip(
                "\r\n"
            )

            if not raw_line.strip():
                continue

            try:
                data = json.loads(
                    raw_line
                )

            except json.JSONDecodeError:
                continue

            # GodMode native hook이 서버 사망 호출을 막았다면
            # 다음 Snapshot을 kill_event로 표시한다.
            if self.native_kill_pending:
                data["kill_event"] = True

                # Raw export에서도 실제 Sensor가 사용한
                # 이벤트를 확인할 수 있도록 보조 정보 추가.
                data["native_block_event"] = True

                if self.last_native_total is not None:
                    data["native_block_total"] = (
                        self.last_native_total
                    )

                self.native_kill_pending = False

                raw_line = json.dumps(
                    data,
                    ensure_ascii=False,
                    separators=(",", ":"),
                )

            snapshot = self._parse_snapshot(
                data
            )

            if snapshot is None:
                continue

            self.pending_snapshots.append(
                (
                    snapshot,
                    raw_line,
                )
            )

    def read_snapshot(
        self,
    ) -> Optional[PlayerSnapshot]:
        """
        다음 PlayerSnapshot 하나를 반환한다.
        """

        if not self.connected:
            return None

        self._read_new_lines()

        if not self.pending_snapshots:
            return None

        snapshot, _ = (
            self.pending_snapshots.popleft()
        )

        return snapshot

    def read_snapshot_with_raw(
        self,
    ) -> Optional[
        Tuple[PlayerSnapshot, str]
    ]:
        """
        ReplayAnalyzer export용.

        반환:
            (
                PlayerSnapshot,
                실제 Sensor가 처리한 JSONL 문자열
            )
        """

        if not self.connected:
            return None

        self._read_new_lines()

        if not self.pending_snapshots:
            return None

        return self.pending_snapshots.popleft()