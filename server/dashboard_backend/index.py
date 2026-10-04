"""Rebuildable read index fed exclusively by Shared's public durable feed."""

from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import closing
from pathlib import Path


class DashboardIndex:
    def __init__(self, path: str | Path, writer, *, sync_budget: int = 2000):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.writer = writer
        self.sync_budget = sync_budget
        self.lock = threading.Lock()
        with closing(self.connect()) as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                INSERT OR IGNORE INTO metadata VALUES ('cursor', '0');
                CREATE TABLE IF NOT EXISTS events (
                    sequence INTEGER PRIMARY KEY, id TEXT UNIQUE NOT NULL,
                    session_id TEXT NOT NULL, player_id TEXT NOT NULL,
                    module TEXT NOT NULL, submodule TEXT,
                    timestamp_ms INTEGER NOT NULL, payload TEXT NOT NULL,
                    kind TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS event_scope ON events(session_id, player_id, sequence);
                CREATE INDEX IF NOT EXISTS event_module ON events(module, sequence);
            """)
            # Reusing an index against another writer would silently mix sessions.
            source = str(writer.root.resolve())
            old = db.execute("SELECT value FROM metadata WHERE key='source'").fetchone()
            if old and old[0] != source:
                raise ValueError("Dashboard index belongs to a different detection writer")
            db.execute("INSERT OR IGNORE INTO metadata VALUES ('source', ?)", (source,))
            db.commit()

    def connect(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        return db

    def sync(self) -> dict:
        """Bound each refresh; cursor and mirrored rows commit together."""
        with self.lock, closing(self.connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            cursor = int(db.execute("SELECT value FROM metadata WHERE key='cursor'").fetchone()[0])
            remaining = self.sync_budget
            complete = False
            while remaining:
                batch = self.writer.iter_stored(after_sequence=cursor, limit=min(remaining, 500))
                if not batch:
                    complete = True
                    break
                for item in batch:
                    event = item.result
                    submodule = event["evidence"].get("submodule")
                    kind = "operational" if event["module"] == "selfdefense" or event["evidence"].get("kind") == "module_health" else "detection"
                    db.execute("INSERT INTO events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", (
                        item.sequence, item.event_id, event["session_id"], event["player_id"],
                        event["module"], submodule if isinstance(submodule, str) else None,
                        event["timestamp_ms"], json.dumps(event, ensure_ascii=False, allow_nan=False), kind,
                    ))
                    cursor = item.sequence
                remaining -= len(batch)
                db.execute("UPDATE metadata SET value=? WHERE key='cursor'", (str(cursor),))
                if len(batch) < min(remaining + len(batch), 500):
                    complete = True
                    break
            db.commit()
            return {"through_sequence": cursor, "catching_up": not complete}

    @staticmethod
    def event(row) -> dict:
        event = json.loads(row["payload"])
        return {
            **event, "id": row["id"], "sequence": row["sequence"],
            "event_kind": row["kind"], "time_basis": "unknown",
            "evidence_image": None, "log_excerpt": None,
        }

    def detail(self, event_id: str):
        with closing(self.connect()) as db:
            row = db.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
        return self.event(row) if row else None

    def events(self, filters: dict, *, after: int, through: int, limit: int):
        clauses, values = ["sequence > ?", "sequence <= ?"], [after, through]
        for key in ("session_id", "player_id", "module", "submodule"):
            if filters.get(key):
                clauses.append(f"{key} = ?")
                values.append(filters[key])
        if filters.get("q"):
            query = filters["q"].replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            clauses.append("(id LIKE ? ESCAPE '\\' OR payload LIKE ? ESCAPE '\\')")
            values.extend([f"%{query}%"] * 2)
        with closing(self.connect()) as db:
            rows = db.execute("SELECT * FROM events WHERE " + " AND ".join(clauses) + " ORDER BY sequence LIMIT ?", (*values, limit + 1)).fetchall()
        return [self.event(row) for row in rows[:limit]], len(rows) > limit

    def overview(self, *, after_session: str = "", limit: int = 100, heartbeat_sessions=None):
        heartbeat_sessions = {item["id"]: item["player_ids"] for item in (heartbeat_sessions or [])}
        with closing(self.connect()) as db:
            counts = db.execute("SELECT COUNT(*) total, COUNT(DISTINCT session_id) sessions, COUNT(DISTINCT player_id) players, SUM(kind='operational') operational FROM events").fetchone()
            ids = [row[0] for row in db.execute("SELECT DISTINCT session_id FROM events WHERE session_id > ? ORDER BY session_id LIMIT ?", (after_session, limit + 1))]
            ids = sorted(set(ids) | set(heartbeat_sessions))
            selected = ids[:limit]
            sessions, players, assessments = [], {}, []
            for session in selected:
                rows = db.execute("SELECT player_id, module, MAX(timestamp_ms) max_time FROM events WHERE session_id=? GROUP BY player_id, module", (session,)).fetchall()
                player_ids = sorted({row["player_id"] for row in rows} | set(heartbeat_sessions.get(session, [])))
                sessions.append({"id": session, "player_ids": player_ids, "module_ids": sorted({row["module"] for row in rows}), "duration_ms": None, "max_observed_timestamp_ms": max((row["max_time"] for row in rows), default=None), "status": "UNKNOWN", "score": None})
                for player in player_ids:
                    players.setdefault(player, {"id": player, "display_name": player, "identity_type": "client_supplied", "session_ids": [], "status": "UNKNOWN", "max_score": None})["session_ids"].append(session)
                    assessments.append({"id": f"{session}:{player}", "session_id": session, "player_id": player, "status": "UNKNOWN", "assessment_available": False, "score": None, "confidence": None, "module_scores": [], "reasons": []})
        return {
            "counts": {"scope": "indexed_events", "events": counts["total"], "sessions": counts["sessions"], "players": counts["players"], "operational_events": counts["operational"] or 0, "review": None, "high": None},
            "sessions": sessions, "players": list(players.values()), "assessments": assessments,
            "session_page": {"next_after_session": selected[-1] if len(ids) > limit else None, "has_more": len(ids) > limit},
        }
