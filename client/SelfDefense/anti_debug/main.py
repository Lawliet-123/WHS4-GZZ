"""Read-only Windows debugger monitoring; no blocking, termination or restart."""
from __future__ import annotations

import argparse
from decimal import Decimal, InvalidOperation
import math
import os
from pathlib import Path
import signal
import sys
import threading
import time


def positive(value):
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError("must be finite and positive")
    return number


def unix_ms(value):
    try:
        number = Decimal(value)
        if not number.is_finite() or not 0 <= number <= Decimal(time.time_ns()) / 1_000_000_000:
            raise ValueError()
        return int(number * 1000)
    except (InvalidOperation, ValueError, OverflowError):
        raise argparse.ArgumentTypeError("--t0 must be past Unix seconds") from None


def main(argv=None):
    module_dir = Path(__file__).resolve().parent
    client_dir = module_dir.parent.parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--player-id", required=True)
    origin = parser.add_mutually_exclusive_group()
    origin.add_argument("--t0", type=unix_ms)
    origin.add_argument("--session-start-unix-ms", type=int)
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--registry-path", type=Path, help="Read-only Launcher anticheat_pids.json snapshot")
    source.add_argument("--self-only", action="store_true", help="Local smoke check, not coverage of team modules")
    parser.add_argument("--launcher-dir", type=Path, default=client_dir / "Launcher")
    parser.add_argument("--shared-root", type=Path)
    parser.add_argument("--output-dir", type=Path, default=module_dir / "logs")
    parser.add_argument("--interval", type=positive, default=2.0, help="Wait after each scan, seconds")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--duration", type=positive, help="Test-only stop time, checked between scans")
    parser.add_argument("--telemetry", choices=("off", "managed", "external"), default="off")
    parser.add_argument("--synthetic", action="store_true", help="Mark local fixture tests; never central telemetry")
    args = parser.parse_args(argv)
    if (args.synthetic or args.self_only) and args.telemetry != "off":
        parser.error("--synthetic and --self-only require --telemetry off")
    shared_root = args.shared_root or client_dir.parent
    if (shared_root / "shared/logger.py").is_file():
        sys.path.insert(0, str(shared_root.resolve()))
    elif args.shared_root:
        parser.error("--shared-root must contain shared/logger.py")
    try:
        if __package__:
            from .reporting import SessionLog, Sender, diagnostic
            from .registry_reader import RegistryReader
            from .monitor import Monitor
            from .probe import Probe
        else:
            from reporting import SessionLog, Sender, diagnostic
            from registry_reader import RegistryReader
            from monitor import Monitor
            from probe import Probe
    except ImportError:
        print("[AntiDebug] imports unavailable; check module files and --shared-root", file=sys.stderr)
        return 2
    registry_path = None
    if not args.self_only:
        directory = Path(os.environ.get("AC_LAUNCHER_LOG_DIR") or args.launcher_dir / "logs")
        registry_path = (args.registry_path or directory / "anticheat_pids.json").resolve()
    try:
        log = SessionLog(args.output_dir, args.session_id, args.player_id, registry_path=registry_path,
                         start_ms=args.t0 if args.t0 is not None else args.session_start_unix_ms,
                         synthetic=args.synthetic)
    except Exception as exc:
        diagnostic(f"SESSION_START_FAILED {type(exc).__name__}; check IDs, clock and writable output path")
        return 2
    diagnostic(f"version=0.1.0 logs={log.directory} scope={'self_only' if args.self_only else 'launcher_registered'}")
    if log.basis == "local_session_start":
        diagnostic("local session clock; integration must pass common --t0 from first run")
    reader = None if registry_path is None else RegistryReader(registry_path, args.session_id)
    monitor, sender = Monitor(Probe(), reader), Sender(args.telemetry)
    stop, previous = threading.Event(), {}
    code, reason = 0, "COMPLETED"

    def request_stop(signum, _frame):
        nonlocal reason
        reason = signal.Signals(signum).name
        stop.set()

    if threading.current_thread() is threading.main_thread():
        for sig in {signal.SIGINT, signal.SIGTERM, getattr(signal, "SIGBREAK", signal.SIGTERM)}:
            previous[sig] = signal.signal(sig, request_stop)
    started, sample = time.monotonic(), 0
    try:
        sender.start()
        while not stop.is_set():
            start = log.elapsed_ms()
            result = monitor.poll(stop)
            event = log.event(result, start, log.elapsed_ms(), sample)
            log.write(event)
            sender.send(event)
            state = event["evidence"]
            diagnostic(f"sample={sample} t={event['timestamp_ms']}ms status={state['status']} "
                       f"checked={state['checked_targets']} debugger={state['debugger_targets']} "
                       f"errors={state['error_targets']} registry={state['registry_state']} code={state['error_code']}")
            sample += 1
            if stop.is_set():
                break
            if args.once:
                code = (2 if state["error_code"] or state["error_targets"] else
                        1 if state["status"] == "DETECTED" else 0)
                reason = "SINGLE_SCAN_COMPLETED"
                break
            remaining = None if args.duration is None else args.duration - (time.monotonic() - started)
            if remaining is not None and remaining <= 0:
                reason = "DURATION_ELAPSED"
                break
            delay = args.interval if remaining is None else min(args.interval, remaining)
            deadline = time.monotonic() + delay
            # Give Python a chance to dispatch Windows console-break handlers.
            while not stop.is_set() and (wait := deadline - time.monotonic()) > 0:
                stop.wait(min(wait, 0.2))
            if args.duration is not None and time.monotonic() - started >= args.duration:
                if not stop.is_set():
                    reason = "DURATION_ELAPSED"
                break
    except KeyboardInterrupt:
        reason = "KEYBOARD_INTERRUPT"
    except Exception as exc:
        diagnostic(f"COLLECTOR_FAILED {type(exc).__name__}")
        code, reason = 3, "COLLECTOR_FAILED"
    finally:
        try:
            sender.close()
            log.finish(code, reason)
        except Exception as exc:
            diagnostic(f"FINALIZE_FAILED {type(exc).__name__}")
            code = 3
        finally:
            for sig, handler in previous.items():
                signal.signal(sig, handler)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
