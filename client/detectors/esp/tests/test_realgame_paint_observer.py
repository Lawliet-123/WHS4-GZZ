"""Pure fake-painter tests: no Qt, game, OS sensor, server or PoC is run."""
from dataclasses import dataclass
import importlib.util
import inspect
from pathlib import Path
import sys
import threading
from types import SimpleNamespace
import unittest


SOURCE = Path(__file__).resolve().parents[1] / "scripts/realgame_paint_observer.py"
SPEC = importlib.util.spec_from_file_location("realgame_paint_observer_test", SOURCE)
observer_module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = observer_module
SPEC.loader.exec_module(observer_module)


@dataclass(frozen=True)
class Player:
    is_local: bool


@dataclass(frozen=True)
class Frame:
    players: tuple
    sequence: int = 3


class Painter:
    def __init__(self, active=True, fail_at=None):
        self.active, self.fail_at = active, fail_at
        self.lines, self.pens, self.texts = [], [], []

    def isActive(self):
        return self.active

    def drawLine(self, *args, **kwargs):
        if self.fail_at == len(self.lines):
            raise ValueError("fake painter failure")
        self.lines.append((args, kwargs))
        return "delegated"

    def setPen(self, value):
        self.pens.append(value)

    def drawText(self, value):
        self.texts.append(value)


class Store:
    def __init__(self, frame):
        self.frame, self.published = frame, []

    def latest(self):
        return self.frame

    def publish(self, frame):
        self.frame = frame
        self.published.append(frame)
        return "published"


def fixture(players):
    class Overlay:
        @staticmethod
        def _player_color(config, player):
            return "same-local-and-remote-color"

        def _draw_box(self, painter, lines, color):
            painter.setPen(color)
            for number in range(lines):
                painter.drawLine(number, 1, number, 2)
            # Original ESP box returns None even when no segments clip.

        def _draw_skeleton(self, painter, lines, color):
            painter.setPen(color)
            for number in range(lines):
                painter.drawLine(number, 3, number, 4)
            return lines

    overlay = Overlay()
    overlay.config = object()
    overlay._snapshots = Store(Frame(tuple(players)))
    module = SimpleNamespace(Overlay=Overlay, FrameRenderSnapshot=Frame,
                             PlayerRenderSnapshot=Player)
    return module, overlay, observer_module.CompletedPaintObserver(module)


