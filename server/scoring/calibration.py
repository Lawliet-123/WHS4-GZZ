"""ReplayAnalyzer 결과로 확정한 Scoring calibration 설정.

이 파일은 detector 자체의 raw_score 의미를 바꾸지 않는다.
각 detector의 raw 점수를 중앙 Scoring에서 어떤 기준으로 해석할지
버전이 명시된 calibration 설정으로만 보존한다.

Non-replay exceptions carry their own explicit version: kernel-source-provisional-v1
is source-audited integration policy, not an empirically validated replay threshold.

replay-v1:
- 현재 팀 ReplayAnalyzer 데이터로 확정한 첫 공식 calibration 버전
- 이후 추가 replay 결과로 기준이 변경되면 replay-v2 등 새 버전으로 갱신한다.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal, Mapping


CALIBRATION_VERSION = "replay-v1"

CalibrationMode = Literal[
    "threshold",
    "event_threshold",
    "advisory",
    "pending",
]


@dataclass(frozen=True)
class ModuleCalibration:
    """모듈 하나의 중앙 Scoring calibration 규칙."""

    module: str
    mode: CalibrationMode
    threshold: float | None
    version: str = CALIBRATION_VERSION
    note: str = ""
    submodule: str | None = None

    def __post_init__(self) -> None:
        if not self.module:
            raise ValueError("module must not be empty")

        if self.submodule is not None and (
            not isinstance(self.submodule, str)
            or not self.submodule
        ):
            raise ValueError("submodule must be None or a non-empty string")

        if self.mode in ("threshold", "event_threshold"):
            if (
                isinstance(self.threshold, bool)
                or not isinstance(self.threshold, (int, float))
                or not math.isfinite(float(self.threshold))
                or float(self.threshold) < 0
            ):
                raise ValueError(
                    "threshold calibration requires a finite nonnegative threshold"
                )
        elif self.threshold is not None:
            raise ValueError(
                "advisory/pending calibration must not define a threshold"
            )

    @property
    def calibrated(self) -> bool:
        return self.mode in ("threshold", "event_threshold")

    def meets_threshold(self, raw_score: float) -> bool | None:
        """raw_score가 이 calibration 기준을 충족하는지 반환한다.

        advisory/pending은 True/False로 강제 분류하지 않고 None을 반환한다.
        event_threshold도 여기서는 '사건 하나가 기준을 넘었는지'만 평가하며,
        사건 이력의 합산/유지시간은 Aggregate Risk 단계에서 처리한다.
        """
        if not self.calibrated:
            return None

        if (
            isinstance(raw_score, bool)
            or not isinstance(raw_score, (int, float))
            or not math.isfinite(float(raw_score))
            or float(raw_score) < 0
        ):
            raise ValueError("raw_score must be a finite nonnegative number")

        assert self.threshold is not None
        return float(raw_score) >= float(self.threshold)


_ITEMS = (
    ModuleCalibration(
        "kernel_sentinel", "threshold", 3, version="kernel-source-provisional-v1",
        note="Source-audited provisional threshold; not replay validated. Structured evidence gates apply; raw is preserved.",
    ),
    # Replay calibration v1 확정값
    ModuleCalibration(
        "aimbot",
        "threshold",
        4,
        note="Replay v1: CHEAT 4 / NORMAL 4; threshold 4 keeps FP=0, FN=0.",
    ),
    ModuleCalibration(
        "autopaint",
        "threshold",
        23,
        note="Replay v1: CHEAT raw 23/32, NORMAL raw 0.",
    ),
    ModuleCalibration(
        "godmode",
        "event_threshold",
        2,
        note=(
            "Godmode is event_delta. raw_score >= 2 marks one meaningful incident; "
            "history handling is performed separately."
        ),
    ),
    ModuleCalibration(
        "hide_anywhere",
        "threshold",
        3,
        note="Replay v1: strong complete-pattern signal; NORMAL remained 0.",
    ),
    ModuleCalibration(
        "injection",
        "threshold",
        40,
        note="Replay v1: threshold 40 keeps current in-scope replay FP=0, FN=0.",
    ),
    ModuleCalibration(
        "value_tamper",
        "threshold",
        100,
        note="Replay v1: currently validated mainly against Hide Anywhere replay.",
    ),

    # 이 모듈은 정상 세션에서도 raw 100이 관측되어 단독 threshold로 쓰지 않는다.
    ModuleCalibration(
        "filesystem",
        "advisory",
        None,
        note="Advisory evidence only; NORMAL replay also produced raw_score 100.",
    ),

    # 추가 Replay 데이터가 도착하면 replay-v1 안에서 확정하거나
    # 필요하면 replay-v2로 승격한다.
    ModuleCalibration(
        "noclip",
        "threshold",
        3,
        note=(
            "Replay v1: CHEAT 3 / NORMAL 3; threshold 3 keeps "
            "FP=0, FN=0 with avg detection latency about 5.67s."
        ),
    ),
    ModuleCalibration("esp", "pending", None),
    ModuleCalibration(
        "whistle",
        "threshold",
        60,
        note=(
            "Replay v1: 2 in-scope CHEAT sessions and 4 NORMAL sessions "
            "keep FP=0 and FN=0. Threshold 60 activates on either strong "
            "whistle-related ExecFunction or character vtable hook while "
            "leaving the weaker sound-swap-only signal below threshold."
        ),
    ),
    ModuleCalibration(
        "whistle_rpc",
        "advisory",
        None,
        note=(
            "Optional RPC observation path. PR #135 removed it from the default "
            "Launcher because the required hook DLL is not injected in normal "
            "deployments; absence must not make the whole assessment unresolved."
        ),
    ),

    # 현재 중앙 risk calibration 근거가 없는 알려진 모듈들.
    ModuleCalibration(
        "external_access",
        "pending",
        None,
        note=(
            "Derived aggregate only. external_process and module_integrity "
            "have different score/state semantics, so aggregate max(raw_score) "
            "must not be calibrated directly."
        ),
    ),
    ModuleCalibration(
        "localguard_executable_hash",
        "threshold",
        1,
        note=(
            "PR #97 live E2E: complete NORMAL scans produce 0, "
            "exact known executable hash produces 1, and stopping the "
            "executable returns the next complete snapshot to 0. "
            "A positive match proves known executable image presence, "
            "not cheat-function activation."
        ),
    ),
    ModuleCalibration("localguard_yara", "pending", None),
    ModuleCalibration(
        "overlay_hook",
        "threshold",
        60,
        note=(
            "PR #104: game-scope confirmed untrusted hookers "
            "(unsigned/forged/untrusted) use threshold 60; "
            "controlled harness and unverifiable signatures are advisory."
        ),
    ),
    ModuleCalibration(
        "godmode_runtime",
        "threshold",
        5,
        note=(
            "Current score-capable detector: Invincible=2 and GodModeState=3. "
            "Fresh 4.0.2 NORMAL E2E produced six complete raw_score=0 scans; "
            "current-version controlled ON/OFF data separates 0 from 5."
        ),
    ),
    ModuleCalibration(
        "noclip_runtime",
        "threshold",
        1,
        note=(
            "Live E2E: OFF raw_score=0 with collision_flags=0x2B, "
            "controlled Noclip ON raw_score=1 with collision_flags=0x23 "
            "and collision_bit_cleared, then OFF returned to raw_score=0. "
            "Detector contract assigns one point per scan for this condition."
        ),
    ),
    ModuleCalibration(
        "aimbot_runtime",
        "advisory",
        None,
        note=(
            "Runtime aimbot evidence is currently advisory; lack of runtime "
            "evidence must not make the whole assessment unresolved."
        ),
    ),
)


CALIBRATIONS: Mapping[str, ModuleCalibration] = MappingProxyType(
    {item.module: item for item in _ITEMS}
)


# external_access는 하나의 module 문자열 아래 서로 다른 두 탐지 채널이 있다.
# 두 채널은 raw 범위와 NORMAL 0의 의미가 다르므로 별도 calibration을 유지한다.
_EXTERNAL_ACCESS_CALIBRATIONS: Mapping[str, ModuleCalibration] = MappingProxyType({
    "external_process": ModuleCalibration(
        "external_access",
        "threshold",
        2,
        note=(
            "Current detector contract: PROCESS_VM_WRITE and PROCESS_VM_OPERATION "
            "each contribute raw 2 and PROCESS_CREATE_THREAD contributes raw 3. "
            "Latest E2E kept NORMAL at 0 and produced controlled VM_WRITE positives "
            "at raw 3. This channel is state-like and is evaluated from its current "
            "scoped observation."
        ),
        submodule="external_process",
    ),
    "module_integrity": ModuleCalibration(
        "external_access",
        "event_threshold",
        2,
        note=(
            "Latest E2E kept NORMAL at 0 and produced an unsigned added-DLL event "
            "at raw 2 (change 1 + unsigned 1). This channel is event-like: a later "
            "NORMAL 0 means no new DLL change and does not erase a qualifying "
            "historical event."
        ),
        submodule="module_integrity",
    ),
})


_OVERLAY_ACTIVE_SIGNATURES = frozenset({
    "서명없음",
    "위조",
    "신뢰안됨",
})


def get_external_access_calibration(
    submodule: str,
) -> ModuleCalibration | None:
    """external_access 하위 채널의 별도 calibration을 반환한다."""

    return _EXTERNAL_ACCESS_CALIBRATIONS.get(submodule)


def get_calibration(module: str) -> ModuleCalibration | None:
    """등록된 module calibration을 반환한다.

    None은 '정상'이라는 뜻이 아니라 calibration config 자체에 없는 신규 module이다.
    """
    return CALIBRATIONS.get(module)


def resolve_calibration(
    module: str,
    *,
    raw_score: float,
    evidence: Mapping[str, object],
    reasons: tuple[str, ...] | list[str] = (),
) -> ModuleCalibration | None:
    """Event evidence까지 반영한 실제 calibration을 반환한다.

    overlay_hook은 PR #104 structured evidence를 사용해서
    실제 비신뢰 hook과 검증 불가, controlled harness를 구분한다.
    """
    base = get_calibration(module)

    if base is None:
        return None

    if module == "kernel_sentinel":
        from .kernel_sentinel_rules import classify, VERSION
        result = classify(raw_score, evidence, reasons)
        if result in ("normal", "strong", "unavailable"):
            return base
        return ModuleCalibration("kernel_sentinel", "advisory" if result == "advisory" else "pending",
                                 None, version=VERSION,
                                 note="Source-derived provisional evidence classification: " + result)

    if module == "external_access":
        submodule = evidence.get("submodule")

        # 과거 external_process Event는 source_pid만 있고
        # submodule 필드가 없던 형식도 있었다.
        if submodule is None and "source_pid" in evidence:
            submodule = "external_process"

        scoped = (
            get_external_access_calibration(submodule)
            if isinstance(submodule, str)
            else None
        )

        if scoped is not None:
            return scoped

        # aggregate / 미분류 / 과거 불완전 Event는
        # module-level pending 규칙을 유지한다.
        return base

    if module == "localguard_yara":
        ruleset = evidence.get("ruleset")

        if not isinstance(ruleset, Mapping):
            return ModuleCalibration(
                "localguard_yara",
                "pending",
                None,
                note=(
                    "Legacy YARA Event lacks structured ruleset identity; "
                    "raw_score cannot be promoted to an operating threshold."
                ),
            )

        if ruleset.get("test_rules_present") is True:
            return ModuleCalibration(
                "localguard_yara",
                "advisory",
                None,
                note=(
                    "YARA ruleset contains test-only rules; evidence is "
                    "calibration/debug information only."
                ),
            )

        source = ruleset.get("source")

        if source == "custom_cli":
            return ModuleCalibration(
                "localguard_yara",
                "advisory",
                None,
                note=(
                    "Custom CLI YARA ruleset is not an approved production "
                    "calibration source."
                ),
            )

        if source != "repository_default":
            return ModuleCalibration(
                "localguard_yara",
                "pending",
                None,
                note="Unknown YARA ruleset source.",
            )

        ruleset_id = ruleset.get("id")
        files = ruleset.get("files")
        rule_count = ruleset.get("rule_count")

        valid_identity = (
            isinstance(ruleset_id, str)
            and ruleset_id.startswith("sha256:")
            and len(ruleset_id) == 71
            and isinstance(files, list)
            and bool(files)
            and type(rule_count) is int
            and rule_count > 0
        )

        if not valid_identity:
            return ModuleCalibration(
                "localguard_yara",
                "pending",
                None,
                note=(
                    "Repository-default YARA Event has incomplete or malformed "
                    "ruleset identity."
                ),
            )

        # 2026-10-07 live E2E에서 아래 repository-default ruleset을
        # NORMAL 0 / loaded AutoPaint bridge 3으로 분리 검증했다.
        #
        # YARA 3은 실제 페인팅 행동 자체가 아니라 알려진 bridge DLL의
        # 메모리 signature 존재 근거다. active_cheat_proven /
        # cheat_confirmed 의미를 여기서 승격하지 않는다.
        validated_ruleset_id = (
            "sha256:"
            "fce9ed2602083e312eaa938b7a2b9d9f"
            "59b2ab062318a48cd8c712788c627ae6"
        )

        if ruleset_id == validated_ruleset_id:
            return ModuleCalibration(
                "localguard_yara",
                "threshold",
                3,
                note=(
                    "Replay v1: exact repository-default YARA ruleset "
                    "fce9ed...27ae6 produced complete NORMAL raw 0 and "
                    "loaded AutoPaint bridge memory raw 3. Threshold 3 "
                    "classifies the validated artifact signature; it does "
                    "not prove cheat-function activation. Recovery/expiry "
                    "after target termination remains separately unverified."
                ),
            )

        return ModuleCalibration(
            "localguard_yara",
            "pending",
            None,
            note=(
                "Repository-default YARA ruleset identity is available, "
                "but this exact ruleset has not been calibrated by "
                "NORMAL/positive E2E."
            ),
        )

    if module != "overlay_hook":
        return base

    # 정상 측정 0점은 그대로 INACTIVE 판정할 수 있다.
    if float(raw_score) == 0:
        return base

    meta = evidence.get("meta")
    if not isinstance(meta, dict):
        return ModuleCalibration(
            "overlay_hook",
            "pending",
            None,
            note=(
                "Positive legacy overlay event lacks PR #104 "
                "structured metadata."
            ),
        )

    scope = meta.get("measurement_scope")

    # controlled harness는 calibration 검증 자료일 뿐
    # 실제 운영 위험도에는 넣지 않는다.
    if scope == "controlled_harness":
        return ModuleCalibration(
            "overlay_hook",
            "advisory",
            None,
            note="Controlled harness is calibration evidence only.",
        )

    if scope != "game":
        return ModuleCalibration(
            "overlay_hook",
            "pending",
            None,
            note="Positive overlay event has unknown measurement scope.",
        )

    hookers = meta.get("untrusted_hookers")
    if not isinstance(hookers, list) or not hookers:
        return ModuleCalibration(
            "overlay_hook",
            "pending",
            None,
            note="Positive game event lacks structured untrusted_hookers.",
        )

    signatures = {
        item.get("signature")
        for item in hookers
        if isinstance(item, dict)
    }

    # 실제 비신뢰 서명 근거가 하나라도 있으면 threshold 60 적용.
    if signatures & _OVERLAY_ACTIVE_SIGNATURES:
        return base

    # 확인불가는 hook은 관측됐지만 신뢰 검증 자체가 실패한 경우.
    if signatures and signatures <= {"확인불가"}:
        return ModuleCalibration(
            "overlay_hook",
            "advisory",
            None,
            note="Hook observed but signer trust could not be verified.",
        )

    return ModuleCalibration(
        "overlay_hook",
        "pending",
        None,
        note="Unknown overlay signature evidence contract.",
    )


def require_calibration(module: str) -> ModuleCalibration:
    """반드시 등록되어 있어야 하는 module calibration을 반환한다."""
    calibration = get_calibration(module)
    if calibration is None:
        raise KeyError(f"no calibration configured for module: {module}")
    return calibration
