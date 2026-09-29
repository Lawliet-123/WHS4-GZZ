"""Cooperating-process locks on local disks. Not a network filesystem lock service."""
from __future__ import annotations

import os
import time
from pathlib import Path

from .errors import ResourceBusyError


class FileLock:
    def __init__(self, path: Path, *, timeout: float = 0):
        self.path, self.timeout = path, timeout
        self._file = None

    def acquire(self) -> FileLock:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        stream = self.path.open("a+b")
        if os.fstat(stream.fileno()).st_size == 0:
            stream.write(b"\0")
            stream.flush()
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                stream.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                self._file = stream
                return self
            except OSError:
                if time.monotonic() >= deadline:
                    stream.close()
                    raise ResourceBusyError("telemetry resource is already in use") from None
                time.sleep(min(0.02, max(0, deadline - time.monotonic())))

    def release(self) -> None:
        if self._file is None:
            return
        try:
            self._file.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self._file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._file.fileno(), fcntl.LOCK_UN)
        finally:
            self._file.close()
            self._file = None

    def __enter__(self):
        return self.acquire()

    def __exit__(self, *_):
        self.release()
