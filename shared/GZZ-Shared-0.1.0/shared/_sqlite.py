"""Short-lived connections with explicit transactions, also on Python 3.10."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path

from .errors import StorageError


@contextmanager
def database(path: Path):
    connection = None
    try:
        connection = sqlite3.connect(path, timeout=1.0, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute("BEGIN IMMEDIATE")
        yield connection
        connection.commit()
    except sqlite3.Error as exc:
        if connection is not None and connection.in_transaction:
            connection.rollback()
        raise StorageError("telemetry database operation failed") from exc
    except BaseException:
        if connection is not None and connection.in_transaction:
            connection.rollback()
        raise
    finally:
        if connection is not None:
            connection.close()
