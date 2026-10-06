"""Opt-in integration fixture: only starts finite, test-owned processes."""
import argparse
import json
import os
from pathlib import Path
import signal
import sys
import threading
import time
import uuid

from fixture_protocol import control_path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("worker", "orphan-owner"))
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--name", default="probe")
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    root = args.root.resolve()
    sys.path.insert(0, str(root / "client" / "Launcher"))
    import registry
    if args.mode == "worker":
        stop = threading.Event()
        reason = "duration"
        def request(signum, _frame):
            nonlocal reason
            reason = signal.Signals(signum).name
            stop.set()
        for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGBREAK):
            signal.signal(sig, request)
        record = root / "artifacts" / "workers" / args.name / (str(os.getpid()) + ".json")
        record.parent.mkdir(parents=True, exist_ok=True)
        control_id = uuid.uuid4().hex
        value = dict(pid=os.getpid(), create_time=registry.create_time(os.getpid()),
                     parent_pid=os.getppid(), parent_create_time=registry.create_time(os.getppid()),
                     control_id=control_id,
                     outbox=os.environ.get("GZZ_TELEMETRY_OUTBOX"), status="RUNNING")
        record.write_text(json.dumps(value), encoding="utf-8")
        code = 0
        if args.once:
            release = control_path(root, control_id)
            deadline = time.monotonic() + 180
            while not stop.wait(0.02):
                if release.exists():
                    try:
                        code = json.loads(release.read_text())["exit_code"]
                        break
                    except (ValueError, KeyError):
                        pass
                if time.monotonic() > deadline:
                    code = 3
                    break
        else:
            stop.wait(180)
        value.update(status="STOPPED", reason=reason)
        record.write_text(json.dumps(value), encoding="utf-8")
        return code

    # A disposable parent plays the Launcher owner, using the real registry unchanged.
    registry.begin_session("orphan_test")
    log = Path(registry.LOG_DIR)
    wd_args = [sys.executable, str(root / "client/SelfDefense/watchdog/main.py"),
               "--session-id", "orphan_test", "--player-id", "fixture_player", "--t0", str(time.time()),
               "--telemetry", "off", "--interval", "0.05", "--duration", "120",
               "--output-dir", str(root / "artifacts/orphan-watchdog")]
    worker_args = [sys.executable, str(Path(__file__).resolve()), "worker", "--root", str(root), "--name", "orphan_probe"]
    created = []
    for name, argv in (("self_defense", wd_args), ("orphan_probe", worker_args)):
        logfile = str(log / (name + ".log"))
        proc = registry.spawn(argv, str(root), logfile)
        created.append(proc)
        registry.register(name, proc, by="launcher", restartable=True, argv=argv, cwd=str(root), log=logfile)
    ready = root / "artifacts/orphan-ready.json"
    ready.write_text(json.dumps(registry.load()), encoding="utf-8")
    time.sleep(150)


if __name__ == "__main__":
    raise SystemExit(main())
