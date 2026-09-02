from pathlib import Path

from meccha_chameleon_tools.module_registry import MODULES


# ============================================================
# Repository paths
# ============================================================

def get_repo_root():
    """
    현재 파일 위치:

    WHS4-GZZ/
    └── integration/
        └── meccha-tools-ui/
            └── meccha_chameleon_tools/
                └── module_actions.py

    parents[3] = WHS4-GZZ 저장소 루트
    """

    return Path(__file__).resolve().parents[3]


def get_modules_root():
    """
    WHS4-GZZ/modules 경로 반환
    """

    return get_repo_root() / "modules"


# ============================================================
# Module lookup
# ============================================================

def get_module_by_id(module_id):
    """
    module_registry.py에서 ID에 해당하는 모듈 정보 찾기
    """

    for module in MODULES:

        if module["id"] == module_id:
            return module

    return None


def get_module_path(module):
    """
    registry의 상대 경로를 실제 절대 경로로 변환
    """

    relative_path = module.get("path", "")

    return get_repo_root() / relative_path


# ============================================================
# Module state
# ============================================================

def module_exists(module):
    """
    실제 modules/<module> 폴더가 존재하는지 확인
    """

    path = get_module_path(module)

    return path.exists() and path.is_dir()


def get_module_state(module_id):
    """
    특정 모듈의 현재 상태 반환
    """

    module = get_module_by_id(module_id)

    if module is None:

        return {
            "id": module_id,
            "name": module_id,
            "exists": False,
            "ready": False,
            "path": None,
            "message": "Unknown module",
        }

    path = get_module_path(module)

    exists = module_exists(module)

    registry_ready = (
        module.get("status", "ready") == "ready"
    )

    ready = exists and registry_ready

    if not exists:

        message = "Module folder not found"

    elif not registry_ready:

        message = "Module is not ready"

    else:

        message = "Ready"

    return {
        "id": module["id"],
        "name": module["name"],
        "exists": exists,
        "ready": ready,
        "path": str(path),
        "message": message,
    }


# ============================================================
# Selected modules
# ============================================================

def prepare_selected_modules(selected_ids):
    """
    UI에서 선택한 모듈을 검사해서 사용할 수 있는 모듈과
    문제가 있는 모듈을 구분함.

    실제 모듈 실행은 하지 않음.
    """

    ready_modules = []
    unavailable_modules = []

    for module_id in selected_ids:

        state = get_module_state(module_id)

        if state["ready"]:

            ready_modules.append(state)

        else:

            unavailable_modules.append(state)

    return {
        "selected_count": len(selected_ids),
        "ready_count": len(ready_modules),
        "unavailable_count": len(unavailable_modules),
        "ready_modules": ready_modules,
        "unavailable_modules": unavailable_modules,
    }


# ============================================================
# UI text helpers
# ============================================================

def build_selection_message(result):
    """
    module_selector.py에서 바로 표시할 수 있는 문자열 생성
    """

    ready_modules = result["ready_modules"]

    unavailable_modules = result[
        "unavailable_modules"
    ]

    if not ready_modules and not unavailable_modules:

        return "No modules selected."

    messages = []

    if ready_modules:

        names = [
            module["name"]
            for module in ready_modules
        ]

        messages.append(
            "Ready: " + ", ".join(names)
        )

    if unavailable_modules:

        names = [
            module["name"]
            for module in unavailable_modules
        ]

        messages.append(
            "Unavailable: " + ", ".join(names)
        )

    return " | ".join(messages)


# ============================================================
# Debug / test
# ============================================================

def print_module_status():
    """
    터미널에서 module_actions.py만 테스트할 때 사용
    """

    print()
    print("MECCHA CHAMELEON MODULE STATUS")
    print("=" * 50)

    print(
        f"Repository : {get_repo_root()}"
    )

    print(
        f"Modules    : {get_modules_root()}"
    )

    print()

    for module in MODULES:

        state = get_module_state(
            module["id"]
        )

        status_text = (
            "READY"
            if state["ready"]
            else "NOT READY"
        )

        print(
            f"[{status_text}] "
            f"{state['name']}"
        )

        print(
            f"  {state['path']}"
        )

        print(
            f"  {state['message']}"
        )

        print()


if __name__ == "__main__":
    print_module_status()