"""Stage-specific startup diagnostics, available even before device attachment."""
from datetime import datetime, timezone
import json
from pathlib import Path
import sys


class StartupError(RuntimeError):
    pass


class StartupDiagnostics:
    def __init__(self, output):
        # Sibling file does not pre-create the exclusive collector output folder.
        self.path = Path(str(output) + '.startup.jsonl')

    def emit(self, stage, status, **details):
        record = dict(type='startup_step', time=datetime.now(timezone.utc).isoformat(),
                      stage=stage, status=status, **details)
        line = json.dumps(record, ensure_ascii=False)
        print(line, file=sys.stderr, flush=True)
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open('a', encoding='utf-8') as stream:
                stream.write(line+'\n')
        except OSError:
            # stderr still identifies the actual startup failure.
            pass

    def call(self, stage, function, *args, **kwargs):
        self.emit(stage, 'begin')
        try:
            result = function(*args, **kwargs)
        except Exception as exc:
            self.emit(stage, 'failed', error_type=type(exc).__name__,
                      winerror=getattr(exc, 'winerror', None), errno=getattr(exc, 'errno', None),
                      message=str(exc))
            raise StartupError(f'stage={stage}: {exc}') from exc
        self.emit(stage, 'ok')
        return result
