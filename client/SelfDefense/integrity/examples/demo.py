"""Six local fixture cases. Creates a new directory; never modifies deployed files."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time

ENTRY = Path(__file__).resolve().parents[1] / "main.py"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--shared-root", type=Path, default=ENTRY.parent.parent.parent.parent)
    parser.add_argument("--output-dir", type=Path, help="Must not exist; omit for a new retained temp directory")
    args = parser.parse_args(argv)
    if args.output_dir:
        base = args.output_dir.resolve()
        base.mkdir(parents=True, exist_ok=False)
    else:
        base = Path(tempfile.mkdtemp(prefix="gzz-integrity-demo-"))
    root = base / "fixture-deployment"
    (root / "client/detectors/demo").mkdir(parents=True)
    (root / "shared").mkdir()
    target = root / "client/detectors/demo/main.py"
    original = b"# synthetic fixture, never executed\n"
    target.write_bytes(original)
    (root / "shared/config.py").write_bytes(b"# synthetic shared fixture\n")
    manifest = base / "fixture-baseline.json"
    build = subprocess.run([sys.executable, str(ENTRY.with_name("build_baseline.py")), "--root", str(root),
                            "--include-dir", "client", "--include-dir", "shared", "--release-id", "fixture_v1",
                            "--output", str(manifest)], capture_output=True, text=True, timeout=15)
    if build.returncode:
        raise RuntimeError(build.stderr)
    pin = json.loads(build.stdout)["baseline_sha256"]  # Fixture release build, not a runtime trust decision.
    baseline_bytes = manifest.read_bytes()
    t0 = time.time_ns() // 1_000_000
    results = []

    def run(name, expected_status, expected_code, reason=None):
        result = subprocess.run([sys.executable, str(ENTRY), "--shared-root", str(args.shared_root.resolve()),
                                 "--session-id", name, "--player-id", "fixture_player", "--root", str(root),
                                 "--baseline", str(manifest), "--baseline-sha256", pin, "--once", "--synthetic",
                                 "--telemetry", "off", "--session-start-unix-ms", str(t0),
                                 "--output-dir", str(base / "logs")],
                                capture_output=True, text=True, timeout=15)
        if result.returncode != expected_code:
            raise RuntimeError(f"{name}: unexpected exit {result.returncode}: {result.stderr}")
        event_file = next((base / "logs" / name).rglob("events.jsonl"))
        event = json.loads(event_file.read_text(encoding="utf-8"))
        if (event["evidence"]["status"] != expected_status or event["raw_score"] != 0
                or not event["evidence"]["synthetic"] or (reason and reason not in event["reasons"])):
            raise RuntimeError(name + ": unexpected event")
        results.append({"case": name, "status": expected_status, "exit_code": expected_code,
                        "reasons": event["reasons"], "events": event_file.relative_to(base).as_posix()})

    run("fixture_clean", "NORMAL", 0)
    target.write_bytes(b"# changed synthetic fixture\n")
    run("fixture_modified", "DETECTED", 1, "FILE_CONTENT_MISMATCH")
    target.unlink()  # Only the dummy file created above, never a user-selected source file.
    run("fixture_missing", "DETECTED", 1, "FILE_MISSING")
    target.write_bytes(original)
    extra = root / "client/unexpected.py"
    extra.write_bytes(b"# added synthetic fixture\n")
    run("fixture_added", "DETECTED", 1, "UNEXPECTED_CODE_FILE")
    extra.unlink()
    manifest.write_bytes(baseline_bytes + b" ")
    run("fixture_baseline_changed", "ERROR", 2, "BASELINE_PIN_MISMATCH")
    manifest.write_bytes(baseline_bytes)
    run("fixture_restored", "NORMAL", 0)
    summary = {"synthetic": True, "not_gameplay_data": True, "telemetry": "off", "cases": results}
    (base / "demo-summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(base), **summary}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
