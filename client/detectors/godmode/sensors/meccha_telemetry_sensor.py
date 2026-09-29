import json
import os
import re
from collections import deque
from pathlib import Path
from typing import Deque, Optional, Tuple

from core.models import PlayerSnapshot
from sensors.game_state_sensor import GameStateSensor


class MecchaTelemetrySensor(GameStateSensor):
    """
    MECCHA CHAMELEON telemetry JSONL???쎌뼱??
    PlayerSnapshot?쇰줈 蹂?섑븯??Sensor.

    ?곸긽/?ㅽ뿕 ?섍꼍?먯꽌??GodModeHost402.log??
    "blocked server death call" 濡쒓렇???④퍡 愿李고븳??

    ?대떦 濡쒓렇???쒕쾭 ?щ쭩 ?몄텧??GodMode???섑빐
    李⑤떒???쒖젏???섎??섎?濡??ㅼ쓬 Snapshot??
    kill_event=True濡??꾨떖?쒕떎.

    湲곗〈 read_snapshot() / read_snapshot_with_raw()
    ?명꽣?섏씠?ㅻ뒗 洹몃?濡??좎??쒕떎.
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
            configured_path = os.environ.get(
                "GZZ_GODMODE_TELEMETRY_PATH"
            )

            if configured_path:
                self.telemetry_path = Path(
                    configured_path
                )
            else:
                base_dir = (
                    os.environ.get("LOCALAPPDATA")
                    or os.environ.get("TEMP")
                )

                if base_dir:
                    self.telemetry_path = (
                        Path(base_dir)
                        / "MECCHA-GZZ-godmode-telemetry.jsonl"
                    )
                else:
                    self.telemetry_path = (
                        Path.cwd()
                        / "MECCHA-GZZ-godmode-telemetry.jsonl"
                    )
        else:
            self.telemetry_path = Path(
                telemetry_path
            )

        if native_log_path is None:
            configured_native_log = os.environ.get(
                "GZZ_GODMODE_NATIVE_LOG_PATH"
            )

            if configured_native_log:
                self.native_log_path = Path(
                    configured_native_log
                )
            else:
                self.native_log_path = (
                    Path.home()
                    / "Desktop"
                    / "godmode ?? ???"
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

        # ??server-death block??諛쒓껄?덉?留?
        # ?꾩쭅 Snapshot???꾨떖?섏? ?딆? ?곹깭.
        self.native_kill_pending = False

        self.last_native_total: Optional[int] = None

        self.pending_snapshots: Deque[
            Tuple[PlayerSnapshot, str]
        ] = deque()

    def connect(self) -> bool:
        """
        Telemetry JSONL ??? ????.

        ??? ?? ??? ???? ????.
        Native GodMode ?? ?? ???
        ?? Telemetry ?? ??? ???? ???.
        """

        try:
            self.telemetry_path.parent.mkdir(
                parents=True,
                exist_ok=True,
            )

            self.telemetry_path.touch(
                exist_ok=True,
            )

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
        GodMode native 濡쒓렇???곌껐?쒕떎.

        ?뚯씪???꾩쭅 議댁옱?섏? ?딆쑝硫??섏쨷???ㅼ떆 ?쒕룄?쒕떎.
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
        紐⑤뱺 濡쒓렇 ?뚯씪 ?곌껐??醫낅즺?쒕떎.
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
        GodModeHost402.log???덈줈 異붽???以꾩쓣 ?쎈뒗??

        blocked server death call???섎굹 ?댁긽 ?덈줈 諛쒖깮?섎㈃
        ?ㅼ쓬 PlayerSnapshot??kill_event=True瑜??꾨떖?쒕떎.

        ???꾨젅????怨듦꺽?먯꽌 ?щ윭 block 濡쒓렇媛 諛쒖깮?섎뜑?쇰룄
        ?щ윭 ?먯닔濡?以묐났 怨꾩궛?섏? ?딅룄濡?pending bool ?섎굹濡?
        ?⑹퀜???꾨떖?쒕떎.
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
        JSON ?곗씠?곕? PlayerSnapshot?쇰줈 蹂?섑븳??
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
        ??Telemetry? Native GodMode ?대깽?몃? ?쎈뒗??
        """

        if (
            not self.connected
            or self.file is None
        ):
            return

        # Telemetry Snapshot??留뚮뱾湲??꾩뿉
        # native ?щ쭩 李⑤떒 ?대깽?몃????뺤씤.
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

            # GodMode native hook???쒕쾭 ?щ쭩 ?몄텧??留됱븯?ㅻ㈃
            # ?ㅼ쓬 Snapshot??kill_event濡??쒖떆?쒕떎.
            if self.native_kill_pending:
                data["kill_event"] = True

                # Raw export?먯꽌???ㅼ젣 Sensor媛 ?ъ슜??
                # ?대깽?몃? ?뺤씤?????덈룄濡?蹂댁“ ?뺣낫 異붽?.
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
        ?ㅼ쓬 PlayerSnapshot ?섎굹瑜?諛섑솚?쒕떎.
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
        ReplayAnalyzer export??

        諛섑솚:
            (
                PlayerSnapshot,
                ?ㅼ젣 Sensor媛 泥섎━??JSONL 臾몄옄??
            )
        """

        if not self.connected:
            return None

        self._read_new_lines()

        if not self.pending_snapshots:
            return None

        return self.pending_snapshots.popleft()
