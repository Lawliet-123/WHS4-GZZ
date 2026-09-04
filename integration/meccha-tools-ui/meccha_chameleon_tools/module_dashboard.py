from dataclasses import dataclass

from meccha_chameleon_tools.module_registry import MODULES

from meccha_chameleon_tools.module_adapters import (
    get_module_display_status,
)


# ============================================================
# Dashboard item
# ============================================================

@dataclass
class DashboardItem:
    module_id: str
    name: str
    status: str


# ============================================================
# Dashboard state
# ============================================================

class ModuleDashboard:
    """
    전체 모듈 상태를 한 곳에서 관리함.

    실제 모듈 실행은 하지 않음.
    UI 상태 표시용 데이터만 관리함.
    """

    def __init__(self):
        self.runtime_states = {}

        self.refresh()

    # ========================================================
    # Refresh
    # ========================================================

    def refresh(self):
        """
        실제 파일 상태를 기준으로 초기 상태 갱신
        """

        for module in MODULES:

            module_id = module["id"]

            status = (
                get_module_display_status(
                    module_id
                )
            )

            if status == "READY":

                self.runtime_states[
                    module_id
                ] = "READY"

            elif status in (
                "MISSING",
                "NO IMPLEMENTATION",
            ):

                self.runtime_states[
                    module_id
                ] = "UNAVAILABLE"

            else:

                self.runtime_states[
                    module_id
                ] = status

    # ========================================================
    # Runtime state
    # ========================================================

    def set_status(
        self,
        module_id,
        status
    ):
        """
        PREPARED / IDLE 등의 Runtime 상태 반영
        """

        if module_id not in self.runtime_states:
            return False

        self.runtime_states[
            module_id
        ] = status

        return True

    def get_status(
        self,
        module_id
    ):

        return self.runtime_states.get(
            module_id,
            "UNKNOWN"
        )

    # ========================================================
    # Items
    # ========================================================

    def get_items(self):

        items = []

        for module in MODULES:

            module_id = module["id"]

            items.append(
                DashboardItem(
                    module_id=module_id,
                    name=module["name"],
                    status=self.get_status(
                        module_id
                    ),
                )
            )

        return items

    # ========================================================
    # Status count
    # ========================================================

    def get_counts(self):

        counts = {
            "READY": 0,
            "PREPARED": 0,
            "IDLE": 0,
            "UNAVAILABLE": 0,
            "UNKNOWN": 0,
        }

        for status in (
            self.runtime_states.values()
        ):

            if status not in counts:

                counts["UNKNOWN"] += 1

            else:

                counts[status] += 1

        return counts

    # ========================================================
    # Ready count
    # ========================================================

    def get_available_count(self):

        return sum(
            1
            for status
            in self.runtime_states.values()
            if status
            != "UNAVAILABLE"
        )

    # ========================================================
    # Summary
    # ========================================================

    def get_summary(self):

        counts = self.get_counts()

        return (
            f"READY {counts['READY']} | "
            f"PREPARED {counts['PREPARED']} | "
            f"IDLE {counts['IDLE']} | "
            f"UNAVAILABLE "
            f"{counts['UNAVAILABLE']}"
        )


# ============================================================
# Debug test
# ============================================================

def print_dashboard_test():

    dashboard = (
        ModuleDashboard()
    )

    print()
    print(
        "MECCHA CHAMELEON DASHBOARD"
    )

    print(
        "=" * 60
    )

    print()

    for item in dashboard.get_items():

        print(
            f"{item.name:<20} "
            f"{item.status}"
        )

    print()

    counts = dashboard.get_counts()

    print(
        f"READY       : "
        f"{counts['READY']}"
    )

    print(
        f"PREPARED    : "
        f"{counts['PREPARED']}"
    )

    print(
        f"IDLE        : "
        f"{counts['IDLE']}"
    )

    print(
        f"UNAVAILABLE : "
        f"{counts['UNAVAILABLE']}"
    )

    print()

    print(
        dashboard.get_summary()
    )


if __name__ == "__main__":

    print_dashboard_test()