"""Read-only scan: mismatches, missing files and unavailable checks stay distinct."""
from __future__ import annotations

from dataclasses import dataclass, field
import stat

if __package__:
    from .baseline import IntegrityError, error_code, fingerprint, load_baseline, _reject_link, discover_files, MAX_TOTAL_BYTES
else:
    from baseline import IntegrityError, error_code, fingerprint, load_baseline, _reject_link, discover_files, MAX_TOTAL_BYTES


@dataclass
class Scan:
    release_id: str | None = None
    files: list[dict] = field(default_factory=list)
    error: str | None = None
    expected_count: int | None = None

    def summary(self):
        counts = {s: sum(x["state"] == s for x in self.files)
                  for s in ("MATCH", "MODIFIED", "MISSING", "UNEXPECTED", "ERROR")}
        changed = counts["MODIFIED"] + counts["MISSING"] + counts["UNEXPECTED"]
        failed = self.error is not None or counts["ERROR"] > 0
        status = "DETECTED" if changed else "ERROR" if failed else "NORMAL"
        return {"status": status, "integrity_state": "MISMATCH" if changed else "ERROR" if failed else "MATCH",
                "release_id": self.release_id, "scan_complete": not failed,
                "expected_files": self.expected_count,
                "matched_files": counts["MATCH"], "modified_files": counts["MODIFIED"],
                "missing_files": counts["MISSING"], "unexpected_files": counts["UNEXPECTED"], "error_files": counts["ERROR"],
                "error_code": self.error}

    def reasons(self):
        return sorted({x["code"] for x in self.files if x["code"]} | ({self.error} if self.error else set()))


def scan(root, baseline_path, pin, *, stop=None):
    result = Scan()
    try:
        manifest = load_baseline(baseline_path, pin)
        result.release_id = manifest["release_id"]
        result.expected_count = len(manifest["files"])
        info = root.lstat()
        _reject_link(info)
        if not stat.S_ISDIR(info.st_mode):
            raise IntegrityError("ROOT_NOT_DIRECTORY")
    except (OSError, IntegrityError) as exc:
        result.error = error_code(exc) if isinstance(exc, IntegrityError) else "ROOT_UNAVAILABLE"
        return result
    remaining = MAX_TOTAL_BYTES
    for expected in manifest["files"]:
        if stop is not None and stop.is_set():
            result.error = "SCAN_CANCELLED"
            break
        item = {"path": expected["path"], "expected_sha256": expected["sha256"],
                "expected_size": expected["size"], "actual_sha256": None, "actual_size": None,
                "state": "ERROR", "code": None}
        try:
            actual = fingerprint(root, expected["path"], max_bytes=remaining)
            remaining -= actual["size"]
            item.update(actual_sha256=actual["sha256"], actual_size=actual["size"])
            matches = actual["sha256"] == expected["sha256"] and actual["size"] == expected["size"]
            item.update(state="MATCH" if matches else "MODIFIED", code=None if matches else "FILE_CONTENT_MISMATCH")
        except (OSError, IntegrityError) as exc:
            code = error_code(exc)
            item.update(state="MISSING" if code == "FILE_MISSING" else "ERROR", code=code)
        result.files.append(item)
    if result.error is None:
        try:
            expected_names = {e["path"].casefold() for e in manifest["files"]}
            for name in discover_files(root, manifest["scope_dirs"]):
                if name.casefold() not in expected_names:
                    result.files.append({"path": name, "state": "UNEXPECTED", "code": "UNEXPECTED_CODE_FILE"})
        except (OSError, IntegrityError) as exc:
            result.error = error_code(exc)
    return result
