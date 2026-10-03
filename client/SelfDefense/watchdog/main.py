"""SelfDefense watchdog entry point. Launcher owns process lifecycle mechanics."""
from __future__ import annotations

import argparse
import math
import signal
import sys
import threading
import time
from decimal import Decimal, InvalidOperation
from pathlib import Path


def positive(value):
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError("must be finite and greater than zero")
    return number


def unix_seconds_ms(value):
    """Launcher --t0 is Unix seconds; convert once without binary float drift."""
    try:
        seconds = Decimal(value)
        if not seconds.is_finite() or not 0 <= seconds <= Decimal(time.time_ns()) / 1_000_000_000:
            raise ValueError
        return int(seconds * 1000)
    except (InvalidOperation, ValueError, OverflowError):
        raise argparse.ArgumentTypeError("--t0 must be finite Unix seconds in the past") from None


def main(argv=None):
    module_dir = Path(__file__).resolve().parent
    client_dir = module_dir.parent.parent  # <repo>/client/SelfDefense/watchdog
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--player-id", required=True)
    origin = parser.add_mutually_exclusive_group()
    origin.add_argument("--session-start-unix-ms", type=int)
    origin.add_argument("--t0", type=unix_seconds_ms, help="Launcher session start in Unix seconds")
    parser.add_argument("--launcher-dir", type=Path, default=client_dir / "Launcher")
    parser.add_argument("--shared-root", type=Path, help="Directory containing the shared Python package")
    parser.add_argument("--output-dir", type=Path, default=Path(__file__).resolve().parent / "logs")
    parser.add_argument("--interval", type=positive, default=1.0)
    parser.add_argument("--duration", type=positive, help="Test-only time limit; omit for CONTINUOUS")
    parser.add_argument("--self-name", default="self_defense")
    parser.add_argument("--exclude-module", action="append", default=[])
    parser.add_argument("--telemetry", choices=("managed", "external", "off"), default="managed")
    parser.add_argument("--demo", action="store_true", help="Synthetic registry responses; never starts/kills processes")
    args = parser.parse_args(argv)
    if args.demo and args.telemetry != "off":
        parser.error("--demo requires --telemetry off; synthetic data must not reach the central server")
    shared_root = args.shared_root or client_dir.parent
    if (shared_root / "shared" / "logger.py").is_file():
        sys.path.insert(0, str(shared_root.resolve()))
    elif args.shared_root:
        parser.error("--shared-root must contain shared/logger.py")
    # Both direct main.py and python -m client.SelfDefense.watchdog.main work.
    if __package__:
        from .launcher_adapter import LauncherRegistry
        from .monitor import Watchdog
        from .demo import DemoRegistry
    else:
        from launcher_adapter import LauncherRegistry
        from monitor import Watchdog
        from demo import DemoRegistry
    try:
        if __package__:
            from .reporting import Reporter, SessionLog, diagnostic
        else:
            from reporting import Reporter, SessionLog, diagnostic
    except ImportError:
        print("[SelfDefense] shared unavailable; set --shared-root or PYTHONPATH", file=sys.stderr)
        return 2
    try:
        log = SessionLog(args.output_dir, args.session_id, args.player_id,
                         start_unix_ms=args.t0 if args.t0 is not None else args.session_start_unix_ms,
                         synthetic=args.demo)
    except Exception as exc:
        diagnostic(f"SESSION_START_FAILED {type(exc).__name__}; check IDs, clock and writable output path")
        return 2
    diagnostic(f"version=0.2.1 synthetic={args.demo} logs={log.directory}")
    if log.basis == "local_session_start":
        diagnostic("local session clock; integration must pass common --t0 or --session-start-unix-ms on FIRST run")
    reporter = Reporter(log, args.telemetry)
    registry = DemoRegistry() if args.demo else LauncherRegistry(args.launcher_dir, expected_session=args.session_id)
    watchdog = Watchdog(registry, self_name=args.self_name, excluded=("autopaint", *args.exclude_module))
    stop = threading.Event()
    stop_reason = "COMPLETED"

    def request_stop(signum, _frame):
        nonlocal stop_reason
        stop_reason = signal.Signals(signum).name
        stop.set()

    handlers = {}
    if threading.current_thread() is threading.main_thread():
        signals = [signal.SIGINT, signal.SIGTERM]
        if hasattr(signal, "SIGBREAK"):
            signals.append(signal.SIGBREAK)
        for sig in signals:
            handlers[sig] = signal.signal(sig, request_stop)
    started = time.monotonic()
    exit_code = 0
    try:
        reporter.start()
        while not stop.is_set():
            for observation in watchdog.poll():
                reporter.record(observation)
            reporter.check_delivery()
            remaining = None if args.duration is None else args.duration - (time.monotonic() - started)
            if remaining is not None and remaining <= 0:
                stop_reason = "DURATION_ELAPSED"
                break
            stop.wait(args.interval if remaining is None else min(args.interval, remaining))
    except KeyboardInterrupt:
        stop_reason = "KEYBOARD_INTERRUPT"
    except Exception as exc:
        diagnostic(f"WATCHDOG_FAILED {type(exc).__name__}")
        exit_code = 3
        stop_reason = "WATCHDOG_FAILED"
    finally:
        try:
            reporter.close()
            log.finish(exit_code, stop_reason)
        except Exception as exc:
            diagnostic(f"FINALIZE_FAILED {type(exc).__name__}")
            exit_code = 3
        finally:
            for sig, previous in handlers.items():
                signal.signal(sig, previous)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
