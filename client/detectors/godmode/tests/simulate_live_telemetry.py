import json
import time
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]

LOG_PATH = (
    PROJECT_ROOT
    / "logs"
    / "meccha_telemetry.jsonl"
)


def send_snapshot(snapshot):
    """
    MECCHA가 실제 Telemetry 데이터를 보내는 것처럼
    JSON 한 줄을 기존 로그 파일 뒤에 추가한다.
    """

    with LOG_PATH.open(
        "a",
        encoding="utf-8"
    ) as file:

        file.write(
            json.dumps(snapshot)
        )

        file.write("\n")
        file.flush()

    print(
        f"[SIMULATOR] sent: {snapshot}"
    )


def main():

    print()
    print("=" * 60)
    print("MECCHA Telemetry Simulator")
    print("=" * 60)
    print()

    # 로그 폴더가 없으면 생성
    LOG_PATH.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    # 파일이 없을 때만 새로 생성한다.
    # 이미 main.py가 읽고 있는 파일은 절대 삭제하지 않는다.
    LOG_PATH.touch(
        exist_ok=True
    )

    print(
        "[SIMULATOR] Telemetry file ready."
    )

    print(
        "[SIMULATOR] Starting live test..."
    )

    time.sleep(1.0)

    # --------------------------------------------------
    # 1. 정상 상태
    # --------------------------------------------------

    send_snapshot({
        "timestamp": 0.0,
        "health": 100,
        "max_health": 100,
        "dead": False,
        "invincible": False,
        "change_before_health": 100,
        "kill_event": False,
        "death_event": False,
        "heal_event": False,
        "respawn_event": False
    })

    time.sleep(0.7)

    # --------------------------------------------------
    # 2. Health 감소
    # --------------------------------------------------

    send_snapshot({
        "timestamp": 1.0,
        "health": 40,
        "max_health": 100,
        "dead": False,
        "invincible": False,
        "change_before_health": 100,
        "kill_event": False,
        "death_event": False,
        "heal_event": False,
        "respawn_event": False
    })

    time.sleep(0.7)

    # --------------------------------------------------
    # 3. GodMode Lua 형태의 강제 Health 복구
    # --------------------------------------------------

    send_snapshot({
        "timestamp": 1.5,
        "health": 100,
        "max_health": 100,
        "dead": False,
        "invincible": True,
        "change_before_health": 100,
        "kill_event": False,
        "death_event": False,
        "heal_event": False,
        "respawn_event": False
    })

    time.sleep(0.7)

    # --------------------------------------------------
    # 4. Kill 조건 발생
    # --------------------------------------------------

    send_snapshot({
        "timestamp": 2.1,
        "health": 100,
        "max_health": 100,
        "dead": False,
        "invincible": True,
        "change_before_health": 100,
        "kill_event": True,
        "death_event": False,
        "heal_event": False,
        "respawn_event": False
    })

    time.sleep(0.7)

    # --------------------------------------------------
    # 5. Kill 이후 Death가 발생하지 않음
    # --------------------------------------------------

    send_snapshot({
        "timestamp": 2.7,
        "health": 100,
        "max_health": 100,
        "dead": False,
        "invincible": True,
        "change_before_health": 100,
        "kill_event": False,
        "death_event": False,
        "heal_event": False,
        "respawn_event": False
    })

    time.sleep(0.7)

    # --------------------------------------------------
    # 6. Invincible 지속
    # --------------------------------------------------

    send_snapshot({
        "timestamp": 3.7,
        "health": 100,
        "max_health": 100,
        "dead": False,
        "invincible": True,
        "change_before_health": 100,
        "kill_event": False,
        "death_event": False,
        "heal_event": False,
        "respawn_event": False
    })

    print()
    print(
        "[SIMULATOR] Test sequence finished."
    )


if __name__ == "__main__":
    main()