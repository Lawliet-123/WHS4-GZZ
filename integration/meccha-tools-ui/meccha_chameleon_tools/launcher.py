import sys

from PyQt5.QtWidgets import QApplication

from meccha_chameleon_tools.game_session import (
    launch_game,
)

from meccha_chameleon_tools.module_selector import (
    ModuleSelector,
)


# ============================================================
# MECCHA CHAMELEON Tools Launcher
# ============================================================

def main():
    """
    MECCHA CHAMELEON 4.0.2와
    통합 Module UI를 함께 실행함.
    """

    # --------------------------------------------------------
    # Qt Application
    # --------------------------------------------------------

    app = QApplication(
        sys.argv
    )

    # --------------------------------------------------------
    # Launch game
    # --------------------------------------------------------

    game_result = (
        launch_game()
    )

    print()
    print(
        "MECCHA CHAMELEON TOOLS"
    )

    print(
        "=" * 60
    )

    print(
        f"Game status : "
        f"{game_result['status']}"
    )

    print(
        f"Message     : "
        f"{game_result['message']}"
    )

    # --------------------------------------------------------
    # Launch module UI
    # --------------------------------------------------------

    window = (
        ModuleSelector()
    )

    window.show()

    # --------------------------------------------------------
    # Qt event loop
    # --------------------------------------------------------

    sys.exit(
        app.exec_()
    )


if __name__ == "__main__":
    main()