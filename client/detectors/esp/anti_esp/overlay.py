"""Detection of external overlay-shaped windows intersecting the game."""

from __future__ import annotations

from dataclasses import dataclass
import os
import time
from typing import Callable, Iterable, Sequence

from .models import EvidenceEvent
from .windows_api import (
    Rect,
    WS_EX_LAYERED,
    WS_EX_TOPMOST,
    WS_EX_TRANSPARENT,
    WindowInfo,
    list_top_level_windows,
)


SYSTEM_PIDS = frozenset({0, 4})


@dataclass(frozen=True, slots=True)
class OverlayAssessment:
    suspicious: bool
    reason: str
    style_labels: tuple[str, ...]
    intersection: Rect | None
    game_overlap_ratio: float
    candidate_overlap_ratio: float
    strength: float
    reliability: float


def intersect_rectangles(first: Rect, second: Rect) -> Rect | None:
    """Return the positive-area intersection of two rectangles."""

    intersection = Rect(
        left=max(first.left, second.left),
        top=max(first.top, second.top),
        right=min(first.right, second.right),
        bottom=min(first.bottom, second.bottom),
    )
    return intersection if intersection.area > 0 else None


def extended_style_labels(ex_style: int) -> tuple[str, ...]:
    styles = (
        (WS_EX_LAYERED, "LAYERED"),
        (WS_EX_TRANSPARENT, "TRANSPARENT"),
        (WS_EX_TOPMOST, "TOPMOST"),
    )
    return tuple(label for bit, label in styles if int(ex_style) & bit)


def assess_overlay_window(
    game_window: WindowInfo,
    candidate: WindowInfo,
    *,
    excluded_pids: Iterable[int] = (),
    minimum_overlap_ratio: float = 0.55,
) -> OverlayAssessment:
    """Purely assess whether *candidate* resembles an external game overlay.

    The overlap threshold is measured against the game window.  Extended
    styles alone are intentionally insufficient: a candidate must have at
    least two of layered, input-transparent, and topmost.  This substantially
    reduces noise from ordinary topmost utility windows.
    """

    if not 0.0 <= minimum_overlap_ratio <= 1.0:
        raise ValueError("minimum_overlap_ratio must be between 0 and 1")

    excluded = SYSTEM_PIDS | {int(pid) for pid in excluded_pids}
    styles = extended_style_labels(candidate.ex_style)

    if not game_window.visible or game_window.rect.area <= 0:
        return OverlayAssessment(
            False, "game window is not visible", styles, None, 0.0, 0.0, 0.0, 0.0
        )
    if not candidate.visible or candidate.rect.area <= 0:
        return OverlayAssessment(
            False,
            "candidate window is not visible",
            styles,
            None,
            0.0,
            0.0,
            0.0,
            0.0,
        )
    if candidate.hwnd == game_window.hwnd or candidate.pid == game_window.pid:
        return OverlayAssessment(
            False,
            "candidate belongs to the game",
            styles,
            None,
            0.0,
            0.0,
            0.0,
            0.0,
        )
    if candidate.pid in excluded:
        return OverlayAssessment(
            False,
            "candidate PID is excluded",
            styles,
            None,
            0.0,
            0.0,
            0.0,
            0.0,
        )

    intersection = intersect_rectangles(game_window.rect, candidate.rect)
    if intersection is None:
        return OverlayAssessment(
            False,
            "candidate does not overlap the game",
            styles,
            None,
            0.0,
            0.0,
            0.0,
            0.0,
        )

    game_ratio = intersection.area / game_window.rect.area
    candidate_ratio = intersection.area / candidate.rect.area
    if game_ratio < minimum_overlap_ratio:
        return OverlayAssessment(
            False,
            "candidate overlap is below the configured threshold",
            styles,
            intersection,
            game_ratio,
            candidate_ratio,
            0.0,
            0.0,
        )

    layered = bool(candidate.ex_style & WS_EX_LAYERED)
    transparent = bool(candidate.ex_style & WS_EX_TRANSPARENT)
    topmost = bool(candidate.ex_style & WS_EX_TOPMOST)
    strong_style_combination = (
        (layered and transparent)
        or (layered and topmost)
        or (transparent and topmost)
    )
    if not strong_style_combination:
        return OverlayAssessment(
            False,
            "candidate lacks a suspicious extended-style combination",
            styles,
            intersection,
            game_ratio,
            candidate_ratio,
            0.0,
            0.0,
        )

    style_strength = (
        (0.30 if layered else 0.0)
        + (0.30 if transparent else 0.0)
        + (0.20 if topmost else 0.0)
    )
    strength = min(1.0, style_strength + min(0.20, game_ratio * 0.20))
    reliability = min(0.94, 0.68 + 0.07 * len(styles) + 0.05 * game_ratio)
    return OverlayAssessment(
        suspicious=True,
        reason="external window overlaps the game and has overlay-like styles",
        style_labels=styles,
        intersection=intersection,
        game_overlap_ratio=game_ratio,
        candidate_overlap_ratio=candidate_ratio,
        strength=strength,
        reliability=reliability,
    )


