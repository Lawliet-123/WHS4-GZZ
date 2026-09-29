import json
import tempfile
from pathlib import Path

from detector.godmode_detector import GodModeDetector
from sensors.meccha_telemetry_sensor import MecchaTelemetrySensor


def write_jsonl(path: Path, samples):
    """
    테스트용 MECCHA Telemetry JSONL 파일을 만든다.
    """

    with path.open("w", encoding="utf-8") as file:
        for sample in samples:
            file.write(json.dumps(sample))
            file.write("\n")


def run_pipeline_test(title, samples):
    """
    JSONL
        ↓
    MecchaTelemetrySensor
        ↓
    PlayerSnapshot
        ↓
    GodModeDetector

    전체 흐름을 테스트한다.
    """

    print()
    print("=" * 60)
    print(title)
    print("=" * 60)

    temp_file = tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".jsonl",
        delete=False,
        encoding="utf-8"
    )

    temp_path = Path(temp_file.name)
    temp_file.close()

    try:
        write_jsonl(
            temp_path,
            samples
        )

        sensor = MecchaTelemetrySensor(
            telemetry_path=str(temp_path),
            start_at_end=False
        )

        detector = GodModeDetector()

        if not sensor.connect():
            print("[ERROR] Sensor connection failed")
            return

        snapshot_count = 0
        result = None

        while True:
            snapshot = sensor.read_snapshot()

            if snapshot is None:
                break

            snapshot_count += 1

            result = detector.process(
                snapshot
            )

        sensor.disconnect()

        print(f"Snapshots : {snapshot_count}")

        if result is None:
            print("Result    : No data")
            return

        print(f"Status    : {result.status}")
        print(f"Score     : {result.score}")

        print("Reasons   :")

        if not result.reasons:
            print("  - None")
        else:
            for reason in result.reasons:
                print(f"  - {reason}")

    finally:
        if temp_path.exists():
            temp_path.unlink()


def test_normal_player():

    samples = [
        {
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
        },

        {
            "timestamp": 0.1,
            "health": 100,
            "max_health": 100,
            "dead": False,
            "invincible": False,
            "change_before_health": 100,
            "kill_event": True,
            "death_event": False,
            "heal_event": False,
            "respawn_event": False
        },

        {
            "timestamp": 0.3,
            "health": 0,
            "max_health": 100,
            "dead": True,
            "invincible": False,
            "change_before_health": 100,
            "kill_event": False,
            "death_event": True,
            "heal_event": False,
            "respawn_event": False
        }
    ]

    run_pipeline_test(
        "PIPELINE TEST A - Normal Player",
        samples
    )


def test_godmode_player():

    samples = [
        {
            "timestamp": 0.0,
            "health": 100,
            "max_health": 100,
            "dead": False,
            "invincible": True,
            "change_before_health": 100,
            "kill_event": False,
            "death_event": False,
            "heal_event": False,
            "respawn_event": False
        },

        {
            "timestamp": 0.1,
            "health": 100,
            "max_health": 100,
            "dead": False,
            "invincible": True,
            "change_before_health": 100,
            "kill_event": True,
            "death_event": False,
            "heal_event": False,
            "respawn_event": False
        },

        {
            "timestamp": 0.7,
            "health": 100,
            "max_health": 100,
            "dead": False,
            "invincible": True,
            "change_before_health": 100,
            "kill_event": False,
            "death_event": False,
            "heal_event": False,
            "respawn_event": False
        },

        {
            "timestamp": 1.7,
            "health": 100,
            "max_health": 100,
            "dead": False,
            "invincible": True,
            "change_before_health": 100,
            "kill_event": False,
            "death_event": False,
            "heal_event": False,
            "respawn_event": False
        }
    ]

    run_pipeline_test(
        "PIPELINE TEST B - GodMode Player",
        samples
    )


if __name__ == "__main__":
    test_normal_player()
    test_godmode_player()