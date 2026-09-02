from copy import deepcopy
from datetime import datetime

from meccha_chameleon_tools.module_registry import MODULES
from meccha_chameleon_tools.module_adapters import (
    get_module_adapter_info,
)


# ============================================================
# Per-module UI settings
# ============================================================

MODULE_SETTINGS = {
    "whistle-spoofing": {
        "enabled": True,
        "profile": "default",
        "notes": "",
    },

    "aimbot": {
        "enabled": True,
        "profile": "default",
        "notes": "",
    },

    "auto-paint": {
        "enabled": True,
        "profile": "default",
        "notes": "",
    },

    "esp": {
        "enabled": True,
        "profile": "default",
        "notes": "",
    },

    "godmode": {
        "enabled": True,
        "profile": "default",
        "notes": "",
    },

    "hide-anywhere": {
        "enabled": False,
        "profile": "default",
        "notes": "",
    },

    "noclip": {
        "enabled": True,
        "profile": "default",
        "notes": "",
    },
}


# ============================================================
# Module workspace
# ============================================================

class ModuleWorkspace:
    """
    UI에서 모듈별 설정과 로그를 관리함.

    실제 게임 프로세스 실행, DLL 주입,
    후킹, 메모리 변경은 수행하지 않음.
    """

    def __init__(self):
        self.settings = {}
        self.logs = {}

        for module in MODULES:
            module_id = module["id"]

            self.settings[module_id] = deepcopy(
                MODULE_SETTINGS.get(
                    module_id,
                    {
                        "enabled": True,
                        "profile": "default",
                        "notes": "",
                    },
                )
            )

            self.logs[module_id] = []

    # ========================================================
    # Module validation
    # ========================================================

    def has_module(self, module_id):
        return module_id in self.settings

    # ========================================================
    # Settings
    # ========================================================

    def get_settings(self, module_id):
        if not self.has_module(module_id):
            return None

        return deepcopy(
            self.settings[module_id]
        )

    def set_setting(
        self,
        module_id,
        key,
        value
    ):
        if not self.has_module(module_id):
            return False

        self.settings[module_id][key] = value

        self.add_log(
            module_id,
            f"Setting changed: {key} = {value}"
        )

        return True

    def reset_settings(self, module_id):
        if not self.has_module(module_id):
            return False

        self.settings[module_id] = deepcopy(
            MODULE_SETTINGS.get(
                module_id,
                {
                    "enabled": True,
                    "profile": "default",
                    "notes": "",
                },
            )
        )

        self.add_log(
            module_id,
            "Settings reset."
        )

        return True

    # ========================================================
    # Logs
    # ========================================================

    def add_log(
        self,
        module_id,
        message,
        level="INFO"
    ):
        if module_id not in self.logs:
            return False

        timestamp = datetime.now().strftime(
            "%H:%M:%S"
        )

        entry = {
            "time": timestamp,
            "level": level,
            "message": str(message),
        }

        self.logs[module_id].append(
            entry
        )

        return True

    def get_logs(self, module_id):
        return list(
            self.logs.get(
                module_id,
                []
            )
        )

    def get_log_text(self, module_id):
        entries = self.get_logs(
            module_id
        )

        if not entries:
            return "No logs yet."

        lines = []

        for entry in entries:
            lines.append(
                f"[{entry['time']}] "
                f"[{entry['level']}] "
                f"{entry['message']}"
            )

        return "\n".join(
            lines
        )

    def clear_logs(self, module_id):
        if module_id not in self.logs:
            return False

        self.logs[module_id] = []

        return True

    # ========================================================
    # Module snapshot
    # ========================================================

    def get_module_snapshot(
        self,
        module_id
    ):
        """
        상세 UI에서 한 번에 사용할 데이터.
        """

        info = get_module_adapter_info(
            module_id
        )

        return {
            "id": module_id,
            "name": info.get(
                "name",
                module_id
            ),
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
            "settings": self.get_settings(
                module_id
            ),
            "logs": self.get_logs(
                module_id
            ),
        }


# ============================================================
# Debug test
# ============================================================

def print_workspace_test():
    workspace = ModuleWorkspace()

    print()
    print(
        "MECCHA CHAMELEON MODULE WORKSPACE"
    )
    print(
        "=" * 60
    )

    for module in MODULES:
        module_id = module["id"]

        workspace.add_log(
            module_id,
            "Workspace initialized."
        )

        snapshot = (
            workspace.get_module_snapshot(
                module_id
            )
        )

        print()
        print(
            snapshot["name"]
        )
        print(
            "-" * 40
        )

        print(
            f"Type       : "
            f"{snapshot['type']}"
        )

        print(
            f"Language   : "
            f"{snapshot['language']}"
        )

        print(
            f"Implemented: "
            f"{snapshot['implemented']}"
        )

        print(
            f"Settings   : "
            f"{snapshot['settings']}"
        )

        print(
            "Logs:"
        )

        print(
            workspace.get_log_text(
                module_id
            )
        )


if __name__ == "__main__":
    print_workspace_test()