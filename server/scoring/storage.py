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

# external_access는 같은 module 문자열 아래 두 독립 관측 채널을 사용한다.
# module-level latest_state와 별도로 각 채널의 최신 상태를 보존한다.
EXTERNAL_ACCESS_SUBMODULES = (
    "external_process",
    "module_integrity",
)

# localguard_yara는 PR #84 이후 정상 0점도 PID/scope별로 전송한다.
# 서로 다른 대상의 NORMAL 0이 다른 대상의 양수 상태를 지우지 않도록
# scoped_latest_state에서 각 PID/scope를 독립 상태로 보존한다.
YARA_SCOPES = frozenset({
    "selected_local_process_memory",
    "same_session_external_python_memory",
    "loaded_autopaint_bridge_module_memory",
    "known_autopaint_bridge_module_inventory",
})


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


@dataclass(frozen=True)
class WindowEvent:
    """whistle_rpc 검사 window 1개의 보존된 관측.

    window_id/sample_id가 유효하지 않아도 원본 Event는 보존한다.
    그 경우 semantic_key_valid=False이며 의미 중복 키에는 사용하지 않는다.
    """

    event_id: str
    sequence: int
    session_id: str
    player_id: str
    module: str
    window_id: int | None
    sample_id: int | None
    semantic_key_valid: bool
    timestamp_ms: int
    raw_score: float
    evidence: dict[str, Any]
    reasons: list[str]


