import json
import sys
import time
from pathlib import Path

# ---------------------------------------------------------
# Repository root / shared package
# ---------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[3]

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from shared.config import ClientConfig
from shared.errors import SharedError
from shared.logger import (
    configure_client,
    flush_client,
    send_detection,
    shutdown_client,
)

from detector.godmode_detector import GodModeDetector
from sensors.meccha_telemetry_sensor import MecchaTelemetrySensor


POLL_INTERVAL = 0.10
RECONNECT_INTERVAL = 1.0

DEFAULT_PLAYER_ID = "player_001"

PROJECT_DIR = Path(__file__).resolve().parent
EXPORT_ROOT = PROJECT_DIR / "replay_exports"


def print_banner():
    print()
    print("=" * 60)
    print("MECCHA CHAMELEON - GodMode Anti-Cheat")
    print("=" * 60)
    print()


def print_result(result):
    print()
    print("-" * 60)

    print(
        json.dumps(
            result.to_dict(),
            ensure_ascii=False,
            indent=4,
        )
    )

    print("-" * 60)


def configure_telemetry():
    """
    shared client를 프로세스 시작 시 한 번 설정한다.

    Shared 설정이 없거나 초기화에 실패하더라도
    기존 GodMode 로컬 탐지는 계속 동작한다.
    """

    try:
        configure_client(
            ClientConfig.from_env()
        )

        print(
            "[Telemetry] shared client configured"
        )

        return True

    except SharedError as exc:
        print(
            "[Telemetry] shared client unavailable; "
            "local detection continues "
            f"({type(exc).__name__}: {exc})"
        )

        return False


def get_session_info(session_id):
    """
    session_id 이름으로 테스트 종류를 구분한다.

    normal_001
        -> NORMAL

    godmode_001
        -> CHEAT / GODMODE
    """

    lowered = session_id.lower()

    if lowered.startswith("normal"):
        return {
            "label": "NORMAL",
            "cheat_type": None,
        }

    if lowered.startswith("godmode"):
        return {
            "label": "CHEAT",
            "cheat_type": "GODMODE",
        }

    return {
        "label": "UNKNOWN",
        "cheat_type": None,
    }


def get_timestamp_ms(
    snapshot,
    session_start_timestamp,
):
    return int(
        round(
            (
                snapshot.timestamp
                - session_start_timestamp
            )
            * 1000
        )
    )


def build_common_event(
    session_id,
    player_id,
    snapshot,
    result,
    session_start_timestamp,
):
    """
    ReplayAnalyzer / Dashboard / Shared 공통 Event.

    기존 팀 공통 7필드 형식을 유지한다.

    중요:
    누적 reasons / score가 아니라
    이번 Snapshot에서 새로 발생한 탐지만 저장한다.

    Shared 전송에서도 이 값을 수정하지 않고
    그대로 send_detection()에 전달한다.
    """

    timestamp_ms = get_timestamp_ms(
        snapshot,
        session_start_timestamp,
    )

    return {
        "session_id": session_id,
        "player_id": player_id,
        "module": "godmode",
        "timestamp_ms": timestamp_ms,

        "evidence": {
            "health": snapshot.health,
            "max_health": snapshot.max_health,
            "dead": snapshot.dead,
            "invincible": snapshot.invincible,
            "change_before_health": (
                snapshot.change_before_health
            ),
            "kill_event": snapshot.kill_event,
            "death_event": snapshot.death_event,
            "heal_event": snapshot.heal_event,
            "respawn_event": snapshot.respawn_event,
        },

        "reasons": result.new_reasons.copy(),

        "raw_score": result.new_score,
    }


def write_json(
    path,
    data,
):
    with path.open(
        "w",
        encoding="utf-8",
    ) as file:

        json.dump(
            data,
            file,
            ensure_ascii=False,
            indent=2,
        )


