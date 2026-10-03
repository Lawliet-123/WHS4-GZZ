
from core.result import DetectorResult, Evidence
from core.process_memory import ProcessMemory
from core.pawn_locator import PawnLocator
from rules.noclip.memory_rules import NoclipMemoryRules


PROCESS_NAME = "PenguinHotel-Win64-Shipping.exe"

OFFSET_BODY_CAPSULE = 0x420
OFFSET_COLLISION_FLAGS = 0x94

REASON_CODE = "collision_bit_cleared"

# 단일 메모리 검사에서 충돌 비트 해제 확인 시 부여하는 점수
COLLISION_DISABLED_SCORE = 1


def _format_evidence(
    item,
    pawn_address,
    body_capsule_address,
):
    value = (
        f"target={item.target}; "
        f"observed={item.observed}; "
        f"expected={item.expected}"
    )

    note = (
        f"{item.event_type}; "
        f"reason={item.reason}; "
        f"pawn=0x{pawn_address:X}; "
        f"body_capsule=0x{body_capsule_address:X}"
    )

    if item.details:
        note += f"; details={item.details}"

    return Evidence(
        type="value",
        value=value,
        note=note,
    )


def scan():
    result = DetectorResult(
        detector="noclip_runtime"
    )

    try:
        with ProcessMemory(
            PROCESS_NAME
        ) as memory:

            locator = PawnLocator(
                memory
            )

            pawn_address = (
                locator.locate()
            )

            if not pawn_address:
                return result.unavailable(
                    "Local player Pawn is not available"
                )

            body_capsule_address = (
                memory.read_pointer(
                    pawn_address
                    + OFFSET_BODY_CAPSULE
                )
            )

            if not body_capsule_address:
                return result.unavailable(
                    "BodyCapsule component is not available"
                )

            collision_flags = (
                memory.read_uint8(
                    body_capsule_address
                    + OFFSET_COLLISION_FLAGS
                )
            )

            evidence_list = (
                NoclipMemoryRules()
                .evaluate(
                    collision_flags
                )
            )

            result.meta = {
                "source": "ReadProcessMemory",
                "pawn_source": "direct_game_memory_chain",
                "pawn_address": f"0x{pawn_address:X}",
                "body_capsule_address": (
                    f"0x{body_capsule_address:X}"
                ),
                "collision_flags": (
                    f"0x{collision_flags:02X}"
                ),
            }

            if not evidence_list:
                return result

            for item in evidence_list:
                if REASON_CODE not in result.reasons:
                    result.reasons.append(
                        REASON_CODE
                    )

                    # 같은 검사 내 중복 근거에 중복 점수를 주지 않는다.
                    result.score = min(
                        100,
                        result.score + COLLISION_DISABLED_SCORE,
                    )

                result.evidence.append(
                    _format_evidence(
                        item,
                        pawn_address,
                        body_capsule_address,
                    )
                )

            result.detail = (
                f"{len(evidence_list)} "
                "Noclip memory evidence item(s) detected"
            )

            return result

    except Exception as error:
        return result.fail(
            f"{type(error).__name__}: {error}"
        )


if __name__ == "__main__":
    print(
        scan().to_dict()
    )
