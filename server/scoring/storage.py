"""B Scoring의 SQLite 저장 계층.

목적은 최종 치트 판정이 아니라 데이터의 무결성 보장이다.
- 같은 event_id 재전송을 한 번만 반영한다.
- 동일 모듈의 반복 표본을 합산하지 않고 최신 상태를 갱신한다.
- 늦게 도착한 과거 표본이 새 표본을 덮어쓰지 못하게 한다.
- 실시간 처리 위치와 장애 복구 커서를 별도로 관리한다.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from shared.schema import encode_event, validate_event_id, validate_identifier

# 현재 소스에서 '새 사건의 증분 점수'를 보내는 것으로 확인된 모듈만 포함한다.
# 다른 모듈을 임의로 이 목록에 넣으면 평가 표본까지 사건으로 잘못 기록될 수 있다.
EVENT_DELTA_MODULES = frozenset({"godmode"})


# 이벤트 1건을 처리한 결과: 중복 여부와 최신 상태 변경 여부를 구분한다.
@dataclass(frozen=True)
class ProcessReceipt:
    event_id: str
    sequence: int
    status: str
    state_updated: bool


# (세션, 플레이어, 모듈)별로 현재 저장된 최신 7필드 Event의 상태.
# 이는 최종 위험도가 아니며, 특히 event_delta 모듈에는 최신 기록만으로 충분하지 않다.
@dataclass(frozen=True)
class ModuleState:
    session_id: str
    player_id: str
    module: str
    timestamp_ms: int
    sequence: int
    event_id: str
    raw_score: float
    evidence: dict[str, Any]
    reasons: list[str]


# 최신 모듈 상태와 별개로 보존하는 신규 사건 1건의 원본 탐지 정보.
# 서버가 받은 순서(sequence)와 게임 시간(timestamp_ms)은 서로 다를 수 있다.
@dataclass(frozen=True)
class DeltaEvent:
    event_id: str
    sequence: int
    session_id: str
    player_id: str
    module: str
    timestamp_ms: int
    raw_score: float
    evidence: dict[str, Any]
    reasons: list[str]


class ScoringStore:
    """단일 서버가 사용하는 SQLite 기반 처리 이력·최신 상태 저장소."""

    STORAGE_VERSION = "1"

    def __init__(self, path: str | Path):
        # 기존 DB는 재사용하며, 파일이 없으면 상위 폴더와 테이블을 준비한다.
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        """DB 연결 1개 생성. 호출한 쪽에서 반드시 close()/closing()으로 닫는다."""
        db = sqlite3.connect(self.path, timeout=5.0, isolation_level=None)
        db.row_factory = sqlite3.Row
        # 사건 이력이 processed_events 원본 없이 단독 저장되지 않도록 외래키 검사 활성화.
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA synchronous=FULL")
        return db

    def _initialize(self) -> None:
        """중복 이력, 최신 상태, 복구 위치 테이블을 초기화한다."""
        try:
            with closing(self._connect()) as db:
                db.execute("BEGIN IMMEDIATE")
                db.execute(
                    "CREATE TABLE IF NOT EXISTS metadata ("
                    "key TEXT PRIMARY KEY, value TEXT NOT NULL)"
                )
                # 예전 버전 DB를 새 구조로 착각하지 않도록 저장소 버전을 확인한다.
                row = db.execute(
                    "SELECT value FROM metadata WHERE key='scoring_version'"
                ).fetchone()
                if row is not None and row["value"] != self.STORAGE_VERSION:
                    raise RuntimeError(
                        "unsupported scoring storage version; explicit migration required"
                    )
                db.execute(
                    "INSERT OR IGNORE INTO metadata(key, value) VALUES('scoring_version', ?)",
                    (self.STORAGE_VERSION,),
                )
                db.execute(
                    "INSERT OR IGNORE INTO metadata(key, value) VALUES('recovery_cursor', '0')"
                )
                # event_id: 재전송 중복 검사 / sequence: 서버 저장 순서 / digest: 본문 무결성.
                db.execute(
                    """CREATE TABLE IF NOT EXISTS processed_events (
                        event_id TEXT PRIMARY KEY,
                        sequence INTEGER NOT NULL UNIQUE,
                        digest TEXT NOT NULL
                    )"""
                )
                # 플레이어 1명이 여러 모듈 탐지를 받으므로 3개 식별자를 복합 기본키로 사용한다.
                db.execute(
                    """CREATE TABLE IF NOT EXISTS latest_state (
                        session_id TEXT NOT NULL,
                        player_id TEXT NOT NULL,
                        module TEXT NOT NULL,
                        timestamp_ms INTEGER NOT NULL,
                        sequence INTEGER NOT NULL,
                        event_id TEXT NOT NULL,
                        raw_score REAL NOT NULL,
                        evidence_json TEXT NOT NULL,
                        reasons_json TEXT NOT NULL,
                        PRIMARY KEY(session_id, player_id, module)
                    )"""
                )
                # B2b: Godmode처럼 '새로 발생한 사건'을 보내는 모듈의 기록은
                # 최신 상태 테이블과 분리하여 모두 보존한다.
                # 기존 B1 DB에도 이 테이블만 추가되는 호환 가능한 확장이다.
                # 신규 데이터에 적용하며, 과거 이벤트는 backfill 함수로 보완한다.
                db.execute(
                    """CREATE TABLE IF NOT EXISTS event_delta_history (
                        event_id TEXT PRIMARY KEY,
                        sequence INTEGER NOT NULL UNIQUE,
                        session_id TEXT NOT NULL,
                        player_id TEXT NOT NULL,
                        module TEXT NOT NULL,
                        timestamp_ms INTEGER NOT NULL,
                        raw_score REAL NOT NULL,
                        evidence_json TEXT NOT NULL,
                        reasons_json TEXT NOT NULL,
                        FOREIGN KEY(event_id) REFERENCES processed_events(event_id)
                    )"""
                )
                db.execute(
                    """CREATE INDEX IF NOT EXISTS ix_delta_player_sequence
                       ON event_delta_history(session_id, player_id, module, sequence)"""
                )
                db.commit()
        except sqlite3.Error as exc:
            raise RuntimeError("cannot initialize scoring storage") from exc

    @staticmethod
    def _canonical_event(result: Mapping[str, Any]) -> tuple[dict[str, Any], str]:
        """공통 7필드 검증 → 정해진 JSON 직렬화 → 동일 본문 확인용 SHA-256 생성."""
        payload = encode_event(result)
        event = json.loads(payload)
        digest = hashlib.sha256(payload).hexdigest()
        return event, digest

    @staticmethod
    def _remember_delta_event(
        db: sqlite3.Connection, event: dict[str, Any], event_id: str, sequence: int
    ) -> None:
        """새 사건의 원본 데이터를 재전송 ID 기준으로 정확히 한 번 보존한다.

        새로 받은 이벤트와 기존 B1 처리 이력을 복구하는 이벤트 양쪽에서 사용한다.
        같은 근거가 *다른 event_id*로 또 발생하는 의미상 중복 여부는 여기서
        판단하지 않는다. detector 재시작을 포함한 판정 규칙은 팀 합의가 필요하다.
        """
        if event["module"] not in EVENT_DELTA_MODULES:
            return
        db.execute(
            """INSERT INTO event_delta_history(
                   event_id, sequence, session_id, player_id, module, timestamp_ms,
                   raw_score, evidence_json, reasons_json
               ) VALUES(?,?,?,?,?,?,?,?,?)
               ON CONFLICT(event_id) DO NOTHING""",
            (
                event_id, sequence,
                event["session_id"], event["player_id"], event["module"],
                event["timestamp_ms"], float(event["raw_score"]),
                json.dumps(event["evidence"], ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                json.dumps(event["reasons"], ensure_ascii=False, separators=(",", ":")),
            ),
        )

    def process_event(
        self,
        result: Mapping[str, Any],
        *,
        event_id: str,
        sequence: int,
    ) -> ProcessReceipt:
        """이벤트를 한 번만 반영하고 필요한 경우 최신 모듈 상태를 갱신한다.

        처리 이력 INSERT와 최신 상태 갱신을 같은 DB 트랜잭션에서 실행한다.
        중간에 예외가 나면 둘 다 롤백하여 '처리했다고 표시만 된 이벤트'를 막는다.
        """
        event_id = validate_event_id(event_id)
        if type(sequence) is not int or sequence <= 0:
            raise ValueError("sequence must be a positive integer")

        # 본문을 검증한 다음 동일 event_id가 동일 내용인지도 확인할 수 있게 해시를 만든다.
        event, digest = self._canonical_event(result)

        try:
            db = self._connect()
            try:
                db.execute("BEGIN IMMEDIATE")
                # 재전송된 동일 이벤트는 최신 점수에 다시 반영하지 않는다.
                # 같은 ID로 다른 본문/순서가 오면 단순 중복이 아닌 충돌이므로 거부한다.
                existing = db.execute(
                    "SELECT sequence, digest FROM processed_events WHERE event_id=?",
                    (event_id,),
                ).fetchone()
                if existing is not None:
                    if existing["digest"] != digest or existing["sequence"] != sequence:
                        raise RuntimeError(
                            "event_id is already associated with different scoring input"
                        )
                    # 업그레이드 전 B1이 이미 처리했던 Godmode 이벤트라도,
                    # 원본 Shared feed로 재전송되면 누락된 사건 이력을 채운다.
                    # 최근 상태나 event_id 처리 이력은 다시 누적하지 않는다.
                    self._remember_delta_event(db, event, event_id, sequence)
                    db.commit()
                    return ProcessReceipt(event_id, sequence, "duplicate", False)

                # 하나의 sequence가 서로 다른 이벤트를 뜻하는 경우도 거부한다.
                sequence_owner = db.execute(
                    "SELECT event_id FROM processed_events WHERE sequence=?",
                    (sequence,),
                ).fetchone()
                if sequence_owner is not None:
                    raise RuntimeError("sequence is already associated with another event_id")

                db.execute(
                    "INSERT INTO processed_events(event_id, sequence, digest) VALUES(?,?,?)",
                    (event_id, sequence, digest),
                )

                # 모듈별 현재 상태를 독립 저장한다. 다른 모듈의 raw_score와 합산하지 않는다.
                key = (event["session_id"], event["player_id"], event["module"])
                current = db.execute(
                    """SELECT timestamp_ms, sequence
                       FROM latest_state
                       WHERE session_id=? AND player_id=? AND module=?""",
                    key,
                ).fetchone()

                # game elapsed timestamp가 우선이다. timestamp가 같으면 서버 저장 sequence로 결정한다.
                # 뒤늦게 도착한 오래된 결과도 처리 이력은 기록하지만 최신 상태는 덮어쓰지 않는다.
                should_update = (
                    current is None
                    or event["timestamp_ms"] > current["timestamp_ms"]
                    or (
                        event["timestamp_ms"] == current["timestamp_ms"]
                        and sequence > current["sequence"]
                    )
                )

                if should_update:
                    # INSERT 또는 기존 3중 키의 UPSERT. 점수를 누적(+ 연산)하지 않는다.
                    db.execute(
                        """INSERT INTO latest_state(
                               session_id, player_id, module, timestamp_ms, sequence,
                               event_id, raw_score, evidence_json, reasons_json
                           ) VALUES(?,?,?,?,?,?,?,?,?)
                           ON CONFLICT(session_id, player_id, module) DO UPDATE SET
                               timestamp_ms=excluded.timestamp_ms,
                               sequence=excluded.sequence,
                               event_id=excluded.event_id,
                               raw_score=excluded.raw_score,
                               evidence_json=excluded.evidence_json,
                               reasons_json=excluded.reasons_json""",
                        (
                            event["session_id"],
                            event["player_id"],
                            event["module"],
                            event["timestamp_ms"],
                            sequence,
                            event_id,
                            float(event["raw_score"]),
                            json.dumps(
                                event["evidence"],
                                ensure_ascii=False,
                                sort_keys=True,
                                separators=(",", ":"),
                            ),
                            json.dumps(
                                event["reasons"],
                                ensure_ascii=False,
                                separators=(",", ":"),
                            ),
                        ),
                    )

                # processed_events + 최신 상태 + 새 사건 이력을 한 트랜잭션으로 묶는다.
                # 이력 INSERT 실패 시 세 변경 사항 모두 롤백된다.
                self._remember_delta_event(db, event, event_id, sequence)
                db.commit()
                return ProcessReceipt(event_id, sequence, "processed", should_update)
            except BaseException:
                # 실패한 트랜잭션을 다음 이벤트가 이어받지 않도록 전부 되돌린다.
                if db.in_transaction:
                    db.rollback()
                raise
            finally:
                db.close()
        except sqlite3.Error as exc:
            raise RuntimeError("scoring storage operation failed") from exc

    # 개별 모듈 현재 기록 조회: 정책 계산이나 대시보드가 참조할 수 있다.
    def get_module_state(
        self, session_id: str, player_id: str, module: str
    ) -> ModuleState | None:
        try:
            with closing(self._connect()) as db:
                row = db.execute(
                    """SELECT * FROM latest_state
                       WHERE session_id=? AND player_id=? AND module=?""",
                    (session_id, player_id, module),
                ).fetchone()
        except sqlite3.Error as exc:
            raise RuntimeError("cannot read scoring state") from exc
        return self._row_to_state(row) if row is not None else None

    def get_player_snapshot(self, session_id: str, player_id: str) -> list[ModuleState]:
        """플레이어의 모듈별 최신 기록 목록. 과거 사건 전체를 뜻하지 않는다."""
        try:
            with closing(self._connect()) as db:
                rows = db.execute(
                    """SELECT * FROM latest_state
                       WHERE session_id=? AND player_id=?
                       ORDER BY module""",
                    (session_id, player_id),
                ).fetchall()
        except sqlite3.Error as exc:
            raise RuntimeError("cannot read scoring snapshot") from exc
        return [self._row_to_state(row) for row in rows]

    @staticmethod
    def _row_to_state(row: sqlite3.Row) -> ModuleState:
        """DB의 JSON 문자열을 다시 Python dict/list로 복원한다."""
        return ModuleState(
            session_id=row["session_id"],
            player_id=row["player_id"],
            module=row["module"],
            timestamp_ms=row["timestamp_ms"],
            sequence=row["sequence"],
            event_id=row["event_id"],
            raw_score=row["raw_score"],
            evidence=json.loads(row["evidence_json"]),
            reasons=json.loads(row["reasons_json"]),
        )

    def get_event_delta_history(
        self,
        session_id: str,
        player_id: str,
        *,
        module: str = "godmode",
        after_sequence: int = 0,
        limit: int = 100,
    ) -> list[DeltaEvent]:
        """새 사건 이력을 서버 저장 순서로 나누어 조회한다(최종 위험도 아님).

        게임 시간은 역순으로 도착할 수 있으므로 sequence를 페이지 커서로 사용한다.
        raw_score 합계를 만들지 않는다. 같은 이유로 새 event_id가 전송되는
        detector 재시작 시 재탐지 여부는 현재 규격만으로 구별할 수 없기 때문이다.
        """
        validate_identifier(session_id)
        validate_identifier(player_id)
        if module not in EVENT_DELTA_MODULES:
            raise ValueError("module is not configured for event-delta history")
        if type(after_sequence) is not int or after_sequence < 0:
            raise ValueError("after_sequence must be a nonnegative integer")
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        try:
            with closing(self._connect()) as db:
                rows = db.execute(
                    """SELECT * FROM event_delta_history
                       WHERE session_id=? AND player_id=? AND module=? AND sequence>?
                       ORDER BY sequence LIMIT ?""",
                    (session_id, player_id, module, after_sequence, limit),
                ).fetchall()
        except sqlite3.Error as exc:
            raise RuntimeError("cannot read event-delta history") from exc
        return [
            DeltaEvent(
                event_id=row["event_id"], sequence=row["sequence"],
                session_id=row["session_id"], player_id=row["player_id"],
                module=row["module"], timestamp_ms=row["timestamp_ms"],
                raw_score=row["raw_score"], evidence=json.loads(row["evidence_json"]),
                reasons=json.loads(row["reasons_json"]),
            )
            for row in rows
        ]

    def get_recovery_cursor(self) -> int:
        """장애 복구 feed에서 마지막으로 확인 완료한 서버 저장 sequence 조회."""
        try:
            with closing(self._connect()) as db:
                row = db.execute(
                    "SELECT value FROM metadata WHERE key='recovery_cursor'"
                ).fetchone()
        except sqlite3.Error as exc:
            raise RuntimeError("cannot read scoring recovery cursor") from exc
        return int(row["value"]) if row is not None else 0

    def advance_recovery_cursor(self, sequence: int) -> None:
        """복구로 처리 완료한 sequence에 한해 복구 커서를 이동한다.

        실시간 처리로 새 이벤트가 들어왔다고 이 커서를 옮기면, 더 오래된
        미복구 이벤트를 건너뛸 수 있어 의도적으로 분리했다.
        """
        if type(sequence) is not int or sequence <= 0:
            raise ValueError("sequence must be a positive integer")
        try:
            db = self._connect()
            try:
                db.execute("BEGIN IMMEDIATE")
                current_row = db.execute(
                    "SELECT value FROM metadata WHERE key='recovery_cursor'"
                ).fetchone()
                current = int(current_row["value"]) if current_row is not None else 0
                if sequence < current:
                    db.commit()
                    return
                # 처리 이력이 없는 위치로 복구 커서를 건너뛰지 못하게 한다.
                processed = db.execute(
                    "SELECT 1 FROM processed_events WHERE sequence=?", (sequence,)
                ).fetchone()
                if processed is None:
                    raise RuntimeError(
                        "cannot advance recovery cursor past an unprocessed sequence"
                    )
                db.execute(
                    "UPDATE metadata SET value=? WHERE key='recovery_cursor'",
                    (str(sequence),),
                )
                db.commit()
            except BaseException:
                if db.in_transaction:
                    db.rollback()
                raise
            finally:
                db.close()
        except sqlite3.Error as exc:
            raise RuntimeError("cannot update scoring recovery cursor") from exc
