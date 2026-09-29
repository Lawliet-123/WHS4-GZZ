from dataclasses import dataclass
from typing import Any, Dict, List


@dataclass
class NoclipEvidence:
    module: str
    event_type: str
    target: str
    reason: str
    observed: Any
    expected: Any
    details: Dict[str, Any]


class NoclipMemoryRules:
    """
    MECCHA CHAMELEON 4.0.2
    Noclip memory rules.

    Verified memory behavior:

        BodyCapsule + 0x94

        Noclip OFF : 0x2B
        Noclip ON  : 0x23

    Difference:

        0x2B ^ 0x23 = 0x08

    Therefore LocalGuard watches bit 0x08 instead of relying
    on the entire byte value.
    """

    MODULE_NAME = "noclip"

    BODY_CAPSULE_FLAGS_OFFSET = 0x94
    COLLISION_BIT_MASK = 0x08

    def evaluate(
        self,
        body_capsule_flags: int,
    ) -> List[NoclipEvidence]:

        evidence: List[NoclipEvidence] = []

        flags = int(body_capsule_flags) & 0xFF

        collision_bit_enabled = (
            flags & self.COLLISION_BIT_MASK
        ) != 0

        if not collision_bit_enabled:
            evidence.append(
                NoclipEvidence(
                    module=self.MODULE_NAME,
                    event_type="VALUE_TAMPER",
                    target="BodyCapsule.CollisionFlags",
                    reason=(
                        "BodyCapsule collision-related bit "
                        "0x08 is cleared"
                    ),
                    observed=f"0x{flags:02X}",
                    expected="bit 0x08 enabled",
                    details={
                        "offset": (
                            f"0x{self.BODY_CAPSULE_FLAGS_OFFSET:04X}"
                        ),
                        "mask": (
                            f"0x{self.COLLISION_BIT_MASK:02X}"
                        ),
                        "verified_off_value": "0x2B",
                        "verified_on_value": "0x23",
                    },
                )
            )

        return evidence