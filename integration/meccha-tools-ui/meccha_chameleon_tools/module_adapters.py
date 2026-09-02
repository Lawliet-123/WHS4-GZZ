from pathlib import Path

from meccha_chameleon_tools.module_registry import MODULES


# ============================================================
# Repository path
# ============================================================

def get_repo_root():
    """
    WHS4-GZZ 저장소 루트 반환

    현재 위치:
    integration/
        meccha-tools-ui/
            meccha_chameleon_tools/
                module_adapters.py
    """

    return Path(__file__).resolve().parents[3]


# ============================================================
# Adapter definitions
# ============================================================

MODULE_ADAPTERS = {
    "whistle-spoofing": {
        "type": "native",
        "language": "C++ / Python",
        "expected_files": [
            "README.md",
            "CMakeLists.txt",
            "src/main.cpp",
            "tools/inject.py",
            "tools/sdk_query.py",
            "tools/ue_sdk_dumper.py",
        ],
        "primary_files": [
            "src/main.cpp",
        ],
    },

    "aimbot": {
        "type": "python",
        "language": "Python",
        "expected_files": [
            "README.md",
            "engine.py",
            "requirements.txt",
            "step3_other_players_positions.py",
            "step5_target_angle.py",
            "step8_fov_target.py",
            "step9_aim_trace.py",
        ],
        "primary_files": [
            "engine.py",
        ],
    },

    "auto-paint": {
        "type": "hybrid",
        "language": "Python / C++",
        "expected_files": [
            "README.md",
            "requirements.txt",
            "Scripts/auto_paint.py",
            "Scripts/RUN_AUTO_PAINT.bat",
            "src/bridge/bridge.cpp",
            "src/injector/injector.cpp",
        ],
        "primary_files": [
            "Scripts/auto_paint.py",
        ],
    },

    "esp": {
        "type": "python",
        "language": "Python",
        "expected_files": [
            "README.md",
            "esp.py",
            "requirements.txt",
        ],
        "primary_files": [
            "esp.py",
        ],
    },

    "godmode": {
        "type": "hybrid",
        "language": "Lua / C++",
        "expected_files": [
            "README.md",
            "Mods/GodMode/Scripts/main.lua",
            "Mods/mods.txt",
            "native/godmode_host_402.cpp",
            "native/README_GodModeHost402_KO.md",
        ],
        "primary_files": [
            "Mods/GodMode/Scripts/main.lua",
            "native/godmode_host_402.cpp",
        ],
    },

    "hide-anywhere": {
        "type": "unknown",
        "language": "Unknown",
        "expected_files": [
            "README.md",
        ],
        "primary_files": [],
    },

    "noclip": {
        "type": "lua",
        "language": "Lua",
        "expected_files": [
            "README.md",
            "Scripts/main.lua",
        ],
        "primary_files": [
            "Scripts/main.lua",
        ],
    },
}


# ============================================================
# Registry helpers
# ============================================================

def get_registry_module(module_id):
    """
    module_registry.py에서 모듈 정보 찾기
    """

    for module in MODULES:

        if module["id"] == module_id:
            return module

    return None


def get_adapter_definition(module_id):
    """
    모듈별 adapter 정의 반환
    """

    return MODULE_ADAPTERS.get(
        module_id,
        {
            "type": "unknown",
            "language": "Unknown",
            "expected_files": [],
            "primary_files": [],
        },
    )


# ============================================================
# Path helpers
# ============================================================

def get_module_root(module_id):
    """
    실제 modules/<module> 경로 반환
    """

    module = get_registry_module(
        module_id
    )

    if module is None:
        return None

    return (
        get_repo_root()
        / module["path"]
    )


def resolve_module_file(
    module_id,
    relative_path
):
    """
    모듈 내부 상대 경로를 실제 Path로 변환
    """

    module_root = get_module_root(
        module_id
    )

    if module_root is None:
        return None

    return (
        module_root
        / relative_path
    )


# ============================================================
# File inspection
# ============================================================

def inspect_expected_files(module_id):
    """
    adapter에서 정의한 파일들이 실제로 존재하는지 검사
    """

    definition = get_adapter_definition(
        module_id
    )

    results = []

    for relative_path in definition[
        "expected_files"
    ]:

        path = resolve_module_file(
            module_id,
            relative_path
        )

        exists = (
            path is not None
            and path.exists()
        )

        results.append(
            {
                "relative_path": relative_path,
                "path": (
                    str(path)
                    if path is not None
                    else None
                ),
                "exists": exists,
            }
        )

    return results


