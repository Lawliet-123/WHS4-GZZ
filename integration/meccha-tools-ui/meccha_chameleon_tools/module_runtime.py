from dataclasses import dataclass
from typing import Dict, List

from meccha_chameleon_tools.module_adapters import (
    get_module_adapter_info,
    get_module_display_status,
)


# ============================================================
# Runtime result
# ============================================================

@dataclass
class RuntimeResult:
    module_id: str
    name: str
    status: str
    message: str
    adapter_type: str
    language: str
    path: str | None


# ============================================================
# Base runtime adapter
# ============================================================

class BaseRuntimeAdapter:
    """
    모든 모듈이 공통으로 사용하는 Runtime Adapter.

    실제 게임 프로세스 실행/주입/후킹은 수행하지 않음.
    여기서는 준비 상태와 모듈 정보를 공통 형식으로 관리함.
    """

    runtime_type = "generic"

    def __init__(self, module_id):
        self.module_id = module_id

        self.info = get_module_adapter_info(
            module_id
        )

        self.state = "IDLE"

    # --------------------------------------------------------
    # Module information
    # --------------------------------------------------------

    @property
    def name(self):
        return self.info.get(
            "name",
            self.module_id
        )

    @property
    def language(self):
        return self.info.get(
            "language",
            "Unknown"
        )

    @property
    def path(self):
        return self.info.get(
            "root"
        )

    # --------------------------------------------------------
    # Prepare
    # --------------------------------------------------------

    def prepare(self):
        """
        모듈 폴더와 핵심 구현 파일을 확인함.
        """

        display_status = (
            get_module_display_status(
                self.module_id
            )
        )

        if display_status == "MISSING":

            self.state = "UNAVAILABLE"

            return RuntimeResult(
                module_id=self.module_id,
                name=self.name,
                status=self.state,
                message="Module folder not found.",
                adapter_type=self.runtime_type,
                language=self.language,
                path=self.path,
            )

        if display_status == "NO IMPLEMENTATION":

            self.state = "UNAVAILABLE"

            return RuntimeResult(
                module_id=self.module_id,
                name=self.name,
                status=self.state,
                message="Implementation not available.",
                adapter_type=self.runtime_type,
                language=self.language,
                path=self.path,
            )

        self.state = "PREPARED"

        return RuntimeResult(
            module_id=self.module_id,
            name=self.name,
            status=self.state,
            message="Module is prepared.",
            adapter_type=self.runtime_type,
            language=self.language,
            path=self.path,
        )

    # --------------------------------------------------------
    # Start request
    # --------------------------------------------------------

    def request_start(self):
        """
        통합 UI에서 실행 요청을 받았을 때 사용하는 공통 진입점.

        실제 외부 프로세스 실행, DLL 주입, 후킹,
        게임 메모리 변경은 여기서 수행하지 않음.
        """

        if self.state != "PREPARED":

            prepare_result = self.prepare()

            if prepare_result.status != "PREPARED":
                return prepare_result

        self.state = "READY"

        return RuntimeResult(
            module_id=self.module_id,
            name=self.name,
            status=self.state,
            message=(
                "Module adapter is ready. "
                "Execution backend is not attached."
            ),
            adapter_type=self.runtime_type,
            language=self.language,
            path=self.path,
        )

    # --------------------------------------------------------
    # Stop
    # --------------------------------------------------------

    def stop(self):

        self.state = "IDLE"

        return RuntimeResult(
            module_id=self.module_id,
            name=self.name,
            status=self.state,
            message="Module runtime reset.",
            adapter_type=self.runtime_type,
            language=self.language,
            path=self.path,
        )


# ============================================================
# Module-specific runtime adapters
# ============================================================

class WhistleRuntimeAdapter(
    BaseRuntimeAdapter
):
    runtime_type = "native"


class AimbotRuntimeAdapter(
    BaseRuntimeAdapter
):
    runtime_type = "python"


class AutoPaintRuntimeAdapter(
    BaseRuntimeAdapter
):
    runtime_type = "hybrid"


class ESPRuntimeAdapter(
    BaseRuntimeAdapter
):
    runtime_type = "python"


class GodModeRuntimeAdapter(
    BaseRuntimeAdapter
):
    runtime_type = "hybrid"


class HideAnywhereRuntimeAdapter(
    BaseRuntimeAdapter
):
    runtime_type = "unavailable"


