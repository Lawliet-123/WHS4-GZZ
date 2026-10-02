"""서버 A(Receiver)와 B(Scoring)를 연결하는 진입점.

여기서는 이벤트를 안전하게 저장하고 다시 처리할 수 있는 기반만 담당한다.
모듈별 위험도 가중치와 최종 치트 판정은 별도의 policy 단계에서 정의한다.
따라서 Receiver는 향후 점수 정책이 바뀌어도 같은 process() 함수를 호출한다.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any, Mapping

from .policies.contract import PolicyEvaluation
from .policies.registry import evaluate_registered_policy
from .storage import DeltaEvent, ModuleState, ProcessReceipt, ScoringStore, EVENT_DELTA_MODULES

# 기본 DB 경로: 저장 위치를 외부에서 지정하지 않으면 서버 내부 logs/scoring에 생성한다.
_DEFAULT_DB = Path(__file__).resolve().parents[1] / "logs" / "scoring" / "scoring.sqlite3"
# 프로세스 안에서 ScoringStore를 하나만 공유한다. 초기화 경쟁을 막기 위해 Lock을 사용한다.
_store: ScoringStore | None = None
_store_lock = threading.Lock()


def configure_scoring(path: str | Path | None = None) -> ScoringStore:
    """Scoring DB를 한 번 설정하고 같은 저장소 객체를 재사용한다.

    경로 인자를 생략하면 환경변수 GZZ_SCORING_DB 또는 기본 경로를 사용한다.
    import 시 DB 파일을 만들지 않고, 최초 configure/process 호출 때 생성한다.
    이미 다른 경로로 초기화되어 있으면 실수로 DB가 갈리는 일을 막기 위해 거부한다.
    """
    global _store
    resolved = Path(path or os.environ.get("GZZ_SCORING_DB", _DEFAULT_DB))
    with _store_lock:
        if _store is None:
            _store = ScoringStore(resolved)
        elif _store.path.resolve() != resolved.resolve():
            raise RuntimeError("scoring is already configured with a different database")
        return _store


# C가 configure_scoring()을 생략한 경우에도 첫 이벤트에서 기본 DB를 준비한다.
def _get_store() -> ScoringStore:
    return _store if _store is not None else configure_scoring()


def process(
    payload: Mapping[str, Any],
    *,
    event_id: str,
    sequence: int,
) -> ProcessReceipt:
    """A Receiver가 저장까지 마친 공통 7필드 이벤트를 전달하는 함수.

    event_id는 재전송 중복을 식별하고, sequence는 서버 저장 순서를 나타낸다.
    실제 중복 검사·최신 상태 갱신은 storage.ScoringStore가 수행한다.
    """
    return _get_store().process_event(payload, event_id=event_id, sequence=sequence)


def get_player_snapshot(session_id: str, player_id: str) -> list[ModuleState]:
    """특정 세션/플레이어의 모듈별 최신 저장 기록을 조회한다(종합 위험도 아님)."""
    return _get_store().get_player_snapshot(session_id, player_id)


def evaluate_event_policy(payload: Mapping[str, Any]) -> PolicyEvaluation:
    """공통 Event를 module에 맞는 등록 정책으로 평가한다.

    저장/점수 합산을 수행하지 않는 읽기 전용 분석 단계다. 미등록 모듈은
    contract의 안전한 기본 동작에 따라 기존 B2a 상태만 보존한다.
    """
    return evaluate_registered_policy(payload)


def recover_from_writer(writer, *, batch_size: int = 1000) -> int:
    """서버 장애로 B가 놓쳤을 수 있는 A의 영구 저장 이벤트를 재처리한다.

    A의 writer.iter_stored()가 서버 저장 순서(sequence)대로 기록을 반환한다.
    이미 처리된 event_id는 중복 반영되지 않으므로 재실행해도 안전하다.
    이벤트 처리에 성공한 뒤에만 cursor를 전진시켜, 처리 전 장애 시 누락을 방지한다.
    """
    if type(batch_size) is not int or not 1 <= batch_size <= 10000:
        raise ValueError("batch_size must be between 1 and 10000")

    store = _get_store()
    # 실시간으로 받은 최신 sequence가 아닌, 복구 절차가 실제로 확인한 위치를 사용한다.
    cursor = store.get_recovery_cursor()
    while True:
        batch = writer.iter_stored(after_sequence=cursor, limit=batch_size)
        if not batch:
            return cursor
        for record in batch:
            store.process_event(
                record.result,
                event_id=record.event_id,
                sequence=record.sequence,
            )
            # 처리 성공을 확인한 다음에만 복구 커서를 이동한다.
            store.advance_recovery_cursor(record.sequence)
            cursor = record.sequence
        if len(batch) < batch_size:
            return cursor


def get_player_signal_inventory(session_id: str, player_id: str):
    """현재 저장된 모듈 기록을 점검용 비율/주의사항으로 바꿔 반환한다.

    결과는 detector 내부 척도 설명일 뿐, 치트 확률이나 최종 player risk가 아니다.
    """
    from .policy import inspect_player_snapshot

    return inspect_player_snapshot(get_player_snapshot(session_id, player_id))


def get_event_delta_history(
    session_id: str,
    player_id: str,
    *,
    module: str = "godmode",
    after_sequence: int = 0,
    limit: int = 100,
) -> list[DeltaEvent]:
    """새 사건별 이력 조회. 이 값 자체는 종합 위험도나 치트 판정이 아니다."""
    return _get_store().get_event_delta_history(
        session_id, player_id, module=module,
        after_sequence=after_sequence, limit=limit,
    )


def backfill_event_delta_history_from_writer(writer, *, batch_size: int = 1000) -> int:
    """B1 사용 중 누락됐던 과거 Godmode 사건을 Shared 원본 로그에서 채운다.

    일반 recover_from_writer()는 복구 커서부터 읽기 때문에 이전에 처리한
    사건을 다시 보지 못한다. 따라서 수동 업그레이드 작업에서는 처음부터
    전체 원본을 읽고, 사건형 모듈만 기존 event_id와 대조해 안전하게 기록한다.
    여러 번 실행해도 점수 합산·최근 상태 중복 변경은 일어나지 않는다.
    반환값은 마지막으로 검사한 Shared sequence이며, 추가된 사건 개수가 아니다.
    """
    if type(batch_size) is not int or not 1 <= batch_size <= 10000:
        raise ValueError("batch_size must be between 1 and 10000")
    store = _get_store()
    cursor = 0
    while True:
        batch = writer.iter_stored(after_sequence=cursor, limit=batch_size)
        if not batch:
            return cursor
        for record in batch:
            # 현재 감사로 증분형임이 확인된 모듈만 재처리한다.
            if record.result["module"] in EVENT_DELTA_MODULES:
                store.process_event(
                    record.result, event_id=record.event_id, sequence=record.sequence
                )
            cursor = record.sequence
        if len(batch) < batch_size:
            return cursor
