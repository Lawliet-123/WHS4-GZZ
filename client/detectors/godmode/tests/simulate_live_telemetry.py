import json
import os
import time
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]

CONFIGURED_PATH = os.environ.get(
    "GZZ_GODMODE_TELEMETRY_PATH"
)

if CONFIGURED_PATH:
    LOG_PATH = Path(
        CONFIGURED_PATH
    )
else:
    BASE_DIR = (
        os.environ.get("LOCALAPPDATA")
        or os.environ.get("TEMP")
    )

    if BASE_DIR:
        LOG_PATH = (
            Path(BASE_DIR)
            / "MECCHA-GZZ-godmode-telemetry.jsonl"
        )
    else:
        LOG_PATH = (
            Path.cwd()
            / "MECCHA-GZZ-godmode-telemetry.jsonl"
        )

def send_snapshot(snapshot):
    """
    MECCHA媛 ?ㅼ젣 Telemetry ?곗씠?곕? 蹂대궡??寃껋쿂??
    JSON ??以꾩쓣 湲곗〈 濡쒓렇 ?뚯씪 ?ㅼ뿉 異붽??쒕떎.
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

    # 濡쒓렇 ?대뜑媛 ?놁쑝硫??앹꽦
    LOG_PATH.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    # ?뚯씪???놁쓣 ?뚮쭔 ?덈줈 ?앹꽦?쒕떎.
    # ?대? main.py媛 ?쎄퀬 ?덈뒗 ?뚯씪? ?덈? ??젣?섏? ?딅뒗??
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
    # 1. ?뺤긽 ?곹깭
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
    # 2. Health 媛먯냼
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
    # 3. GodMode Lua ?뺥깭??媛뺤젣 Health 蹂듦뎄
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
    # 4. Kill 議곌굔 諛쒖깮
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
    # 5. Kill ?댄썑 Death媛 諛쒖깮?섏? ?딆쓬
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
    # 6. Invincible 吏??
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
