"""Launcher Unix-seconds t0 anchored to a monotonic clock at startup."""
import time
from decimal import Decimal, InvalidOperation


class SessionClock:
    def __init__(self, t0=None, *, wall_ns=time.time_ns, mono_ns=time.monotonic_ns):
        self._mono_ns = mono_ns
        # Adjacent samples limit wall/monotonic anchor skew to startup call latency.
        self._anchor_ns = mono_ns()
        now_ns = wall_ns()
        if t0 is None:
            start_ns = now_ns
            self.basis = 'local_session_start'
        else:
            try:
                value = Decimal(str(t0))
                if not value.is_finite() or value < 0 or value > Decimal(now_ns) / 1_000_000_000:
                    raise ValueError('t0 must be finite Unix seconds in the past')
                start_ns = int(value * 1_000_000_000)
            except (InvalidOperation, TypeError) as exc:
                raise ValueError('t0 must be Unix epoch seconds') from exc
            self.basis = 'launcher_session_start'
        self.start_unix_ms = start_ns // 1_000_000
        self._offset_ns = now_ns - start_ns

    def elapsed_ms(self):
        return max(0, (self._offset_ns + self._mono_ns() - self._anchor_ns) // 1_000_000)

    def evidence(self):
        return {'timestamp_basis': self.basis, 'session_start_unix_ms': self.start_unix_ms}
