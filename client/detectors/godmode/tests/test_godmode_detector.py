from core.models import PlayerSnapshot
from detector.godmode_detector import GodModeDetector


def print_result(title, result):
    print()
    print("=" * 60)
    print(title)
    print("=" * 60)

    print(f"Status : {result.status}")
    print(f"Score  : {result.score}")
    print("Reasons:")

    if not result.reasons:
        print("  - None")
    else:
        for reason in result.reasons:
            print(f"  - {reason}")


def test_normal_death():
    detector = GodModeDetector()

    samples = [
        PlayerSnapshot(
            timestamp=0.0,
            health=100,
            max_health=100,
            dead=False,
            invincible=False,
            change_before_health=100
        ),

        PlayerSnapshot(
            timestamp=0.1,
            health=100,
            max_health=100,
            dead=False,
            invincible=False,
            change_before_health=100,
            kill_event=True
        ),

        PlayerSnapshot(
            timestamp=0.3,
            health=0,
            max_health=100,
            dead=True,
            invincible=False,
            change_before_health=100,
            death_event=True
        ),
    ]

    result = None

    for sample in samples:
        result = detector.process(sample)

    print_result(
        "TEST A - Normal Kill / Death Flow",
        result
    )


def test_godmode_survival():
    detector = GodModeDetector()

    samples = [
        PlayerSnapshot(
            timestamp=0.0,
            health=100,
            max_health=100,
            dead=False,
            invincible=True,
            change_before_health=100
        ),

        PlayerSnapshot(
            timestamp=0.1,
            health=100,
            max_health=100,
            dead=False,
            invincible=True,
            change_before_health=100,
            kill_event=True
        ),

        PlayerSnapshot(
            timestamp=0.7,
            health=100,
            max_health=100,
            dead=False,
            invincible=True,
            change_before_health=100,
            death_event=False
        ),

        PlayerSnapshot(
            timestamp=1.7,
            health=100,
            max_health=100,
            dead=False,
            invincible=True,
            change_before_health=100
        ),
    ]

    result = None

    for sample in samples:
        result = detector.process(sample)

    print_result(
        "TEST B - GodMode Kill Survival",
        result
    )


def test_normal_heal():
    detector = GodModeDetector()

    samples = [
        PlayerSnapshot(
            timestamp=0.0,
            health=100,
            max_health=100,
            dead=False,
            invincible=False,
            change_before_health=100
        ),

        PlayerSnapshot(
            timestamp=1.0,
            health=50,
            max_health=100,
            dead=False,
            invincible=False,
            change_before_health=100
        ),

        PlayerSnapshot(
            timestamp=2.0,
            health=80,
            max_health=100,
            dead=False,
            invincible=False,
            change_before_health=50,
            heal_event=True
        ),
    ]

    result = None

    for sample in samples:
        result = detector.process(sample)

    print_result(
        "TEST C - Normal Heal",
        result
    )


def test_normal_respawn():
    detector = GodModeDetector()

    samples = [
        PlayerSnapshot(
            timestamp=0.0,
            health=0,
            max_health=100,
            dead=True,
            invincible=False,
            change_before_health=100,
            death_event=True
        ),

        PlayerSnapshot(
            timestamp=2.0,
            health=100,
            max_health=100,
            dead=False,
            invincible=False,
            change_before_health=100,
            respawn_event=True
        ),
    ]

    result = None

    for sample in samples:
        result = detector.process(sample)

    print_result(
        "TEST D - Normal Respawn",
        result
    )


def test_godmode_forced_health_restore():
    """
    실제 GodMode Lua의 다음 패턴을 흉내낸다.

    Health = MaxHealthValue
    ChangeBeforeHealth = MaxHealthValue
    """

    detector = GodModeDetector()

    samples = [
        PlayerSnapshot(
            timestamp=0.0,
            health=100,
            max_health=100,
            dead=False,
            invincible=True,
            change_before_health=100
        ),

        # 어떤 이유로 체력이 감소했다고 가정
        PlayerSnapshot(
            timestamp=0.5,
            health=40,
            max_health=100,
            dead=False,
            invincible=True,
            change_before_health=100
        ),

        # GodMode Lua가 다시 두 값을 MaxHealth로 복구
        PlayerSnapshot(
            timestamp=1.0,
            health=100,
            max_health=100,
            dead=False,
            invincible=True,
            change_before_health=100
        ),

        PlayerSnapshot(
            timestamp=1.6,
            health=100,
            max_health=100,
            dead=False,
            invincible=True,
            change_before_health=100
        ),
    ]

    result = None

    for sample in samples:
        result = detector.process(sample)

    print_result(
        "TEST E - GodMode Forced Health Restore",
        result
    )


if __name__ == "__main__":
    test_normal_death()
    test_godmode_survival()
    test_normal_heal()
    test_normal_respawn()
    test_godmode_forced_health_restore()