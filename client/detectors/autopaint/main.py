"""AutoPaint entry point for the team Launcher."""
if __package__:
    from .gzz_anticheat.cli import main as run_detector
else:
    from gzz_anticheat.cli import main as run_detector


def main(argv=None):
    return run_detector(argv)


if __name__ == "__main__":
    raise SystemExit(main())
