"""런처가 실행할 모듈 등록표.

**팀원이 자기 모듈을 런처에 붙이려면 이 파일에 한 줄만 추가하면 된다.**
런처 본체(main.py, process_manager.py)는 건드릴 필요가 없다.

등록표를 코드 밖으로 빼둔 이유는 하나다. 붙이는 사람과 돌리는 사람이 다르면
"내 모듈이 안 붙는다"는 말이 나오는데, 그때 고칠 곳이 한 군데여야 한다.

## 실행 방식이 모듈마다 다르다 — 확인하고 적은 것

  external_access   상대 import(`from ..common import`)를 써서 **`-m` 으로만** 돈다.
                    `python runner.py` 로 직접 실행하면 ImportError 가 난다.
  나머지            `python <경로>/main.py` 로 돈다. 파이썬이 스크립트 폴더를
                    sys.path[0] 에 넣어주기 때문에 자기 옆 모듈을 찾는다.

추측하지 않고 실제로 `--help` 를 돌려서 확인했다(2026-09-27).
"""

import os
import sys
from dataclasses import dataclass, field
from typing import Dict, List, Optional

# 이 파일은 client/Launcher/ 에 있다. 레포 루트는 두 단계 위.
REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

GAME_EXE = "PenguinHotel-Win64-Shipping.exe"

# 게임 설치 위치. 사람마다 다를 수 있어 없으면 런처가 직접 찾는다.
GAME_DIR = (r"C:\Program Files (x86)\Steam\steamapps\common"
            r"\MECCHA CHAMELEON\Chameleon\Binaries\Win64")

# 실행 방식
ONESHOT = "oneshot"        # 한 번 돌고 끝난다. 주기 검사는 런처가 다시 부른다
CONTINUOUS = "continuous"  # 자기가 알아서 계속 돈다


@dataclass
class Module:
    name: str
    owner: str                      # 누구 담당인지. 안 붙을 때 물어볼 사람
    argv: List[str]                 # {session} {player} 자리표시자를 쓸 수 있다
    mode: str = CONTINUOUS
    cwd: Optional[str] = None       # None 이면 레포 루트
    needs_game: bool = True         # 게임이 떠 있어야 의미가 있는가
    needs_admin: bool = False       # 관리자 권한이 필요한가
    every_s: float = 0.0            # ONESHOT 을 몇 초마다 다시 부를지 (0 이면 한 번만)
    # 이 모듈이 세션 로그(<세션>.jsonl)를 쓰는 폴더. 런처가 시작 전에
    # "그 세션 이름이 이미 있는지" 를 보려고 쓴다. 레포 루트 기준 상대경로.
    session_log_dir: str = ""
    note: str = ""

    def resolved(self, ctx: Dict[str, object]) -> List[str]:
        """자리표시자를 채운 실제 명령.

        `{window}` 는 실행할 때마다 달라지므로 시작 시점에 채운다.
        `{t0}` 는 세션 전체가 같은 시계를 쓰게 하려고 넘긴다 — 주기 검사는
        실행마다 새 프로세스라, 안 넘기면 시각이 매번 0 으로 되돌아간다.
        """
        out = []
        for a in self.argv:
            for k, v in ctx.items():
                a = a.replace("{" + k + "}", str(v))
            out.append(a)
        return out

    def script_path(self) -> Optional[str]:
        """존재 여부를 확인할 파일. `-m` 실행이면 모듈 경로로 바꿔 본다."""
        if "-m" in self.argv:
            mod = self.argv[self.argv.index("-m") + 1]
            return os.path.join(REPO, *mod.split(".")) + ".py"
        for a in self.argv[1:]:
            if a.endswith(".py"):
                return a if os.path.isabs(a) else os.path.join(REPO, a)
        return None


PY = sys.executable

MODULES: List[Module] = [
    # ── 게임과 무관하게 먼저 뜨는 것 ────────────────────────────────────
    Module(
        name="self_defense",
        owner="4번 (성민)",
        argv=[PY, "client/SelfDefense/main.py"],
        needs_game=False,
        note="워치독·안티디버깅·자체 무결성. 아직 폴더가 비어 있다",
    ),
    Module(
        name="kernel_watcher",
        owner="5번 (찬준)",
        argv=[PY, "client/KernelWatcher/main.py"],
        needs_game=False,
        needs_admin=True,          # 드라이버를 올려야 한다
        note="커널 프로세스·드라이버 관측. 아직 폴더가 비어 있다",
    ),

    # ── 게임이 떠 있어야 하는 것 ────────────────────────────────────────
    Module(
        name="external_access",
        owner="1번 (은지·지완)",
        argv=[PY, "-m", "client.LocalGuard.external_access.process_access.runner",
              "--game-exe", GAME_EXE,
              "--session-id", "{session}", "--player-id", "{player}",
              "--output", "client/LocalGuard/external_access/logs/external_access.jsonl"],
        mode=CONTINUOUS,
        note="위험 핸들 감시. 상대 import 라 -m 으로만 돈다",
    ),
    Module(
        name="input_signature",
        owner="3번 (동효)",
        argv=[PY, "client/LocalGuard/input_signature/main.py"],
        note="Raw Input 대조·YARA·해시. 아직 폴더가 비어 있다",
    ),
    Module(
        name="memory_integrity",
        owner="2번 (재민·랑언)",
        argv=[PY, "client/LocalGuard/memory_integrity/run_session.py",
              "--session", "{session}", "--player", "{player}",
              "--log-name", "{session}", "--t0", "{t0}", "--window", "{window}"],
        mode=ONESHOT,
        every_s=30.0,
        session_log_dir="client/LocalGuard/memory_integrity/logs/detection",
        note="값 변조·코드 무결성·후킹. 한 번 스캔에 수 초~10초대라 주기 검사다",
    ),
    Module(
        name="whistle_spoofing",
        owner="휘파람 (랑언)",
        argv=[PY, "client/detectors/whistle-spoofing/main.py",
              "--session", "{session}", "--player", "{player}",
              "--log-name", "{session}", "--t0", "{t0}", "--window", "{window}"],
        mode=ONESHOT,
        every_s=30.0,
        session_log_dir="client/detectors/whistle-spoofing/logs/detection",
        note="휘파람 후킹 흔적 + 도발 RPC",
    ),
    Module(
        name="aimbot",
        owner="에임봇 (은지)",
        argv=[PY, "client/detectors/aimbot/main.py"],
        mode=CONTINUOUS,
        note="UE4SS DamageLogger 가 남기는 텔레메트리를 읽는다",
    ),
]


def by_name() -> Dict[str, Module]:
    return {m.name: m for m in MODULES}