class OverlayMonitor:
    """Enumerate overlay candidates and apply per-window emission cooldowns."""

    def __init__(
        self,
        *,
        minimum_overlap_ratio: float = 0.55,
        cooldown_seconds: float = 30.0,
        self_pid: int | None = None,
        excluded_pids: Iterable[int] = (),
        window_provider: Callable[[], Sequence[WindowInfo]] = list_top_level_windows,
        monotonic_clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
    ) -> None:
        if not 0.0 <= minimum_overlap_ratio <= 1.0:
            raise ValueError("minimum_overlap_ratio must be between 0 and 1")
        if cooldown_seconds < 0.0:
            raise ValueError("cooldown_seconds must be non-negative")
        self.minimum_overlap_ratio = float(minimum_overlap_ratio)
        self.cooldown_seconds = float(cooldown_seconds)
        self.self_pid = os.getpid() if self_pid is None else int(self_pid)
        self.excluded_pids = frozenset(int(pid) for pid in excluded_pids)
        self._window_provider = window_provider
        self._monotonic_clock = monotonic_clock
        self._wall_clock = wall_clock
        self._last_emitted: dict[str, float] = {}
        self.last_game_window_found = False

    def reset_cooldowns(self) -> None:
        self._last_emitted.clear()

    def scan(
        self, game_pid: int, *, session_id: str = "default"
    ) -> list[EvidenceEvent]:
        """Enumerate current windows and emit new overlay evidence."""

        # Sensor failures must reach the controller so observation confidence is
        # reduced instead of incorrectly reporting an online sensor with no data.
        windows = tuple(self._window_provider())
        return self.scan_windows(game_pid, windows, session_id=session_id)

    def scan_windows(
        self,
        game_pid: int,
        windows: Sequence[WindowInfo],
        *,
        session_id: str = "default",
    ) -> list[EvidenceEvent]:
        """Pure-input scan entry point used by tests and replayed snapshots."""

        game_pid = int(game_pid)
        game_windows = [
            window
            for window in windows
            if window.pid == game_pid and window.visible and window.rect.area > 0
        ]
        self.last_game_window_found = bool(game_windows)
        if not game_windows:
            return []

        excluded = SYSTEM_PIDS | self.excluded_pids | {self.self_pid, game_pid}
        best_by_candidate: dict[tuple[int, int], OverlayAssessment] = {}
        candidate_by_key: dict[tuple[int, int], WindowInfo] = {}

        for candidate in windows:
            if candidate.pid in excluded or not candidate.visible:
                continue
            key = (candidate.pid, candidate.hwnd)
            for game_window in game_windows:
                assessment = assess_overlay_window(
                    game_window,
                    candidate,
                    excluded_pids=excluded,
                    minimum_overlap_ratio=self.minimum_overlap_ratio,
                )
                if not assessment.suspicious:
                    continue
                previous = best_by_candidate.get(key)
                if previous is None or assessment.strength > previous.strength:
                    best_by_candidate[key] = assessment
                    candidate_by_key[key] = candidate

        now_monotonic = self._monotonic_clock()
        now_wall = self._wall_clock()
        emitted: list[EvidenceEvent] = []
        for key, assessment in sorted(best_by_candidate.items()):
            candidate = candidate_by_key[key]
            cooldown_key = f"overlay:{game_pid}:{candidate.pid}:{candidate.hwnd}"
            last_emitted = self._last_emitted.get(cooldown_key)
            if (
                last_emitted is not None
                and now_monotonic - last_emitted < self.cooldown_seconds
            ):
                continue
            self._last_emitted[cooldown_key] = now_monotonic

            intersection = assessment.intersection
            emitted.append(
                EvidenceEvent(
                    category="overlay",
                    strength=assessment.strength,
                    reliability=assessment.reliability,
                    source="windows:window-enumeration",
                    timestamp=now_wall,
                    reason=assessment.reason,
                    details={
                        "game_pid": game_pid,
                        "window_pid": candidate.pid,
                        "hwnd": candidate.hwnd,
                        "process_path": candidate.process_path,
                        "title": candidate.title,
                        "class_name": candidate.class_name,
                        "extended_style": candidate.ex_style,
                        "style_labels": list(assessment.style_labels),
                        "window_rect": [
                            candidate.rect.left,
                            candidate.rect.top,
                            candidate.rect.right,
                            candidate.rect.bottom,
                        ],
                        "intersection_rect": (
                            [
                                intersection.left,
                                intersection.top,
                                intersection.right,
                                intersection.bottom,
                            ]
                            if intersection is not None
                            else None
                        ),
                        "game_overlap_ratio": assessment.game_overlap_ratio,
                        "candidate_overlap_ratio": assessment.candidate_overlap_ratio,
                    },
                    dedup_key=cooldown_key,
                    session_id=session_id,
                    subject_id=str(game_pid),
                )
            )
        return emitted


# Name retained for callers that prefer the scanner terminology.
OverlayScanner = OverlayMonitor


__all__ = [
    "OverlayAssessment",
    "OverlayMonitor",
    "OverlayScanner",
    "SYSTEM_PIDS",
    "assess_overlay_window",
    "extended_style_labels",
    "intersect_rectangles",
]
