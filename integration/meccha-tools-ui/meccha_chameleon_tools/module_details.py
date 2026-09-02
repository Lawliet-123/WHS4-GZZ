from meccha_chameleon_tools.module_adapters import (
    get_module_adapter_info,
    get_module_display_status,
)


# ============================================================
# Detail information
# ============================================================

def get_module_details(module_id):
    """
    모듈 상세 화면에서 사용할 정보를
    하나의 dict 형태로 반환함.

    실제 모듈 실행은 하지 않음.
    """

    info = get_module_adapter_info(
        module_id
    )

    status = get_module_display_status(
        module_id
    )

    primary_files = []

    for item in info.get(
        "primary_files",
        []
    ):

        primary_files.append(
            {
                "name": item[
                    "relative_path"
                ],
                "exists": item[
                    "exists"
                ],
                "path": item[
                    "path"
                ],
            }
        )

    expected_files = []

    for item in info.get(
        "expected_files",
        []
    ):

        expected_files.append(
            {
                "name": item[
                    "relative_path"
                ],
                "exists": item[
                    "exists"
                ],
                "path": item[
                    "path"
                ],
            }
        )

    existing_count = sum(
        1
        for item in expected_files
        if item["exists"]
    )

    missing_count = (
        len(expected_files)
        - existing_count
    )

    return {
        "id": info.get(
            "id",
            module_id
        ),

        "name": info.get(
            "name",
            module_id
        ),

        "description": info.get(
            "description",
            ""
        ),

        "status": status,

        "type": info.get(
            "type",
            "unknown"
        ),

        "language": info.get(
            "language",
            "Unknown"
        ),

        "path": info.get(
            "root"
        ),

        "implemented": info.get(
            "implemented",
            False
        ),

        "found": info.get(
            "found",
            False
        ),

        "primary_files": (
            primary_files
        ),

        "expected_files": (
            expected_files
        ),

        "existing_file_count": (
            existing_count
        ),

        "missing_file_count": (
            missing_count
        ),
    }


# ============================================================
# File summary
# ============================================================

def build_file_summary(
    module_id
):
    """
    핵심 파일 검사 결과를 문자열 목록으로 반환
    """

    details = get_module_details(
        module_id
    )

    results = []

    for item in details[
        "primary_files"
    ]:

        marker = (
            "OK"
            if item["exists"]
            else "MISSING"
        )

        results.append(
            f"[{marker}] "
            f"{item['name']}"
        )

    if not results:

        results.append(
            "No primary implementation files."
        )

    return results


# ============================================================
# Detail text
# ============================================================

def build_detail_text(
    module_id
):
    """
    터미널 테스트나 UI 로그에서 사용할
    상세 정보 문자열 생성
    """

    details = get_module_details(
        module_id
    )

    lines = []

    lines.append(
        details["name"]
    )

    lines.append(
        "=" * 50
    )

    lines.append(
        f"Status      : "
        f"{details['status']}"
    )

    lines.append(
        f"Type        : "
        f"{details['type']}"
    )

    lines.append(
        f"Language    : "
        f"{details['language']}"
    )

    lines.append(
        f"Implemented : "
        f"{details['implemented']}"
    )

    lines.append(
        f"Path        : "
        f"{details['path']}"
    )

    lines.append("")

    lines.append(
        "Primary files"
    )

    for item in build_file_summary(
        module_id
    ):

        lines.append(
            f"  {item}"
        )

    lines.append("")

    lines.append(
        "Expected files"
    )

    lines.append(
        f"  Found   : "
        f"{details['existing_file_count']}"
    )

    lines.append(
        f"  Missing : "
        f"{details['missing_file_count']}"
    )

    return "\n".join(
        lines
    )


# ============================================================
# Debug
# ============================================================

def print_detail_test():

    test_modules = [
        "whistle-spoofing",
        "aimbot",
        "auto-paint",
        "esp",
        "godmode",
        "hide-anywhere",
        "noclip",
    ]

    print()

    print(
        "MECCHA CHAMELEON MODULE DETAILS"
    )

    print(
        "=" * 60
    )

    for module_id in test_modules:

        print()

        print(
            build_detail_text(
                module_id
            )
        )

        print()


if __name__ == "__main__":

    print_detail_test()