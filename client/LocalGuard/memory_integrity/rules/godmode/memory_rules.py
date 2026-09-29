from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass
class GodModeEvidence:
    module: str
    event_type: str
    target: str
    reason: str
    observed: Any
    expected: Any
    details: Dict[str, Any] = field(default_factory=dict)


class GodModeMemoryRules:
    """
    MECCHA 4.0.2 GodMode 메모리 값 변조 규칙.

    LocalGuard 역할:
    - 값 변조 증거 생성
    - 최종 점수/치터 판정은 하지 않음
    """

    MODULE_NAME = "godmode"

    def evaluate(
        self,
        values: Dict[str, Any],
    ) -> List[GodModeEvidence]:

        evidence = []

        # Hunter는 GodMode 감시 대상에서 제외
        if values.get("is_hunter", False):
            return evidence

        dead = bool(
            values.get(
                "dead",
                False,
            )
        )

        invincible = bool(
            values.get(
                "invincible",
                False,
            )
        )

        health = float(
            values.get(
                "health",
                0.0,
            )
        )

        max_health = float(
            values.get(
                "max_health",
                0.0,
            )
        )

        change_before_health = float(
            values.get(
                "change_before_health",
                0.0,
            )
        )

        # ====================================================
        # Rule 1
        # Invincible 값 변조
        # ====================================================

        if invincible:
            evidence.append(
                GodModeEvidence(
                    module=self.MODULE_NAME,
                    event_type="VALUE_TAMPER",
                    target="Invincible",
                    reason="Invincible flag is enabled",
                    expected=False,
                    observed=True,
                    details={
                        "dead": dead,
                        "health": health,
                        "max_health": max_health,
                    },
                )
            )

        # ====================================================
        # Rule 2
        # GodMode 값 조합
        # ====================================================

        health_is_max = (
            abs(
                health
                - max_health
            )
            < 0.001
        )

        change_before_is_max = (
            abs(
                change_before_health
                - max_health
            )
            < 0.001
        )

        godmode_pattern = (
            invincible
            and not dead
            and health_is_max
            and change_before_is_max
        )

        if godmode_pattern:
            evidence.append(
                GodModeEvidence(
                    module=self.MODULE_NAME,
                    event_type="VALUE_PATTERN",
                    target="GodModeState",
                    reason=(
                        "GodMode-like memory value pattern detected"
                    ),
                    expected={
                        "invincible": False,
                    },
                    observed={
                        "invincible": invincible,
                        "dead": dead,
                        "health": health,
                        "max_health": max_health,
                        "change_before_health":
                            change_before_health,
                    },
                    details={
                        "health_is_max":
                            health_is_max,
                        "change_before_is_max":
                            change_before_is_max,
                    },
                )
            )

        return evidence