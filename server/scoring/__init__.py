"""B Scoring 패키지에서 외부(C 서버 통합 코드)가 사용할 공개 함수/자료형 목록.

실제 저장·중복 방지는 storage.py, 서버 연결은 main.py,
모듈별 점수 특성 점검은 policy.py에 각각 분리되어 있다.
"""

# Receiver/서버 통합부는 아래 함수들을 import해 사용한다.
from .main import (
    backfill_event_delta_history_from_writer,
    configure_scoring,
    evaluate_event_policy,
    get_event_delta_history,
    get_godmode_history_summary,
    get_external_access_scoped_state,
    get_whistle_window_conflicts,
    get_whistle_window_history,
    get_player_aggregate_evidence,
    get_player_correlation_candidates,
    get_player_policy_snapshot,
    get_player_risk_input,
    get_player_signal_inventory,
    get_player_snapshot,
    process,
    recover_from_writer,
)
from .aggregate import AggregateEvidence, AggregateSignal
from .history_summary import GodmodeHistorySummary
from .correlation import CorrelationCandidate
from .player_snapshot import ModulePolicySnapshot, PlayerPolicySnapshot
from .storage import (
    DeltaEvent,
    ModuleState,
    ProcessReceipt,
    ScoringStore,
    WindowConflict,
    WindowEvent,
)

__all__ = (
    "AggregateEvidence",
    "AggregateSignal",
    "CorrelationCandidate",
    "DeltaEvent",
    "GodmodeHistorySummary",
    "ModulePolicySnapshot",
    "ModuleState",
    "PlayerPolicySnapshot",
    "ProcessReceipt",
    "ScoringStore",
    "WindowConflict",
    "WindowEvent",
    "backfill_event_delta_history_from_writer",
    "configure_scoring",
    "evaluate_event_policy",
    "get_event_delta_history",
    "get_godmode_history_summary",
    "get_external_access_scoped_state",
    "get_whistle_window_conflicts",
    "get_whistle_window_history",
    "get_player_aggregate_evidence",
    "get_player_correlation_candidates",
    "get_player_policy_snapshot",
    "get_player_risk_input",
    "get_player_snapshot",
    "get_player_signal_inventory",
    "process",
    "recover_from_writer",
)