@dataclass(frozen=True)
class WindowConflict:
    """같은 whistle_rpc window에 서로 다른 관측 내용이 들어온 경우의 감사 기록."""

    canonical_event_id: str
    conflict_event_id: str
    sequence: int
    session_id: str
    player_id: str
    module: str
    window_id: int
    sample_id: int
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
                # module 내부에서 독립 범위를 갖는 상태를 별도로 보존한다.
                # - external_access: submodule별
                # - localguard_yara: PID+scope별
                #
                # 기존 테이블을 변경하지 않고 submodule 열을 범위 키로 재사용하는
                # additive 확장이므로 storage version은 유지한다.
                db.execute(
                    """CREATE TABLE IF NOT EXISTS scoped_latest_state (
                        session_id TEXT NOT NULL,
                        player_id TEXT NOT NULL,
                        module TEXT NOT NULL,
                        submodule TEXT NOT NULL,
                        timestamp_ms INTEGER NOT NULL,
                        sequence INTEGER NOT NULL,
                        event_id TEXT NOT NULL,
                        raw_score REAL NOT NULL,
                        evidence_json TEXT NOT NULL,
                        reasons_json TEXT NOT NULL,
                        PRIMARY KEY(session_id, player_id, module, submodule),
                        FOREIGN KEY(event_id) REFERENCES processed_events(event_id)
                    )"""
                )
                db.execute(
                    """CREATE INDEX IF NOT EXISTS ix_scoped_player_module
                       ON scoped_latest_state(
                           session_id, player_id, module, submodule
                       )"""
                )

                # whistle_rpc는 약 30초 단위의 새 로그 구간 관측이다.
                # 최신 snapshot과 별도로 NORMAL/양수/ERROR/OFFLINE window를 보존한다.
                db.execute(
                    """CREATE TABLE IF NOT EXISTS whistle_window_history (
                        event_id TEXT PRIMARY KEY,
                        sequence INTEGER NOT NULL UNIQUE,
                        session_id TEXT NOT NULL,
                        player_id TEXT NOT NULL,
                        module TEXT NOT NULL,
                        window_id INTEGER,
                        sample_id INTEGER,
                        semantic_key_valid INTEGER NOT NULL
                            CHECK(semantic_key_valid IN (0, 1)),
                        semantic_digest TEXT NOT NULL,
                        timestamp_ms INTEGER NOT NULL,
                        raw_score REAL NOT NULL,
                        evidence_json TEXT NOT NULL,
                        reasons_json TEXT NOT NULL,
                        FOREIGN KEY(event_id)
                            REFERENCES processed_events(event_id)
                    )"""
                )

                # 의미 중복 키는 sender 계약을 만족하는 행에만 적용한다.
                # 식별값이 없거나 잘못된 과거/비정상 Event는 증거를 버리지 않고
                # event_id 기준으로만 history에 남긴다.
                db.execute(
                    """CREATE UNIQUE INDEX IF NOT EXISTS
                       ux_whistle_rpc_semantic_window
                       ON whistle_window_history(
                           session_id,
                           player_id,
                           module,
                           window_id,
                           sample_id
                       )
                       WHERE semantic_key_valid = 1"""
                )

                db.execute(
                    """CREATE INDEX IF NOT EXISTS
                       ix_whistle_window_player_sequence
                       ON whistle_window_history(
                           session_id,
                           player_id,
                           sequence
                       )"""
                )

                # 동일 window/sample에 서로 다른 관측 내용이 도착한 경우
                # 정상 history를 덮어쓰거나 서버 전체를 실패시키지 않고 별도 감사 기록으로 보존한다.
                db.execute(
                    """CREATE TABLE IF NOT EXISTS whistle_window_conflicts (
                        conflict_event_id TEXT PRIMARY KEY,
                        canonical_event_id TEXT NOT NULL,
                        sequence INTEGER NOT NULL UNIQUE,
                        session_id TEXT NOT NULL,
                        player_id TEXT NOT NULL,
                        module TEXT NOT NULL,
                        window_id INTEGER NOT NULL,
                        sample_id INTEGER NOT NULL,
                        canonical_semantic_digest TEXT NOT NULL,
                        conflict_semantic_digest TEXT NOT NULL,
                        timestamp_ms INTEGER NOT NULL,
                        raw_score REAL NOT NULL,
                        evidence_json TEXT NOT NULL,
                        reasons_json TEXT NOT NULL,
                        FOREIGN KEY(conflict_event_id)
                            REFERENCES processed_events(event_id),
                        FOREIGN KEY(canonical_event_id)
                            REFERENCES processed_events(event_id)
                    )"""
                )

                db.execute(
                    """CREATE INDEX IF NOT EXISTS
                       ix_whistle_conflict_player_sequence
                       ON whistle_window_conflicts(
                           session_id,
                           player_id,
                           sequence
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

    @staticmethod
    def _external_access_submodule(event: Mapping[str, Any]) -> str | None:
        """현재 지원하는 external_access 하위 채널을 안전하게 식별한다."""

        evidence = event["evidence"]
        submodule = evidence.get("submodule")

        # 과거 external_process Event 중 명시적 submodule 없이
        # source_pid만 있던 형식은 A 정책과 동일하게 읽기 호환한다.
        if submodule is None and "source_pid" in evidence:
            return "external_process"

        if submodule in EXTERNAL_ACCESS_SUBMODULES:
            return submodule

        return None

    @staticmethod
    def _update_external_access_scoped(
        db: sqlite3.Connection,
        event: dict[str, Any],
        event_id: str,
        sequence: int,
        submodule: str,
    ) -> bool:
        """external_access 하위 채널 하나의 최신 상태를 갱신한다."""

        key = (
            event["session_id"],
            event["player_id"],
            event["module"],
            submodule,
        )

        current = db.execute(
            """SELECT timestamp_ms, sequence
               FROM scoped_latest_state
               WHERE session_id=? AND player_id=?
                 AND module=? AND submodule=?""",
            key,
        ).fetchone()

        should_update = (
            current is None
            or event["timestamp_ms"] > current["timestamp_ms"]
            or (
                event["timestamp_ms"] == current["timestamp_ms"]
                and sequence > current["sequence"]
            )
        )

        if not should_update:
            return False

        db.execute(
            """INSERT INTO scoped_latest_state(
                   session_id, player_id, module, submodule,
                   timestamp_ms, sequence, event_id, raw_score,
                   evidence_json, reasons_json
               ) VALUES(?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(session_id, player_id, module, submodule)
               DO UPDATE SET
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
                submodule,
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

        return True

    @staticmethod
    def _rebuild_external_access_aggregate(
        db: sqlite3.Connection,
        session_id: str,
        player_id: str,
    ) -> None:
        """두 scoped state에서 external_access 대표 현재 상태를 파생한다.

        합산하지 않고 scoped raw_score의 최댓값만 보존한다.
        NORMAL은 두 채널 모두 최신 유효 NORMAL 0일 때만 만든다.
        한 채널 누락/ERROR/OFFLINE은 WARNING + partial로 표현한다.
        """

        rows = db.execute(
            """SELECT *
               FROM scoped_latest_state
               WHERE session_id=? AND player_id=?
                 AND module='external_access'""",
            (session_id, player_id),
        ).fetchall()

        by_submodule = {
            row["submodule"]: row
            for row in rows
            if row["submodule"] in EXTERNAL_ACCESS_SUBMODULES
        }

        missing = [
            name
            for name in EXTERNAL_ACCESS_SUBMODULES
            if name not in by_submodule
        ]

        unavailable: list[str] = []
        scoped_summary: dict[str, Any] = {}
        usable_positive = False

        for name in EXTERNAL_ACCESS_SUBMODULES:
            row = by_submodule.get(name)
            if row is None:
                continue

            evidence = json.loads(row["evidence_json"])
            status = evidence.get("status")
            measurement_unavailable = (
                status in ("ERROR", "OFFLINE")
                or evidence.get("measurement_valid") is False
            )

            if measurement_unavailable:
                unavailable.append(name)

            raw_score = float(row["raw_score"])

            if raw_score > 0 and not measurement_unavailable:
                usable_positive = True

            scoped_summary[name] = {
                "raw_score": raw_score,
                "status": status,
                "timestamp_ms": row["timestamp_ms"],
                "sequence": row["sequence"],
                "event_id": row["event_id"],
            }

        if not rows:
            return

        coverage_complete = not missing and not unavailable

        all_normal_zero = (
            coverage_complete
            and all(
                float(by_submodule[name]["raw_score"]) == 0
                and json.loads(
                    by_submodule[name]["evidence_json"]
                ).get("status") == "NORMAL"
                for name in EXTERNAL_ACCESS_SUBMODULES
            )
        )

        if usable_positive:
            aggregate_status = "SUSPICIOUS"
        elif all_normal_zero:
            aggregate_status = "NORMAL"
        else:
            aggregate_status = "WARNING"

        aggregate_raw = max(
            float(row["raw_score"])
            for row in rows
        )

        # timestamp는 현재 scoped 상태 중 가장 최신 게임 시각,
        # sequence/event_id는 현재 뷰를 구성하는 가장 뒤 서버 기록을 사용한다.
        aggregate_timestamp = max(
            row["timestamp_ms"]
            for row in rows
        )
        latest_sequence_row = max(
            rows,
            key=lambda row: row["sequence"],
        )
        aggregate_sequence = latest_sequence_row["sequence"]
        aggregate_event_id = latest_sequence_row["event_id"]

        aggregate_evidence = {
            "submodule": "aggregate",
            "status": aggregate_status,
            "coverage_complete": coverage_complete,
            "missing_submodules": missing,
            "unavailable_submodules": unavailable,
            "scoped_submodules": scoped_summary,
            "derived": True,
        }

        # 실제 detector Event를 복사하는 것이 아니라 서버 내부 파생 상태다.
        # scoped 상태가 바뀔 때마다 동일 module-level row를 다시 계산한다.
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
                session_id,
                player_id,
                "external_access",
                aggregate_timestamp,
                aggregate_sequence,
                aggregate_event_id,
                aggregate_raw,
                json.dumps(
                    aggregate_evidence,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                "[]",
            ),
        )

    @staticmethod
    def _yara_scope_key(event: Mapping[str, Any]) -> str | None:
        """localguard_yara의 PID+scope 범위를 안정적인 저장 키로 만든다.

        양수 결과는 유효 PID/scope가 있으면 저장한다.
        NORMAL 0은 measurement_valid=true일 때만 해당 범위의 정상 상태로 갱신한다.
        ERROR/OFFLINE/measurement_valid=false는 정상 0으로 사용하지 않는다.
        """

        if event["module"] != "localguard_yara":
            return None

        evidence = event["evidence"]
        pid = evidence.get("pid")
        scope = evidence.get("scope")

        if (
            type(pid) is not int
            or not 0 < pid <= 0xFFFFFFFF
            or not isinstance(scope, str)
            or scope not in YARA_SCOPES
        ):
            return None

        if (
            evidence.get("status") in ("ERROR", "OFFLINE")
            or evidence.get("measurement_valid") is False
        ):
            return None

        # 정상 0점은 실제 측정 성공이 명시된 경우에만
        # 기존 동일 PID/scope 상태를 정상으로 갱신한다.
        if event["raw_score"] == 0 and evidence.get("measurement_valid") is not True:
            return None

        return f"yara_pid:{pid}:{scope}"

    @staticmethod
    def _update_yara_scoped(
        db: sqlite3.Connection,
        event: dict[str, Any],
        event_id: str,
        sequence: int,
        scope_key: str,
    ) -> bool:
        """YARA PID+scope 하나의 최신 상태를 갱신한다."""

        key = (
            event["session_id"],
            event["player_id"],
            event["module"],
            scope_key,
        )

        current = db.execute(
            """SELECT timestamp_ms, sequence
               FROM scoped_latest_state
               WHERE session_id=? AND player_id=?
                 AND module=? AND submodule=?""",
            key,
        ).fetchone()

        should_update = (
            current is None
            or event["timestamp_ms"] > current["timestamp_ms"]
            or (
                event["timestamp_ms"] == current["timestamp_ms"]
                and sequence > current["sequence"]
            )
        )

        if not should_update:
            return False

        db.execute(
            """INSERT INTO scoped_latest_state(
                   session_id, player_id, module, submodule,
                   timestamp_ms, sequence, event_id, raw_score,
                   evidence_json, reasons_json
               ) VALUES(?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(session_id, player_id, module, submodule)
               DO UPDATE SET
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
                scope_key,
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

        return True

    @staticmethod
    def _rebuild_yara_aggregate(
        db: sqlite3.Connection,
        session_id: str,
        player_id: str,
    ) -> None:
        """YARA의 PID/scope별 현재 상태에서 module 대표 상태를 파생한다.

        서로 다른 대상의 점수를 합산하지 않는다.

        현재 양수 대상이 하나라도 있으면 그중 가장 높은 raw_score를 대표 상태로
        유지한다. 따라서 다른 PID/scope의 NORMAL 0이 기존 양수를 지우지 않는다.

        모든 현재 대상이 0이면 가장 최근 정상 표본을 대표 상태로 사용한다.
        """

        rows = db.execute(
            """SELECT *
               FROM scoped_latest_state
               WHERE session_id=? AND player_id=?
                 AND module='localguard_yara'""",
            (session_id, player_id),
        ).fetchall()

        if not rows:
            return

        # raw_score를 우선하여 양수 범위가 unrelated NORMAL 0에 의해
        # 사라지지 않도록 한다. 같은 점수라면 최신 게임 시각/sequence를 쓴다.
        representative = max(
            rows,
            key=lambda row: (
                float(row["raw_score"]),
                row["timestamp_ms"],
                row["sequence"],
            ),
        )

        db.execute(
            """INSERT INTO latest_state(
                   session_id, player_id, module,
                   timestamp_ms, sequence, event_id,
                   raw_score, evidence_json, reasons_json
               ) VALUES(?,?,?,?,?,?,?,?,?)
               ON CONFLICT(session_id, player_id, module)
               DO UPDATE SET
                   timestamp_ms=excluded.timestamp_ms,
                   sequence=excluded.sequence,
                   event_id=excluded.event_id,
                   raw_score=excluded.raw_score,
                   evidence_json=excluded.evidence_json,
                   reasons_json=excluded.reasons_json""",
            (
                session_id,
                player_id,
                "localguard_yara",
                representative["timestamp_ms"],
                representative["sequence"],
                representative["event_id"],
                float(representative["raw_score"]),
                representative["evidence_json"],
                representative["reasons_json"],
            ),
        )

    @staticmethod
    def _whistle_rpc_identity(
        event: Mapping[str, Any],
    ) -> tuple[int | None, int | None, bool]:
        """Shared evidence에서 현재 whistle_rpc 의미 식별자를 읽는다.

        현 sender 계약:
        - window_id: 0부터 증가하는 비음수 정수
        - whistle_rpc sample_id: 일반 검사 1, PR #86의 RPC 전용 마지막 검사 0

        bool은 int의 하위 타입이므로 type(value) is int로 엄격히 검사한다.
        """

        evidence = event["evidence"]

        window_id = evidence.get("window_id")
        sample_id = evidence.get("sample_id")

        valid = (
            type(window_id) is int
            and window_id >= 0
            and type(sample_id) is int
            and sample_id in (0, 1)
        )

        if not valid:
            return None, None, False

        return window_id, sample_id, True

    @staticmethod
    def _whistle_rpc_semantic_digest(
        event: Mapping[str, Any],
    ) -> str:
        """같은 window 관측의 의미 내용 비교용 digest.

        timestamp_ms는 Event 생성 시각이므로 의미 중복 비교에서는 제외한다.
        점수, 이유, evidence 및 식별 범위는 그대로 비교한다.
        """

        semantic_payload = {
            "session_id": event["session_id"],
            "player_id": event["player_id"],
            "module": event["module"],
            "evidence": event["evidence"],
            "reasons": event["reasons"],
            "raw_score": event["raw_score"],
        }

        encoded = json.dumps(
            semantic_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")

        return hashlib.sha256(encoded).hexdigest()

    @classmethod
    def _remember_whistle_window(
        cls,
        db: sqlite3.Connection,
        event: dict[str, Any],
        event_id: str,
        sequence: int,
    ) -> bool:
        """whistle_rpc window를 history에 보존한다.

        반환값:
        - True: 새로운 window history가 실제 삽입됨
        - False: 이미 같은 event_id이거나 같은 의미 window/동일 본문임

        같은 의미 window인데 본문이 다르면 임의 선택하지 않고 실패시켜
        process_event() 전체 transaction을 rollback한다.
        """

        if event["module"] != "whistle_rpc":
            return False

        semantic_digest = cls._whistle_rpc_semantic_digest(event)

        # 동일 event_id history가 이미 있으면 재삽입하지 않는다.
        existing_event = db.execute(
            """SELECT semantic_digest
               FROM whistle_window_history
               WHERE event_id=?""",
            (event_id,),
        ).fetchone()

        if existing_event is not None:
            if existing_event["semantic_digest"] != semantic_digest:
                raise RuntimeError(
                    "whistle_rpc event_id history digest conflict"
                )
            return False

        window_id, sample_id, semantic_key_valid = (
            cls._whistle_rpc_identity(event)
        )

        # 의미 식별자가 정상일 때만 별도 event_id 간 의미 중복을 검사한다.
        if semantic_key_valid:
            existing_window = db.execute(
                """SELECT event_id, semantic_digest
                   FROM whistle_window_history
                   WHERE session_id=?
                     AND player_id=?
                     AND module='whistle_rpc'
                     AND window_id=?
                     AND sample_id=?
                     AND semantic_key_valid=1""",
                (
                    event["session_id"],
                    event["player_id"],
                    window_id,
                    sample_id,
                ),
            ).fetchone()

            if existing_window is not None:
                if existing_window["semantic_digest"] != semantic_digest:
                    db.execute(
                        """INSERT INTO whistle_window_conflicts(
                               conflict_event_id,
                               canonical_event_id,
                               sequence,
                               session_id,
                               player_id,
                               module,
                               window_id,
                               sample_id,
                               canonical_semantic_digest,
                               conflict_semantic_digest,
                               timestamp_ms,
                               raw_score,
                               evidence_json,
                               reasons_json
                           ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                           ON CONFLICT(conflict_event_id) DO NOTHING""",
                        (
                            event_id,
                            existing_window["event_id"],
                            sequence,
                            event["session_id"],
                            event["player_id"],
                            event["module"],
                            window_id,
                            sample_id,
                            existing_window["semantic_digest"],
                            semantic_digest,
                            event["timestamp_ms"],
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

                    # 최초 정상 history/latest를 유지하고 충돌 Event는 감사 기록에만 보존한다.
                    return False

                # 새 event_id여도 동일한 window의 동일 관측이면
                # history/latest에는 다시 반영하지 않는다.
                return False

        db.execute(
            """INSERT INTO whistle_window_history(
                   event_id,
                   sequence,
                   session_id,
                   player_id,
                   module,
                   window_id,
                   sample_id,
                   semantic_key_valid,
                   semantic_digest,
                   timestamp_ms,
                   raw_score,
                   evidence_json,
                   reasons_json
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                event_id,
                sequence,
                event["session_id"],
                event["player_id"],
                event["module"],
                window_id,
                sample_id,
                1 if semantic_key_valid else 0,
                semantic_digest,
                event["timestamp_ms"],
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

        return True

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
                    # 기존 DB에서 이미 processed 처리됐지만 새 history table에는
                    # 없을 수 있으므로 동일 원본 재처리 시 안전하게 보완한다.
                    self._remember_whistle_window(
                        db,
                        event,
                        event_id,
                        sequence,
                    )
                    self._remember_delta_event(db, event, event_id, sequence)

                    # YARA scoped 저장이 추가되기 전에 처리된 기존 DB도
                    # Shared recovery/retry 시 정확한 원본 이벤트로 안전하게 보완한다.
                    if event["module"] == "localguard_yara":
                        scope_key = self._yara_scope_key(event)
                        if scope_key is not None:
                            yara_updated = self._update_yara_scoped(
                                db,
                                event,
                                event_id,
                                sequence,
                                scope_key,
                            )
                            if yara_updated:
                                self._rebuild_yara_aggregate(
                                    db,
                                    event["session_id"],
                                    event["player_id"],
                                )

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

                # whistle_rpc는 latest_state 갱신 전에 window history/의미 중복을
                # 먼저 판정한다. 충돌이면 processed_events INSERT까지 rollback된다.
                whistle_window_inserted = True

                if event["module"] == "whistle_rpc":
                    whistle_window_inserted = self._remember_whistle_window(
                        db,
                        event,
                        event_id,
                        sequence,
                    )

                # external_access는 두 submodule을 module 하나로 직접 덮어쓰지 않는다.
                if event["module"] == "external_access":
                    submodule = self._external_access_submodule(event)
                    should_update = False

                    if submodule is not None:
                        should_update = self._update_external_access_scoped(
                            db,
                            event,
                            event_id,
                            sequence,
                            submodule,
                        )

                        if should_update:
                            self._rebuild_external_access_aggregate(
                                db,
                                event["session_id"],
                                event["player_id"],
                            )

                elif event["module"] == "localguard_yara":
                    scope_key = self._yara_scope_key(event)
                    should_update = False

                    if scope_key is not None:
                        should_update = self._update_yara_scoped(
                            db,
                            event,
                            event_id,
                            sequence,
                            scope_key,
                        )

                        if should_update:
                            self._rebuild_yara_aggregate(
                                db,
                                event["session_id"],
                                event["player_id"],
                            )

                elif (
                    event["module"] == "whistle_rpc"
                    and not whistle_window_inserted
                ):
                    # event_id는 새롭지만 이미 저장된 동일 의미 window/동일 본문이다.
                    # 수신 처리 이력은 남기되 window history와 latest_state를
                    # 다시 반영하지 않는다.
                    should_update = False

                else:
                    # 일반 모듈은 기존 module-level latest_state 계약을 그대로 사용한다.
                    key = (
                        event["session_id"],
                        event["player_id"],
                        event["module"],
                    )

                    current = db.execute(
                        """SELECT timestamp_ms, sequence
                           FROM latest_state
                           WHERE session_id=? AND player_id=? AND module=?""",
                        key,
                    ).fetchone()

                    # game elapsed timestamp가 우선이다.
                    # timestamp가 같으면 서버 저장 sequence로 결정한다.
                    should_update = (
                        current is None
                        or event["timestamp_ms"] > current["timestamp_ms"]
                        or (
                            event["timestamp_ms"] == current["timestamp_ms"]
                            and sequence > current["sequence"]
                        )
                    )

                    if should_update:
                        db.execute(
                            """INSERT INTO latest_state(
                                   session_id, player_id, module,
                                   timestamp_ms, sequence, event_id,
                                   raw_score, evidence_json, reasons_json
                               ) VALUES(?,?,?,?,?,?,?,?,?)
                               ON CONFLICT(session_id, player_id, module)
                               DO UPDATE SET
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

    def get_window_conflicts(
        self,
        session_id: str,
        player_id: str,
        *,
        after_sequence: int = 0,
        limit: int = 100,
    ) -> list[WindowConflict]:
        """whistle_rpc 동일 window의 상충 관측을 감사 목적으로 조회한다."""

        validate_identifier(session_id)
        validate_identifier(player_id)

        if type(after_sequence) is not int or after_sequence < 0:
            raise ValueError(
                "after_sequence must be a nonnegative integer"
            )

        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError(
                "limit must be between 1 and 1000"
            )

        try:
            with closing(self._connect()) as db:
                rows = db.execute(
                    """SELECT *
                       FROM whistle_window_conflicts
                       WHERE session_id=?
                         AND player_id=?
                         AND module='whistle_rpc'
                         AND sequence>?
                       ORDER BY sequence
                       LIMIT ?""",
                    (
                        session_id,
                        player_id,
                        after_sequence,
                        limit,
                    ),
                ).fetchall()
        except sqlite3.Error as exc:
            raise RuntimeError(
                "cannot read whistle window conflicts"
            ) from exc

        return [
            WindowConflict(
                canonical_event_id=row["canonical_event_id"],
                conflict_event_id=row["conflict_event_id"],
                sequence=row["sequence"],
                session_id=row["session_id"],
                player_id=row["player_id"],
                module=row["module"],
                window_id=row["window_id"],
                sample_id=row["sample_id"],
                timestamp_ms=row["timestamp_ms"],
                raw_score=row["raw_score"],
                evidence=json.loads(row["evidence_json"]),
                reasons=json.loads(row["reasons_json"]),
            )
            for row in rows
        ]

    def get_window_history(
        self,
        session_id: str,
        player_id: str,
        *,
        after_sequence: int = 0,
        limit: int = 100,
    ) -> list[WindowEvent]:
        """whistle_rpc window 이력을 서버 저장 sequence 순으로 조회한다.

        여기서는 TTL, 합산 점수, 최종 risk를 계산하지 않는다.
        NORMAL/양수/ERROR/OFFLINE 원본 의미를 그대로 반환한다.
        """

        validate_identifier(session_id)
        validate_identifier(player_id)

        if type(after_sequence) is not int or after_sequence < 0:
            raise ValueError(
                "after_sequence must be a nonnegative integer"
            )

        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError(
                "limit must be between 1 and 1000"
            )

        try:
            with closing(self._connect()) as db:
                rows = db.execute(
                    """SELECT *
                       FROM whistle_window_history
                       WHERE session_id=?
                         AND player_id=?
                         AND module='whistle_rpc'
                         AND sequence>?
                       ORDER BY sequence
                       LIMIT ?""",
                    (
                        session_id,
                        player_id,
                        after_sequence,
                        limit,
                    ),
                ).fetchall()
        except sqlite3.Error as exc:
            raise RuntimeError(
                "cannot read whistle window history"
            ) from exc

        return [
            WindowEvent(
                event_id=row["event_id"],
                sequence=row["sequence"],
                session_id=row["session_id"],
                player_id=row["player_id"],
                module=row["module"],
                window_id=row["window_id"],
                sample_id=row["sample_id"],
                semantic_key_valid=bool(
                    row["semantic_key_valid"]
                ),
                timestamp_ms=row["timestamp_ms"],
                raw_score=row["raw_score"],
                evidence=json.loads(row["evidence_json"]),
                reasons=json.loads(row["reasons_json"]),
            )
            for row in rows
        ]

    def get_scoped_module_state(
        self,
        session_id: str,
        player_id: str,
        module: str,
        submodule: str,
    ) -> ModuleState | None:
        """하위 채널별 최신 저장 기록을 조회한다."""

        try:
            with closing(self._connect()) as db:
                row = db.execute(
                    """SELECT *
                       FROM scoped_latest_state
                       WHERE session_id=? AND player_id=?
                         AND module=? AND submodule=?""",
                    (
                        session_id,
                        player_id,
                        module,
                        submodule,
                    ),
                ).fetchone()
        except sqlite3.Error as exc:
            raise RuntimeError(
                "cannot read scoped scoring state"
            ) from exc

        return self._row_to_state(row) if row is not None else None

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
