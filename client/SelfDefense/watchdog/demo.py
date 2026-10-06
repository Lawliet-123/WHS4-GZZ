"""Watchdog demo responses only; never imports Launcher or manages a process."""

if __package__:
    from .launcher_adapter import Snapshot, Target, ProcessState
else:
    from launcher_adapter import Snapshot, Target, ProcessState


class DemoRegistry:
    def __init__(self):
        self.index = 0

    def inspect(self):
        return Snapshot(False, (
            Target("demo_continuous", 100, "continuous", True, ProcessState("missing")),
            Target("selfdefense", 101, "continuous", True, ProcessState("alive")),
            Target("autopaint", 102, "continuous", False, ProcessState("alive")),
        ))

    def close(self):
        pass

    def restart_if_dead(self, name, *, by):
        states = [("alive", 100), ("restarted", 101), ("backoff", None),
                  ("gave_up", None), ("gave_up", None), ("alive", 102),
                  ("orphaned", None), ("orphaned", None)]
        answer = states[min(self.index, len(states) - 1)]
        self.index += 1
        return (*answer, None)
