import os
import subprocess
from pathlib import Path


# ============================================================
# MECCHA CHAMELEON 4.0.2
# ============================================================

GAME_ROOT = Path(
    r"C:\Program Files (x86)\Steam\steamapps\common\MECCHA CHAMELEON"
)

GAME_LAUNCHER = (
    GAME_ROOT
    / "PenguinHotel.exe"
)

GAME_PROCESS = (
    "PenguinHotel-Win64-Shipping.exe"
)

STEAM_APP_ID = "4704690"


# ============================================================
# Game file state
# ============================================================

def game_files_exist():
    """
    MECCHA CHAMELEON 실행 파일 존재 여부 확인
    """

    return (
        GAME_ROOT.exists()
        and GAME_LAUNCHER.exists()
    )


# ============================================================
# Process detection
# ============================================================

def is_game_running():
    """
    현재 MECCHA CHAMELEON 게임 프로세스가
    실행 중인지 확인함.
    """

    try:

        result = subprocess.run(
            [
                "tasklist",
                "/FI",
                f"IMAGENAME eq {GAME_PROCESS}",
            ],
            capture_output=True,
            text=True,
            creationflags=(
                subprocess.CREATE_NO_WINDOW
                if os.name == "nt"
                else 0
            ),
        )

        return (
            GAME_PROCESS.lower()
            in result.stdout.lower()
        )

    except Exception:

        return False


# ============================================================
# Launch game
# ============================================================

def launch_game():
    """
    MECCHA CHAMELEON 4.0.2 실행 요청.

    이미 실행 중인 경우에는
    중복 실행하지 않음.
    """

    if is_game_running():

        return {
            "success": True,
            "status": "RUNNING",
            "message": (
                "Game is already running."
            ),
        }

    if not game_files_exist():

        return {
            "success": False,
            "status": "MISSING",
            "message": (
                "Game executable was not found: "
                + str(GAME_LAUNCHER)
            ),
        }

    try:

        env = os.environ.copy()

        env[
            "SteamAppId"
        ] = STEAM_APP_ID

        env[
            "SteamGameId"
        ] = STEAM_APP_ID

        subprocess.Popen(
            [
                str(GAME_LAUNCHER)
            ],
            cwd=str(
                GAME_ROOT
            ),
            env=env,
        )

        return {
            "success": True,
            "status": "STARTING",
            "message": (
                "Game launch requested."
            ),
        }

    except Exception as exc:

        return {
            "success": False,
            "status": "ERROR",
            "message": str(
                exc
            ),
        }


# ============================================================
# Session information
# ============================================================

def get_game_session():
    """
    현재 게임 상태를 UI에서 사용할 수 있도록
    dict 형태로 반환함.
    """

    files_exist = (
        game_files_exist()
    )

    running = (
        is_game_running()
    )

    if running:

        status = "CONNECTED"

    elif files_exist:

        status = "OFFLINE"

    else:

        status = "MISSING"

    return {
        "status": status,
        "running": running,
        "files_exist": files_exist,
        "game_root": str(
            GAME_ROOT
        ),
        "launcher": str(
            GAME_LAUNCHER
        ),
        "process": GAME_PROCESS,
        "steam_app_id": STEAM_APP_ID,
    }


# ============================================================
# Debug test
# ============================================================

def print_game_session():

    session = (
        get_game_session()
    )

    print()

    print(
        "MECCHA CHAMELEON GAME SESSION"
    )

    print(
        "=" * 60
    )

    print(
        f"Status      : "
        f"{session['status']}"
    )

    print(
        f"Running     : "
        f"{session['running']}"
    )

    print(
        f"Game files  : "
        f"{session['files_exist']}"
    )

    print(
        f"Process     : "
        f"{session['process']}"
    )

    print(
        f"Steam AppID : "
        f"{session['steam_app_id']}"
    )

    print(
        f"Game root   : "
        f"{session['game_root']}"
    )


if __name__ == "__main__":

    print_game_session()