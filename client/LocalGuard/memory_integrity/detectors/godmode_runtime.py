from core.result import DetectorResult, Evidence
from core.process_memory import ProcessMemory
from core.pawn_locator import PawnLocator
from rules.godmode.memory_rules import GodModeMemoryRules


PROCESS_NAME = "PenguinHotel-Win64-Shipping.exe"

OFFSET_DEAD = 0x05AA
OFFSET_INVINCIBLE = 0x05AB
OFFSET_HEALTH = 0x0638
OFFSET_MAX_HEALTH = 0x0640
OFFSET_CHANGE_BEFORE_HEALTH = 0x0648
OFFSET_IS_HUNTER = 0x0C3A


def _read_values(memory, pawn_address):
    return {
        "dead": memory.read_bool(
            pawn_address + OFFSET_DEAD
        ),
        "invincible": memory.read_bool(
            pawn_address + OFFSET_INVINCIBLE
        ),
        "health": memory.read_double(
            pawn_address + OFFSET_HEALTH
        ),
        "max_health": memory.read_double(
            pawn_address + OFFSET_MAX_HEALTH
        ),
        "change_before_health": memory.read_double(
            pawn_address + OFFSET_CHANGE_BEFORE_HEALTH
        ),
        "is_hunter": memory.read_bool(
            pawn_address + OFFSET_IS_HUNTER
        ),
    }


def _format_evidence(item, pawn_address):
    value = (
        f"target={item.target}; "
        f"observed={item.observed}; "
        f"expected={item.expected}"
    )

    note = (
        f"{item.event_type}; "
        f"pawn=0x{pawn_address:X}"
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
        detector="godmode_runtime"
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

            values = _read_values(
                memory,
                pawn_address,
            )

            evidence_list = (
                GodModeMemoryRules()
                .evaluate(values)
            )

            result.meta = {
                "source": "ReadProcessMemory",
                "pawn_source": "direct_game_memory_chain",
                "pawn_address": f"0x{pawn_address:X}",
                "is_hunter": values["is_hunter"],
            }

            if not evidence_list:
                return result

            for item in evidence_list:
                if item.reason not in result.reasons:
                    result.reasons.append(
                        item.reason
                    )

                result.evidence.append(
                    _format_evidence(
                        item,
                        pawn_address,
                    )
                )

            result.detail = (
                f"{len(evidence_list)} "
                "GodMode memory evidence item(s) detected"
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
