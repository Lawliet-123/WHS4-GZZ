"""Observe completed original ESP paint calls without changing their output.

No Qt, process, file, network or game APIs are imported here. A returned frame
is ephemeral: callers must validate its freshness and retain scalar facts only.
Draw calls prove original painter activity, not compositor pixels or a human
seeing them. First/last observed activity bounds do not prove continuous use.
"""
from __future__ import annotations

from dataclasses import dataclass
import inspect
import threading


@dataclass(frozen=True)
class CompletedPaintObservation:
    frame: object
    remote_box_lines: int
    remote_skeleton_lines: int
    remote_player_draw_count: int


class _CountingPainter:
    def __init__(self, painter):
        self._painter = painter
        self.lines = 0

    def __getattr__(self, name):
        return getattr(self._painter, name)

    def _active(self):
        try:
            return self._painter.isActive() is True
        except Exception:
            return False

    def drawLine(self, *args, **kwargs):
        active = self._active()
        result = self._painter.drawLine(*args, **kwargs)
        if active and self._active():
            self.lines += 1
        return result


class _ObservedStore:
    def __init__(self, store, context):
        self._store = store
        self._context = context

    def __getattr__(self, name):
        return getattr(self._store, name)

    def latest(self):
        frame = self._store.latest()
        context = self._context
        if threading.get_ident() == context["thread"]:
            if not context["frame_seen"]:
                context["frame"] = frame
                context["frame_seen"] = True
            elif context["frame"] is not frame:
                context["frame_changed"] = True
        return frame


class CompletedPaintObserver:
    """Temporarily wrap only the original geometry helpers during one paint.

    Local/remote identity comes from the exact player object in the frame used
    by the original paint, not its color. Zero-clipped box calls are not draws.
    Helpers, store APIs and return values are delegated; exceptions propagate
    and no observation is returned for a partially failed paint. The caller
    must deduplicate frame sequence/freshness across repeated paints.
    """

    _patch_lock = threading.RLock()

    def __init__(self, module):
        self._module = module
        self._painting = False

    def paint(self, overlay, event, original_paint):
        with self._patch_lock:
            if self._painting:
                raise RuntimeError("Completed paint observation is non-reentrant")
            self._painting = True
            try:
                return self._paint(overlay, event, original_paint)
            finally:
                self._painting = False

    def _paint(self, overlay, event, original_paint):
        module = self._module
        cls = module.Overlay
        original_descriptors = {name: inspect.getattr_static(cls, name) for name in (
            "_player_color", "_draw_box", "_draw_skeleton")}
        original_color = cls._player_color
        original_box = cls._draw_box
        original_skeleton = cls._draw_skeleton
        original_store = overlay._snapshots
        context = {"thread": threading.get_ident(), "frame_seen": False,
                   "frame": None, "frame_changed": False, "player": None,
                   "box_lines": 0, "skeleton_lines": 0, "players": set()}

        def current_remote(owner):
            if owner is not overlay or threading.get_ident() != context["thread"]:
                return None
            player, frame = context["player"], context["frame"]
            if (not isinstance(frame, module.FrameRenderSnapshot)
                    or not isinstance(player, module.PlayerRenderSnapshot)
                    or player.is_local is not False
                    or not any(candidate is player for candidate in frame.players)):
                return None
            return player

        def player_color(config, player):
            result = original_color(config, player)
            if threading.get_ident() == context["thread"] and config is overlay.config:
                context["player"] = player
            return result

        def draw_box(owner, painter, *args, **kwargs):
            remote = current_remote(owner)
            counted = _CountingPainter(painter) if remote is not None else painter
            result = original_box(owner, counted, *args, **kwargs)
            if remote is not None and counted.lines:
                context["box_lines"] += counted.lines
                context["players"].add(id(remote))
            return result

        def draw_skeleton(owner, painter, *args, **kwargs):
            remote = current_remote(owner)
            counted = _CountingPainter(painter) if remote is not None else painter
            result = original_skeleton(owner, counted, *args, **kwargs)
            if (remote is not None and type(result) is int
                    and result > 0 and result == counted.lines):
                context["skeleton_lines"] += counted.lines
                context["players"].add(id(remote))
            return result

        try:
            overlay._snapshots = _ObservedStore(original_store, context)
            cls._player_color = staticmethod(player_color)
            cls._draw_box = draw_box
            cls._draw_skeleton = draw_skeleton
            original_paint(overlay, event)
            if (not context["frame_seen"] or context["frame_changed"]
                    or not isinstance(context["frame"], module.FrameRenderSnapshot)):
                return None
            return CompletedPaintObservation(
                context["frame"], context["box_lines"], context["skeleton_lines"],
                len(context["players"]))
        finally:
            overlay._snapshots = original_store
            for name, descriptor in original_descriptors.items():
                setattr(cls, name, descriptor)


__all__ = ["CompletedPaintObservation", "CompletedPaintObserver"]