def prepare_export(
    session_id,
    player_id,
):
    """
    ReplayAnalyzer 전달용 세션 폴더를 만든다.

    구조:

    replay_exports/
        normal_001/
            manifest.json
            events.jsonl
            raw/
                meccha_telemetry.jsonl
    """

    session_dir = (
        EXPORT_ROOT
        / session_id
    )

    raw_dir = (
        session_dir
        / "raw"
    )

    raw_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    raw_path = (
        raw_dir
        / "meccha_telemetry.jsonl"
    )

    events_path = (
        session_dir
        / "events.jsonl"
    )

    manifest_path = (
        session_dir
        / "manifest.json"
    )

    # 같은 session_id로 다시 실행할 경우
    # 이전 결과가 섞이지 않도록 초기화한다.
    raw_path.write_text(
        "",
        encoding="utf-8",
    )

    events_path.write_text(
        "",
        encoding="utf-8",
    )

    session_info = get_session_info(
        session_id
    )

    manifest = {
        "session_id": session_id,
        "player_id": player_id,

        "label": (
            session_info["label"]
        ),

        "cheat_type": (
            session_info["cheat_type"]
        ),

        # 테스트 종료 후 raw 로그의
        # invincible False -> True / True -> False
        # 전환을 확인해서 채울 예정
        "cheat_start_ms": None,
        "cheat_end_ms": None,
    }

    write_json(
        manifest_path,
        manifest,
    )

    return {
        "session_dir": session_dir,
        "raw_path": raw_path,
        "events_path": events_path,
        "manifest_path": manifest_path,
    }


def append_raw(
    raw_path,
    raw_line,
):
    """
    Sensor가 읽은 원본 JSONL 한 줄을 그대로 저장한다.

    pawn_address 등 PlayerSnapshot에 없는 필드도
    원본 그대로 유지된다.
    """

    with raw_path.open(
        "a",
        encoding="utf-8",
    ) as file:

        file.write(
            raw_line
        )

        file.write("\n")


def append_event(
    events_path,
    event,
):
    """
    기존 공통 7필드 Event를 로컬 JSONL에 저장한다.

    Shared 연동 이후에도 기존 로컬 기록은
    그대로 유지한다.
    """

    with events_path.open(
        "a",
        encoding="utf-8",
    ) as file:

        file.write(
            json.dumps(
                event,
                ensure_ascii=False,
            )
        )

        file.write("\n")


def send_shared_event(
    event,
    telemetry_enabled,
):
    """
    기존 공통 Event를 Shared 전송 큐에 등록한다.

    send_detection() 성공은 서버 저장 완료가 아니라
    로컬 outbox에 정상 등록되었다는 의미다.

    Shared 전송 실패가 GodMode detector 자체를
    종료시키지 않도록 예외를 처리한다.
    """

    if not telemetry_enabled:
        return

    try:
        receipt = send_detection(
            event
        )

        print(
            "[Telemetry] queued "
            f"event_id={receipt.event_id} "
            f"status={receipt.status}"
        )

    except SharedError as exc:
        print(
            "[Telemetry] send failed: "
            f"{type(exc).__name__}: {exc}"
        )


