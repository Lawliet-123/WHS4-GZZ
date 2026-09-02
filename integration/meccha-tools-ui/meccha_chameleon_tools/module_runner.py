from dataclasses import dataclass

from meccha_chameleon_tools.module_adapters import (
    get_module_display_status,
)


# ============================================================
# Simulated module effects
# ============================================================

TEST_EFFECTS = {
    "whistle-spoofing": {
        "effect": "whistle_spoofing",
        "description": (
            "Simulated whistle event enabled."
        ),
    },

    "aimbot": {
        "effect": "aim_assist",
        "description": (
            "Simulated aim assist enabled."
        ),
    },

    "auto-paint": {
        "effect": "auto_paint",
        "description": (
            "Simulated auto paint enabled."
        ),
    },

    "esp": {
        "effect": "esp_overlay",
        "description": (
            "Simulated ESP overlay enabled."
        ),
    },

    "godmode": {
        "effect": "damage_immunity",
        "description": (
            "Simulated damage immunity enabled."
        ),
    },

    "hide-anywhere": {
        "effect": "hide_anywhere",
        "description": (
            "Simulated hide-anywhere state enabled."
        ),
    },

    "noclip": {
        "effect": "collision_disabled",
        "description": (
            "Simulated collision disabled."
        ),
    },
}


# ============================================================
# Runner result
# ============================================================

@dataclass
class RunnerResult:
    module_id: str
    status: str
    message: str
    effect: str | None = None


# ============================================================
# Module runner
# ============================================================

class ModuleRunner:
    """
    안티치트 개발용 테스트 Backend.

    실제 MECCHA CHAMELEON 프로세스에는
    주입, 후킹, 메모리 변경 등을 수행하지 않음.

    선택된 모듈의 동작 상태만 시뮬레이션함.
    """

    def __init__(self):

        self.states = {}

        for module_id in TEST_EFFECTS:

            self.states[
                module_id
            ] = {
                "active": False,
                "effect": (
                    TEST_EFFECTS[
                        module_id
                    ]["effect"]
                ),
            }

    # ========================================================
    # Run one module
    # ========================================================

    def run_module(
        self,
        module_id
    ):

        display_status = (
            get_module_display_status(
                module_id
            )
        )

        if display_status != "READY":

            return RunnerResult(
                module_id=module_id,
                status="UNAVAILABLE",
                message=(
                    "Module implementation "
                    "is not available."
                ),
            )

        effect_info = (
            TEST_EFFECTS.get(
                module_id
            )
        )

        if effect_info is None:

            return RunnerResult(
                module_id=module_id,
                status="UNSUPPORTED",
                message=(
                    "No test backend "
                    "is registered."
                ),
            )

        self.states[
            module_id
        ] = {
            "active": True,
            "effect": (
                effect_info[
                    "effect"
                ]
            ),
        }

        return RunnerResult(
            module_id=module_id,
            status="TEST_ACTIVE",
            message=(
                effect_info[
                    "description"
                ]
            ),
            effect=(
                effect_info[
                    "effect"
                ]
            ),
        )

    # ========================================================
    # Run selected modules
    # ========================================================

    def run_selected(
        self,
        module_ids
    ):

        results = []

        for module_id in module_ids:

            result = (
                self.run_module(
                    module_id
                )
            )

            results.append(
                result
            )

        return results

    # ========================================================
    # Stop one module
    # ========================================================

    def stop_module(
        self,
        module_id
    ):

        if module_id not in self.states:

            return RunnerResult(
                module_id=module_id,
                status="UNKNOWN",
                message=(
                    "Module state "
                    "was not found."
                ),
            )

        self.states[
            module_id
        ]["active"] = False

        return RunnerResult(
            module_id=module_id,
            status="TEST_IDLE",
            message=(
                "Simulated module "
                "effect stopped."
            ),
            effect=(
                self.states[
                    module_id
                ]["effect"]
            ),
        )

    # ========================================================
    # Stop selected modules
    # ========================================================

    def stop_selected(
        self,
        module_ids
    ):

        return [
            self.stop_module(
                module_id
            )
            for module_id
            in module_ids
        ]

    # ========================================================
    # State
    # ========================================================

    def is_active(
        self,
        module_id
    ):

        state = (
            self.states.get(
                module_id
            )
        )

        if state is None:
            return False

        return bool(
            state["active"]
        )

    def get_active_modules(self):

        return [
            module_id
            for module_id, state
            in self.states.items()
            if state["active"]
        ]

    def get_snapshot(self):

        return {
            module_id: dict(
                state
            )
            for module_id, state
            in self.states.items()
        }


# ============================================================
# Summary
# ============================================================

def build_runner_summary(
    results
):

    active = [
        result.module_id
        for result in results
        if (
            result.status
            == "TEST_ACTIVE"
        )
    ]

    unavailable = [
        result.module_id
        for result in results
        if (
            result.status
            in (
                "UNAVAILABLE",
                "UNSUPPORTED",
            )
        )
    ]

    parts = []

    if active:

        parts.append(
            "TEST ACTIVE: "
            + ", ".join(
                active
            )
        )

    if unavailable:

        parts.append(
            "Unavailable: "
            + ", ".join(
                unavailable
            )
        )

    if not parts:

        return (
            "No test modules active."
        )

    return " | ".join(
        parts
    )


# ============================================================
# CLI test
# ============================================================

def main():

    runner = ModuleRunner()

    test_modules = [
        "esp",
        "godmode",
        "noclip",
    ]

    results = (
        runner.run_selected(
            test_modules
        )
    )

    print()
    print(
        "MECCHA MODULE TEST BACKEND"
    )
    print(
        "=" * 60
    )

    for result in results:

        print(
            f"[{result.status}] "
            f"{result.module_id} "
            f"-> "
            f"{result.message}"
        )

    print()

    print(
        build_runner_summary(
            results
        )
    )

    print()

    print(
        runner.get_snapshot()
    )


if __name__ == "__main__":

    main()