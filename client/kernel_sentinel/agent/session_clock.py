"""Common Launcher epoch-seconds origin, with monotonic collection timestamps."""
from decimal import Decimal, InvalidOperation
import time


class SessionClock:
    def __init__(self, t0=None):
        self.anchor = time.monotonic_ns()
        now = time.time_ns()
        try:
            value = Decimal(str(t0)) if t0 is not None else Decimal(now) / 1_000_000_000
            if not value.is_finite() or value < 0 or value > Decimal(now) / 1_000_000_000:
                raise ValueError('t0 must be finite Unix seconds in the past')
            self.start_ns = int(value * 1_000_000_000)
        except InvalidOperation as exc:
            raise ValueError('t0 must be Unix epoch seconds') from exc
        self.offset = now - self.start_ns
        self.basis = 'launcher_session_start' if t0 is not None else 'local_session_start'

    def elapsed_ms(self):
        return max(0, (self.offset + time.monotonic_ns() - self.anchor) // 1_000_000)

    def event_ms(self, event):
        # Driver timestamps are acquisition times, not queue drain times.
        if isinstance(event.get('unix_ms'), int) and not isinstance(event['unix_ms'], bool):
            return max(0, event['unix_ms'] - self.start_ns // 1_000_000)
        return self.elapsed_ms()

    def evidence(self):
        return {'timestamp_basis': self.basis, 'session_start_unix_ms': self.start_ns // 1_000_000}