def main():
    print_banner()

    if len(sys.argv) < 2:

        print(
            "[ERROR] session_id가 필요합니다."
        )

        print()
        print("예:")

        print(
            "python main.py normal_001"
        )

        print(
            "python main.py godmode_001"
        )

        return

    session_id = sys.argv[1]

    if len(sys.argv) >= 3:
        player_id = sys.argv[2]

    else:
        player_id = DEFAULT_PLAYER_ID

    export = prepare_export(
        session_id=session_id,
        player_id=player_id,
    )

    raw_path = export[
        "raw_path"
    ]

    events_path = export[
        "events_path"
    ]

    manifest_path = export[
        "manifest_path"
    ]

    print(
        f"[SESSION] {session_id}"
    )

    print(
        f"[PLAYER] {player_id}"
    )

    print()
    print(
        "[RAW]"
    )

    print(
        raw_path
    )

    print()
    print(
        "[EVENTS]"
    )

    print(
        events_path
    )

    print()
    print(
        "[MANIFEST]"
    )

    print(
        manifest_path
    )

    # -----------------------------------------------------
    # Shared client
    # -----------------------------------------------------

    telemetry_enabled = (
        configure_telemetry()
    )

    # -----------------------------------------------------
    # GodMode Sensor / Detector
    # -----------------------------------------------------

    sensor = MecchaTelemetrySensor(
        start_at_end=True
    )

    detector = GodModeDetector()

    print()
    print(
        "[INFO] Waiting for MECCHA telemetry..."
    )

    while not sensor.connect():

        time.sleep(
            RECONNECT_INTERVAL
        )

    print(
        "[INFO] Telemetry connected."
    )

    print(
        "[INFO] GodMode detection started."
    )

    print(
        "[INFO] Press Ctrl+C to stop."
    )

    last_result_signature = None

    session_start_timestamp = None

    try:

        while True:

            item = (
                sensor.read_snapshot_with_raw()
            )

            if item is None:

                time.sleep(
                    POLL_INTERVAL
                )

                continue

            snapshot, raw_line = item

            # -----------------------------
            # 세션 시작 기준시각
            # -----------------------------

            if session_start_timestamp is None:

                session_start_timestamp = (
                    snapshot.timestamp
                )

                print()
                print(
                    "[INFO] Session timer started at 0 ms."
                )

            # -----------------------------
            # 원본 Raw telemetry 저장
            # -----------------------------

            append_raw(
                raw_path,
                raw_line,
            )

            # -----------------------------
            # Detector
            # -----------------------------

            result = detector.process(
                snapshot
            )

            # -----------------------------
            # 공통 7필드 Event
            # -----------------------------

            if result.new_reasons:

                event = build_common_event(
                    session_id=session_id,
                    player_id=player_id,
                    snapshot=snapshot,
                    result=result,
                    session_start_timestamp=(
                        session_start_timestamp
                    ),
                )

                # 기존 로컬 JSONL 기록 유지
                append_event(
                    events_path,
                    event,
                )

                print()
                print(
                    "[COMMON EVENT]"
                )

                print(
                    json.dumps(
                        event,
                        ensure_ascii=False,
                        indent=4,
                    )
                )

                # 같은 Event를 수정 없이
                # Shared 중앙 전송 큐에 등록
                send_shared_event(
                    event,
                    telemetry_enabled,
                )

            # -----------------------------
            # 누적 Detector 화면 출력
            # -----------------------------

            current_signature = (
                result.status,
                result.score,
                tuple(result.reasons),
            )

            if (
                current_signature
                != last_result_signature
            ):

                print_result(
                    result
                )

                last_result_signature = (
                    current_signature
                )

    except KeyboardInterrupt:

        print()
        print(
            "[INFO] Anti-Cheat stopped."
        )

        print()
        print(
            "[INFO] Replay export saved:"
        )

        print(
            export["session_dir"]
        )

    finally:

        # GodMode telemetry 파일 연결 종료
        sensor.disconnect()

        # -------------------------------------------------
        # Shared 전송 큐 정리
        # -------------------------------------------------

        if telemetry_enabled:

            try:
                delivered = flush_client(
                    timeout=3
                )

                print(
                    "[Telemetry] flush "
                    f"delivered={delivered}"
                )

            except SharedError as exc:
                print(
                    "[Telemetry] flush failed: "
                    f"{type(exc).__name__}: {exc}"
                )

            try:
                stopped = shutdown_client(
                    timeout=5
                )

                print(
                    "[Telemetry] shutdown "
                    f"stopped={stopped}"
                )

            except SharedError as exc:
                print(
                    "[Telemetry] shutdown failed: "
                    f"{type(exc).__name__}: {exc}"
                )


if __name__ == "__main__":
    main()