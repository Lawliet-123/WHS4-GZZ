"""B Scoring 패키지에서 외부(C 서버 통합 코드)가 사용할 공개 함수/자료형 목록.

실제 저장·중복 방지는 storage.py, 서버 연결은 main.py,
모듈별 점수 특성 점검은 policy.py에 각각 분리되어 있다.
"""

# Receiver/서버 통합부는 아래 함수들을 import해 사용한다.
from .main import configure_scoring, get_player_snapshot, get_player_signal_inventory, process, recover_from_writer
from .storage import ModuleState, ProcessReceipt, ScoringStore

__all__ = (
    "ModuleState",
    "ProcessReceipt",
    "ScoringStore",
    "configure_scoring",
    "get_player_snapshot",
    "get_player_signal_inventory",
    "process",
    "recover_from_writer",
)
