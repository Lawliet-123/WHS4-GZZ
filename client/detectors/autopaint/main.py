"""AutoPaint entry point for the team Launcher."""
import signal

if __package__:
    from .gzz_anticheat.cli import main as run_detector
else:
    from gzz_anticheat.cli import main as run_detector


def main(argv=None):
    # The Launcher stops modules with Ctrl+Break. Python's default handling exits
    # immediately, so the session manifest and the shared flush/shutdown never run.
    # Turn it into KeyboardInterrupt like Ctrl+C (client/Launcher/README.md).
    if hasattr(signal, "SIGBREAK"):
        try:
            signal.signal(signal.SIGBREAK, signal.default_int_handler)
        except (ValueError, OSError):
            pass          # not the main thread (in-process integration)
    return run_detector(argv)


if __name__ == "__main__":
    raise SystemExit(main())