def inspect_primary_files(module_id):
    """
    모듈의 핵심 구현 파일 존재 여부 검사

    여기서는 파일 존재 여부만 확인하며
    실행하지 않음.
    """

    definition = get_adapter_definition(
        module_id
    )

    results = []

    for relative_path in definition[
        "primary_files"
    ]:

        path = resolve_module_file(
            module_id,
            relative_path
        )

        exists = (
            path is not None
            and path.exists()
        )

        results.append(
            {
                "relative_path": relative_path,
                "path": (
                    str(path)
                    if path is not None
                    else None
                ),
                "exists": exists,
            }
        )

    return results


# ============================================================
# Module information
# ============================================================

def get_module_adapter_info(module_id):
    """
    UI에서 사용할 수 있도록
    모듈 정보를 공통 형식으로 변환
    """

    registry_module = (
        get_registry_module(
            module_id
        )
    )

    if registry_module is None:

        return {
            "id": module_id,
            "name": module_id,
            "found": False,
            "implemented": False,
            "type": "unknown",
            "language": "Unknown",
            "root": None,
            "primary_files": [],
            "expected_files": [],
        }

    definition = (
        get_adapter_definition(
            module_id
        )
    )

    root = get_module_root(
        module_id
    )

    root_exists = (
        root is not None
        and root.exists()
        and root.is_dir()
    )

    primary_files = (
        inspect_primary_files(
            module_id
        )
    )

    expected_files = (
        inspect_expected_files(
            module_id
        )
    )

    # 핵심 파일이 하나 이상 정의되어 있고
    # 그 파일들이 실제로 존재하면 구현체가 있다고 판단
    if primary_files:

        implemented = any(
            item["exists"]
            for item in primary_files
        )

    else:

        implemented = False

    return {
        "id": registry_module["id"],
        "name": registry_module["name"],
        "description": registry_module[
            "description"
        ],
        "found": root_exists,
        "implemented": implemented,
        "type": definition["type"],
        "language": definition[
            "language"
        ],
        "root": (
            str(root)
            if root is not None
            else None
        ),
        "primary_files": primary_files,
        "expected_files": expected_files,
    }


# ============================================================
# All modules
# ============================================================

def get_all_module_adapter_info():
    """
    등록된 모든 모듈의 adapter 정보 반환
    """

    results = []

    for module in MODULES:

        results.append(
            get_module_adapter_info(
                module["id"]
            )
        )

    return results


# ============================================================
# UI helpers
# ============================================================

def get_module_display_status(module_id):
    """
    UI에서 표시할 간단한 상태 문자열
    """

    info = get_module_adapter_info(
        module_id
    )

    if not info["found"]:
        return "MISSING"

    if not info["implemented"]:
        return "NO IMPLEMENTATION"

    return "READY"


def get_module_summary(module_id):
    """
    UI 상세 화면에서 사용할 요약 정보
    """

    info = get_module_adapter_info(
        module_id
    )

    return {
        "name": info["name"],
        "status": get_module_display_status(
            module_id
        ),
        "type": info["type"],
        "language": info["language"],
        "path": info["root"],
    }


# ============================================================
# Debug
# ============================================================

def print_adapter_status():
    """
    터미널에서 adapter 연결 상태 확인
    """

    print()
    print(
        "MECCHA CHAMELEON MODULE ADAPTERS"
    )

    print(
        "=" * 60
    )

    print(
        f"Repository: {get_repo_root()}"
    )

    print()

    for info in (
        get_all_module_adapter_info()
    ):

        status = (
            get_module_display_status(
                info["id"]
            )
        )

        print(
            f"[{status}] "
            f"{info['name']}"
        )

        print(
            f"  Type     : "
            f"{info['type']}"
        )

        print(
            f"  Language : "
            f"{info['language']}"
        )

        print(
            f"  Root     : "
            f"{info['root']}"
        )

        if info["primary_files"]:

            print(
                "  Primary files:"
            )

            for item in info[
                "primary_files"
            ]:

                marker = (
                    "OK"
                    if item["exists"]
                    else "MISSING"
                )

                print(
                    f"    [{marker}] "
                    f"{item['relative_path']}"
                )

        else:

            print(
                "  Primary files: none"
            )

        print()


if __name__ == "__main__":

    print_adapter_status()