"""Factual top-level window geometry sensor used by the ESP detector."""

from __future__ import annotations

import os
import time
from typing import Any, Callable, Mapping, Sequence

from ..core.context import SensorContext
from ..core.events import SensorBatch, SensorEvent
from ..windows_api import (
    Rect,
    WS_EX_LAYERED,
    WS_EX_TOPMOST,
    WS_EX_TRANSPARENT,
    WindowInfo,
    list_top_level_windows,
)


_SYSTEM_PIDS = frozenset({0, 4})


def _intersection(first: Rect, second: Rect) -> Rect | None:
    value = Rect(
        max(first.left, second.left),
        max(first.top, second.top),
        min(first.right, second.right),
        min(first.bottom, second.bottom),
    )
    return value if value.area > 0 else None


def _style_labels(style: int) -> tuple[str, ...]:
    return tuple(
        label
        for bit, label in (
            (WS_EX_LAYERED, "LAYERED"),
            (WS_EX_TRANSPARENT, "TRANSPARENT"),
            (WS_EX_TOPMOST, "TOPMOST"),
        )
        if int(style) & bit
    )


class WindowOverlapSensor:
    """Emit overlap and style facts without deciding that a window is an ESP."""

    sensor_id = "window_overlap"

    def __init__(
        self,
        *,
        window_provider: Callable[[], Sequence[WindowInfo]] = list_top_level_windows,
        self_pid: int | None = None,
        cooldown_seconds: float = 30.0,
        clock: Callable[[], float] = time.time,
        monotonic_clock: Callable[[], float] = time.monotonic,
        process_identity_provider: Callable[[str], Mapping[str, Any]] | None = None,
    ) -> None:
        if cooldown_seconds < 0:
            raise ValueError("cooldown_seconds must be non-negative")
        self._provider = window_provider
        self._self_pid = os.getpid() if self_pid is None else int(self_pid)
        self._cooldown_seconds = float(cooldown_seconds)
        self._clock = clock
        self._monotonic = monotonic_clock
        self._process_identity_provider = process_identity_provider
        self._last_emitted: dict[tuple[int, int, int], float] = {}

    def reset_cooldowns(self) -> None:
        self._last_emitted.clear()

    def poll(self, context: SensorContext) -> SensorBatch:
        if not isinstance(context, SensorContext):
            raise TypeError("context must be SensorContext")
        if not context.targets:
            return SensorBatch(
                sensor_id=self.sensor_id,
                status="waiting",
                message="waiting for the game process",
            )
        try:
            windows = tuple(self._provider())
        except Exception as exc:
            return SensorBatch(
                sensor_id=self.sensor_id,
                status="error",
                message=f"window enumeration failed: {exc}",
            )

        target_by_pid = context.target_by_pid
        game_windows = tuple(
            window
            for window in windows
            if window.pid in target_by_pid and window.visible and window.rect.area > 0
        )
        if not game_windows:
            return SensorBatch(
                sensor_id=self.sensor_id,
                status="waiting",
                message="waiting for a visible game window",
                details={"game_window_found": False},
            )

        excluded = _SYSTEM_PIDS | {self._self_pid} | set(target_by_pid)
        best: dict[tuple[int, int, int], tuple[WindowInfo, WindowInfo, Rect, float, float]] = {}
        for candidate in windows:
            if (
                candidate.pid in excluded
                or not candidate.visible
                or candidate.rect.area <= 0
            ):
                continue
            for game in game_windows:
                intersection = _intersection(game.rect, candidate.rect)
                if intersection is None:
                    continue
                game_ratio = intersection.area / game.rect.area
                candidate_ratio = intersection.area / candidate.rect.area
                key = (game.pid, candidate.pid, candidate.hwnd)
                previous = best.get(key)
                if previous is None or game_ratio > previous[3]:
                    best[key] = (
                        game,
                        candidate,
                        intersection,
                        game_ratio,
                        candidate_ratio,
                    )

        now = self._clock()
        monotonic_now = self._monotonic()
        emitted: list[SensorEvent] = []
        for key, (_game, candidate, intersection, game_ratio, candidate_ratio) in sorted(
            best.items()
        ):
            last = self._last_emitted.get(key)
            if last is not None and monotonic_now - last < self._cooldown_seconds:
                continue
            self._last_emitted[key] = monotonic_now
            game_pid = key[0]
            target = target_by_pid[game_pid]
            identity: dict[str, Any] = {}
            if self._process_identity_provider is not None and candidate.process_path:
                try:
                    identity = dict(
                        self._process_identity_provider(candidate.process_path)
                    )
                except Exception as exc:
                    identity = {
                        "identity_status": "error",
                        "identity_error": str(exc),
                    }
            emitted.append(
                SensorEvent(
                    session_id=context.session_id,
                    sensor_id=self.sensor_id,
                    event_type="window_overlap",
                    subject_id=target.subject_id,
                    timestamp_ms=int(round(now * 1000.0)),
                    payload={
                        "game_pid": game_pid,
                        "window_pid": candidate.pid,
                        "hwnd": candidate.hwnd,
                        "process_path": candidate.process_path,
                        "title": candidate.title,
                        "class_name": candidate.class_name,
                        "extended_style": candidate.ex_style,
                        "style_labels": list(_style_labels(candidate.ex_style)),
                        "window_rect": [
                            candidate.rect.left,
                            candidate.rect.top,
                            candidate.rect.right,
                            candidate.rect.bottom,
                        ],
                        "intersection_rect": [
                            intersection.left,
                            intersection.top,
                            intersection.right,
                            intersection.bottom,
                        ],
                        "game_overlap_ratio": game_ratio,
                        "candidate_overlap_ratio": candidate_ratio,
                        **identity,
                    },
                )
            )
        return SensorBatch(
            sensor_id=self.sensor_id,
            status="online",
            events=tuple(emitted),
            message=f"observed {len(emitted)} overlapping external window(s)",
            details={"game_window_found": True, "window_count": len(windows)},
            observed_at_ms=int(round(now * 1000.0)),
        )


__all__ = ["WindowOverlapSensor"]
