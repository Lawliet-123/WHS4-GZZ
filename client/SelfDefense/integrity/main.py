"""Read-only integrity scans. Never creates a baseline, repairs files or kills processes."""
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
        value = Decimal(value)
        if not value.is_finite() or not 0 <= value <= Decimal(time.time_ns()) / 1_000_000_000:
            raise ValueError
        return int(value * 1000)
    except (InvalidOperation, ValueError, OverflowError):
        raise argparse.ArgumentTypeError("--t0 must be past Unix seconds") from None


def main(argv=None):
    module_dir = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--player-id", required=True)
    parser.add_argument("--root", type=Path, required=True, help="Approved deployment root; never the whole PC")
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--baseline-sha256", required=True, help="Trusted release digest, NOT recomputed on startup")
    parser.add_argument("--shared-root", type=Path)
    origin = parser.add_mutually_exclusive_group()
    origin.add_argument("--t0", type=unix_ms)
    origin.add_argument("--session-start-unix-ms", type=int)
    parser.add_argument("--output-dir", type=Path, default=module_dir / "logs")
    parser.add_argument("--interval", type=positive, default=30.0, help="Wait after each completed scan")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--duration", type=positive, help="Test-only stop time, checked between file operations")
    parser.add_argument("--telemetry", choices=("off", "managed", "external"), default="off")
    parser.add_argument("--synthetic", action="store_true", help="Fixture-only local test; never central telemetry")
    args = parser.parse_args(argv)
    if args.synthetic and args.telemetry != "off":
        parser.error("--synthetic requires --telemetry off")
    shared_root = args.shared_root or module_dir.parent.parent.parent
    if (shared_root / "shared/logger.py").is_file():
        sys.path.insert(0, str(shared_root.resolve()))
    elif args.shared_root:
        parser.error("--shared-root must contain shared/logger.py")
    if __package__:
        from .baseline import check_digest
        from .scanner import scan
    else:
        from baseline import check_digest
        from scanner import scan
    try:
        check_digest(args.baseline_sha256)
    except ValueError:
        parser.error("--baseline-sha256 must be 64 lowercase hexadecimal characters")
    try:
        if __package__:
            from .reporting import SessionLog, Sender, diagnostic
        else:
            from reporting import SessionLog, Sender, diagnostic
    except ImportError:
        print("[Integrity] shared unavailable; set --shared-root", file=sys.stderr)
        return 2
    root = Path(os.path.abspath(args.root))
    baseline_path = Path(os.path.abspath(args.baseline))
    try:
        log = SessionLog(args.output_dir, args.session_id, args.player_id, pin=args.baseline_sha256,
                         root=root, synthetic=args.synthetic,
                         start_ms=args.t0 if args.t0 is not None else args.session_start_unix_ms)
    except Exception as exc:
        diagnostic(f"SESSION_START_FAILED {type(exc).__name__}; check IDs, time, baseline and output permissions")
        return 2
    diagnostic(f"version=0.1.0 logs={log.directory}")
    if log.basis == "local_session_start":
        diagnostic("local session clock; integration must pass common --t0 from the first run")
    sender, stop, previous = Sender(args.telemetry), threading.Event(), {}
    code, reason = 0, "COMPLETED"

    def request_stop(signum, _frame):
        nonlocal reason
        reason = signal.Signals(signum).name
        stop.set()

    if threading.current_thread() is threading.main_thread():
        for sig in {signal.SIGINT, signal.SIGTERM, getattr(signal, "SIGBREAK", signal.SIGTERM)}:
            previous[sig] = signal.signal(sig, request_stop)
    started = time.monotonic()
    sample = 0
    try:
        sender.start()
        while not stop.is_set():
            start = log.elapsed_ms()
            result = scan(root, baseline_path, args.baseline_sha256, stop=stop)
            end = log.elapsed_ms()
            event = log.event(result, start, end, sample)
            log.write(result, event)  # A local write failure never masquerades as a complete collection.
            sender.send(event)
            state = event["evidence"]
            diagnostic(f"sample={sample} t={end}ms state={state['integrity_state']} "
                       f"modified={state['modified_files']} missing={state['missing_files']} "
                       f"unexpected={state['unexpected_files']} errors={state['error_files']} "
                       f"code={state['error_code']}")
            sample += 1
            if stop.is_set():
                break
            if args.once:
                code = 2 if not state["scan_complete"] else 1 if state["status"] == "DETECTED" else 0
                reason = "SINGLE_SCAN_COMPLETED"
                break
            remaining = None if args.duration is None else args.duration - (time.monotonic() - started)
            if remaining is not None and remaining <= 0:
                reason = "DURATION_ELAPSED"
                break
            delay = args.interval if remaining is None else min(args.interval, remaining)
            deadline = time.monotonic() + delay
            # Windows may defer the Python SIGBREAK handler during a long Event.wait.
            # Short waits allow Launcher console-break shutdown without a 30s stall.
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
