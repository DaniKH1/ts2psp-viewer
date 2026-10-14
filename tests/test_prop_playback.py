"""Slow frames must yield to Tk, including at prop loop boundaries."""

from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from ts2psp.ui.objects import PropTab


class Value:
    def __init__(self, value):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


class PropPlaybackTests(unittest.TestCase):
    def setUp(self):
        self.tab = PropTab.__new__(PropTab)
        self.clock = 0.0
        self.pending = {}
        self.sequence = 0
        self.frames = 0
        self.tab.timer = None
        self.tab.last_frame = 0
        self.tab.rendering = False
        self.tab.redraw_requested = False
        self.tab.disposed = False
        self.tab.playing = True
        self.tab.clip = {"duration": 0.1}
        self.tab.animator = SimpleNamespace(bindings=(1,))
        self.tab.playhead = 0
        self.tab.clock_ms = 0
        self.tab.moving_slider = False
        self.tab.position = Value(0)
        self.tab.loop = Value(True)
        self.tab.show_textures = Value(True)
        self.tab.show_bones = Value(True)
        self.tab.parts = [{"vertices": [((0, 0, 0), None, None, None)]}]
        self.tab.posed_parts = lambda: self.tab.parts
        self.tab.current = {"entry": "satellite"}
        self.tab.objeto = Mock()
        self.tab.objeto_key = ("satellite", True)
        self.tab.bones = ()
        self.tab.status = Mock()
        self.tab.play_button = Mock()
        self.tab.time_label = Mock()
        self.tab.canvas = SimpleNamespace(view_size=lambda: (640, 480))
        self.tab.camera = SimpleNamespace(yaw=0, pitch=0, zoom=1, pan=(0, 0))
        self.tab.after = self.after
        self.tab.after_cancel = lambda identifier: self.pending.pop(identifier, None)

    def after(self, delay, callback):
        self.assertGreaterEqual(delay, 1)
        self.sequence += 1
        identifier = str(self.sequence)
        self.pending[identifier] = callback
        return identifier

    def slow_frame(self, *args, **kwargs):
        self.clock += 35
        self.frames += 1
        return None

    def pump(self):
        identifier = next(iter(self.pending))
        callback = self.pending.pop(identifier)
        callback()

    def patches(self):
        return patch.multiple(
            "ts2psp.ui.objects.gl",
            disponible=lambda: True,
            ahora_ms=lambda: self.clock,
            faltan_para=lambda previous, interval: max(
                0, int(interval - (self.clock - previous))
            ),
        )

    def test_schedule_queues_one_frame_and_coalesces_input(self):
        with self.patches():
            for _ in range(20):
                self.tab.schedule()
        self.assertEqual(self.frames, 0)
        self.assertEqual(len(self.pending), 1)

    def test_slow_playback_returns_between_frames_and_repeats_loops(self):
        loops, previous = 0, 0
        with (
            self.patches(),
            patch("ts2psp.ui.objects.render_prop", self.slow_frame),
            patch("ts2psp.ui.objects.set_photo"),
        ):
            self.tab.schedule()
            for expected in range(1, 31):
                self.pump()
                self.assertEqual(self.frames, expected)
                self.assertEqual(len(self.pending), 1)
                self.assertFalse(self.tab.rendering)
                if self.tab.playhead < previous:
                    loops += 1
                previous = self.tab.playhead
        self.assertGreaterEqual(loops, 8)
        self.assertTrue(self.tab.playing)

    def test_nonlooping_clip_stops_without_queuing_another_frame(self):
        self.tab.loop.set(False)
        with (
            self.patches(),
            patch("ts2psp.ui.objects.render_prop", self.slow_frame),
            patch("ts2psp.ui.objects.set_photo"),
        ):
            self.tab.schedule()
            for _ in range(4):
                self.pump()
        self.assertFalse(self.tab.playing)
        self.assertEqual(self.tab.playhead, self.tab.clip["duration"])
        self.assertFalse(self.pending)

    def test_render_failure_stops_playback_and_restores_the_render_guard(self):
        with (
            self.patches(),
            patch(
                "ts2psp.ui.objects.render_prop", side_effect=RuntimeError("test error")
            ),
        ):
            self.tab.schedule()
            self.pump()
        self.assertFalse(self.tab.playing)
        self.assertFalse(self.tab.rendering)
        self.assertFalse(self.pending)
        self.tab.status.configure.assert_called_with(text="Preview: test error")

    def test_dispose_cancels_pending_frames(self):
        with self.patches():
            self.tab.schedule()
            self.tab.dispose()
            self.tab.schedule()
        self.assertFalse(self.pending)
        self.assertFalse(self.tab.playing)