class CompletedPaintObserverTests(unittest.TestCase):
    def test_counts_successful_original_remote_geometry_and_unique_players_only(self):
        local, remote = Player(True), Player(False)
        module, overlay, observer = fixture((local, remote))
        painter = Painter()
        def paint(owner, event):
            frame = owner._snapshots.latest()
            for player in frame.players:
                color = module.Overlay._player_color(owner.config, player)
                self.assertIsNone(owner._draw_box(painter, 2, color))
                self.assertEqual(owner._draw_skeleton(painter, 3, color), 3)
            painter.drawText("HUD and labels are not geometry evidence")
        result = observer.paint(overlay, None, paint)
        self.assertIs(result.frame, overlay._snapshots.latest())
        self.assertEqual((result.remote_box_lines, result.remote_skeleton_lines,
                          result.remote_player_draw_count), (2, 3, 1))
        self.assertEqual(len(painter.lines), 10)
        self.assertEqual(len(painter.pens), 4)
        self.assertEqual(len(painter.texts), 1)

    def test_zero_clipped_remote_box_and_zero_skeleton_are_not_activity(self):
        remote = Player(False)
        module, overlay, observer = fixture((remote,))
        def paint(owner, event):
            player = owner._snapshots.latest().players[0]
            color = module.Overlay._player_color(owner.config, player)
            owner._draw_box(Painter(), 0, color)
            owner._draw_skeleton(Painter(), 0, color)
        result = observer.paint(overlay, None, paint)
        self.assertEqual((result.remote_box_lines, result.remote_skeleton_lines,
                          result.remote_player_draw_count), (0, 0, 0))

    def test_inactive_painter_is_still_delegated_but_not_activity(self):
        remote = Player(False)
        module, overlay, observer = fixture((remote,))
        painter = Painter(active=False)
        def paint(owner, event):
            player = owner._snapshots.latest().players[0]
            color = module.Overlay._player_color(owner.config, player)
            owner._draw_box(painter, 2, color)
        result = observer.paint(overlay, None, paint)
        self.assertEqual(len(painter.lines), 2)
        self.assertEqual(result.remote_box_lines, 0)

    def test_player_not_in_observed_frame_cannot_be_attributed_as_remote(self):
        member, other = Player(False), Player(False)
        module, overlay, observer = fixture((member,))
        def paint(owner, event):
            owner._snapshots.latest()
            color = module.Overlay._player_color(owner.config, other)
            owner._draw_box(Painter(), 2, color)
        self.assertEqual(observer.paint(overlay, None, paint).remote_player_draw_count, 0)

    def test_skeleton_inconsistent_return_is_not_activity(self):
        remote = Player(False)
        module, overlay, _ = fixture((remote,))
        def bad_skeleton(owner, painter, lines, color):
            painter.drawLine(1, 2, 3, 4)
            return 0
        module.Overlay._draw_skeleton = bad_skeleton
        observer = observer_module.CompletedPaintObserver(module)
        def paint(owner, event):
            player = owner._snapshots.latest().players[0]
            color = module.Overlay._player_color(owner.config, player)
            owner._draw_skeleton(Painter(), 1, color)
        self.assertEqual(observer.paint(overlay, None, paint).remote_skeleton_lines, 0)

    def test_failed_paint_restores_descriptors_store_and_propagates(self):
        remote = Player(False)
        module, overlay, observer = fixture((remote,))
        original_store = overlay._snapshots
        originals = {name: inspect.getattr_static(module.Overlay, name) for name in (
            "_player_color", "_draw_box", "_draw_skeleton")}
        def paint(owner, event):
            player = owner._snapshots.latest().players[0]
            color = module.Overlay._player_color(owner.config, player)
            owner._draw_box(Painter(fail_at=1), 2, color)
        with self.assertRaisesRegex(ValueError, "fake painter failure"):
            observer.paint(overlay, None, paint)
        self.assertIs(overlay._snapshots, original_store)
        for name, original in originals.items():
            self.assertIs(inspect.getattr_static(module.Overlay, name), original)
        self.assertIsNotNone(observer.paint(overlay, None, lambda owner, _: owner._snapshots.latest()))

    def test_captures_actual_original_frame_not_later_published_frame(self):
        remote = Player(False)
        module, overlay, observer = fixture((remote,))
        before, after = overlay._snapshots.latest(), Frame((), sequence=4)
        def paint(owner, event):
            frame = owner._snapshots.latest()
            self.assertEqual(owner._snapshots.publish(after), "published")
            color = module.Overlay._player_color(owner.config, frame.players[0])
            owner._draw_box(Painter(), 1, color)
        result = observer.paint(overlay, None, paint)
        self.assertIs(result.frame, before)
        self.assertIs(overlay._snapshots.latest(), after)
        self.assertEqual(result.remote_box_lines, 1)

    def test_other_thread_latest_does_not_replace_gui_frame(self):
        remote = Player(False)
        _, overlay, observer = fixture((remote,))
        frame = overlay._snapshots.latest()
        def paint(owner, event):
            owner._snapshots.latest()
            owner._snapshots.publish(Frame((), sequence=4))
            thread = threading.Thread(target=owner._snapshots.latest)
            thread.start()
            thread.join()
        self.assertIs(observer.paint(overlay, None, paint).frame, frame)

    def test_multiple_different_gui_frames_or_no_frame_return_no_observation(self):
        _, overlay, observer = fixture((Player(False),))
        def inconsistent(owner, event):
            owner._snapshots.latest()
            owner._snapshots.publish(Frame((), sequence=4))
            owner._snapshots.latest()
        self.assertIsNone(observer.paint(overlay, None, inconsistent))
        self.assertIsNone(observer.paint(overlay, None, lambda *_: None))

    def test_local_only_and_unattributed_direct_draws_are_not_activity(self):
        local = Player(True)
        module, overlay, observer = fixture((local,))
        def paint(owner, event):
            frame = owner._snapshots.latest()
            owner._draw_box(Painter(), 1, "unattributed")
            color = module.Overlay._player_color(owner.config, frame.players[0])
            owner._draw_box(Painter(), 1, color)
        result = observer.paint(overlay, None, paint)
        self.assertEqual(result.remote_box_lines, 0)

    def test_cached_repaint_exposes_same_frame_for_caller_deduplication(self):
        remote = Player(False)
        module, overlay, observer = fixture((remote,))
        def paint(owner, event):
            player = owner._snapshots.latest().players[0]
            color = module.Overlay._player_color(owner.config, player)
            owner._draw_box(Painter(), 1, color)
        first, second = observer.paint(overlay, None, paint), observer.paint(overlay, None, paint)
        self.assertIs(first.frame, second.frame)
        self.assertEqual(first.frame.sequence, second.frame.sequence)
        # Observer intentionally has no cumulative/distinct-frame counter.
        self.assertEqual(first.remote_player_draw_count, second.remote_player_draw_count)

    def test_nested_observation_is_rejected_without_leaving_wrappers(self):
        _, overlay, observer = fixture((Player(False),))
        original_store = overlay._snapshots
        def paint(owner, event):
            observer.paint(owner, event, lambda *_: None)
        with self.assertRaisesRegex(RuntimeError, "non-reentrant"):
            observer.paint(overlay, None, paint)
        self.assertIs(overlay._snapshots, original_store)


if __name__ == "__main__":
    unittest.main()
