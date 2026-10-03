"""Watchdog demo responses only; never imports Launcher or manages a process."""


class DemoRegistry:
    def __init__(self):
        self.index = 0

    def restartable_names(self):
        return ["demo_continuous", "selfdefense", "autopaint"]

    def restart_if_dead(self, name, *, by):
        states = [("alive", 100), ("restarted", 101), ("backoff", None),
                  ("gave_up", None), ("gave_up", None), ("alive", 102),
                  ("orphaned", None), ("orphaned", None)]
        answer = states[min(self.index, len(states) - 1)]
        self.index += 1
        return (*answer, None)