class NoclipRuntimeAdapter(
    BaseRuntimeAdapter
):
    runtime_type = "lua"


# ============================================================
# Adapter registry
# ============================================================

RUNTIME_ADAPTERS = {
    "whistle-spoofing": WhistleRuntimeAdapter,
    "aimbot": AimbotRuntimeAdapter,
    "auto-paint": AutoPaintRuntimeAdapter,
    "esp": ESPRuntimeAdapter,
    "godmode": GodModeRuntimeAdapter,
    "hide-anywhere": HideAnywhereRuntimeAdapter,
    "noclip": NoclipRuntimeAdapter,
}


# ============================================================
# Runtime adapter factory
# ============================================================

def create_runtime_adapter(
    module_id
):
    """
    module_id에 맞는 Runtime Adapter 생성
    """

    adapter_class = RUNTIME_ADAPTERS.get(
        module_id,
        BaseRuntimeAdapter,
    )

    return adapter_class(
        module_id
    )


# ============================================================
# Runtime manager
# ============================================================

class ModuleRuntimeManager:
    """
    UI에서 선택된 여러 모듈을 한 번에 관리하는 클래스.
    """

    def __init__(self):

        self.adapters: Dict[
            str,
            BaseRuntimeAdapter
        ] = {}

    # --------------------------------------------------------
    # Get adapter
    # --------------------------------------------------------

    def get_adapter(
        self,
        module_id
    ):

        if module_id not in self.adapters:

            self.adapters[
                module_id
            ] = create_runtime_adapter(
                module_id
            )

        return self.adapters[
            module_id
        ]

    # --------------------------------------------------------
    # Prepare selected
    # --------------------------------------------------------

    def prepare_selected(
        self,
        selected_ids
    ) -> List[RuntimeResult]:

        results = []

        for module_id in selected_ids:

            adapter = self.get_adapter(
                module_id
            )

            result = adapter.prepare()

            results.append(
                result
            )

        return results

    # --------------------------------------------------------
    # Request selected modules
    # --------------------------------------------------------

    def request_start_selected(
        self,
        selected_ids
    ) -> List[RuntimeResult]:

        results = []

        for module_id in selected_ids:

            adapter = self.get_adapter(
                module_id
            )

            result = (
                adapter.request_start()
            )

            results.append(
                result
            )

        return results

    # --------------------------------------------------------
    # Stop selected
    # --------------------------------------------------------

    def stop_selected(
        self,
        selected_ids
    ) -> List[RuntimeResult]:

        results = []

        for module_id in selected_ids:

            adapter = self.get_adapter(
                module_id
            )

            result = adapter.stop()

            results.append(
                result
            )

        return results


# ============================================================
# Result helpers
# ============================================================

def build_runtime_summary(
    results
):

    prepared = []
    unavailable = []
    other = []

    for result in results:

        if result.status in (
            "PREPARED",
            "READY",
        ):

            prepared.append(
                result.name
            )

        elif result.status == (
            "UNAVAILABLE"
        ):

            unavailable.append(
                result.name
            )

        else:

            other.append(
                result.name
            )

    messages = []

    if prepared:

        messages.append(
            "Ready: "
            + ", ".join(prepared)
        )

    if unavailable:

        messages.append(
            "Unavailable: "
            + ", ".join(unavailable)
        )

    if other:

        messages.append(
            "Other: "
            + ", ".join(other)
        )

    if not messages:

        return "No modules selected."

    return " | ".join(
        messages
    )


# ============================================================
# Debug test
# ============================================================

def print_runtime_test():

    print()

    print(
        "MECCHA CHAMELEON MODULE RUNTIME"
    )

    print(
        "=" * 60
    )

    selected = [
        "whistle-spoofing",
        "aimbot",
        "auto-paint",
        "esp",
        "godmode",
        "hide-anywhere",
        "noclip",
    ]

    manager = (
        ModuleRuntimeManager()
    )

    results = (
        manager.prepare_selected(
            selected
        )
    )

    for result in results:

        print(
            f"[{result.status}] "
            f"{result.name}"
        )

        print(
            f"  Adapter  : "
            f"{result.adapter_type}"
        )

        print(
            f"  Language : "
            f"{result.language}"
        )

        print(
            f"  Message  : "
            f"{result.message}"
        )

        print()

    print(
        build_runtime_summary(
            results
        )
    )


if __name__ == "__main__":

    print_runtime_test()