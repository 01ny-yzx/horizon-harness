"""Offline contracts for fixed-cell activity motion and readable status labels."""
import os
import time
import unittest
from unittest.mock import patch

from rich.cells import cell_len
from rich.console import Console
from textual.geometry import Region

from scripts.smoke_tui_ui import FakeClient
from tui.app import HorizonApp
from tui.terminal_colors import TerminalBackground


class ActivityFrames(unittest.TestCase):
    def test_wave_and_shimmer_change_without_changing_labels_or_width(self):
        from tui.activity import activity_frame
        for label in ('Thinking', '正在思考', '考え中', 'コンテキストを圧縮中'):
            frames = [activity_frame(label, t, busy=True, dark=False, rgb=(255, 255, 255))
                      for t in (0, .16, .4, .72, 1.12)]
            self.assertEqual({cell_len(f.plain) for f in frames}, {12 + cell_len(label)})
            self.assertTrue(all(f.plain[6:-6] == label and '\n' not in f.plain for f in frames))
            self.assertTrue(all(set(f.plain[:5]) <= set('▁▂▃▄') for f in frames))
            self.assertTrue(all(f.plain.endswith(' · · ·') for f in frames))
            self.assertGreater(len({f.plain[:5] for f in frames}), 1)
            self.assertGreater(len({str(f.get_style_at_offset(Console(), 6)) for f in frames}), 1)

    def test_theme_changes_keep_transparent_background_and_semantic_role(self):
        from tui.activity import activity_frame
        colors = []
        for dark, rgb in ((False, (255, 255, 255)), (True, (24, 24, 24))):
            for role in ('info', 'warning'):
                frame = activity_frame('正在压缩上下文', .4, busy=True, dark=dark, rgb=rgb, role=role)
                styles = [frame.get_style_at_offset(Console(), i) for i in range(len(frame))]
                self.assertTrue(all(s.bgcolor is None and not s.conceal for s in styles))
                colors.append(styles[6].color)
        self.assertEqual(len(set(colors)), 4)

    def test_idle_and_disabled_animation_are_static(self):
        from tui.activity import activity_frame
        for label in ('执行失败', '已中断', ''):
            self.assertEqual(activity_frame(label, 0, busy=False, dark=True).plain, label)
        a = activity_frame('Thinking', 0, busy=True, dark=True, animate=False)
        b = activity_frame('Thinking', 5, busy=True, dark=True, animate=False)
        self.assertEqual(a.plain, b.plain)
        self.assertEqual(a.spans, b.spans)


class ActivityUI(unittest.IsolatedAsyncioTestCase):
    async def test_activity_uses_wave_localized_shimmer_and_stops_after_failure(self):
        with patch.dict(os.environ):
            os.environ.pop('NO_COLOR', None)
            os.environ.pop('NO_ANIMATION', None)
            app = HorizonApp(FakeClient())
        async with app.run_test(size=(60, 20)) as pilot:
            app._show_chat()
            app._set_phase('正在思考', busy=True)
            for rgb in ((255, 255, 255), (24, 24, 24)):
                app.post_message(TerminalBackground(rgb))
                await pilot.pause(.05)
                widget = app._activity_widget
                render = widget.render()
                self.assertEqual(render.plain[6:-6], 'Thinking')
                self.assertTrue(set(render.plain[:5]) <= set('▁▂▃▄'))
                self.assertTrue(render.plain.endswith(' · · ·'))
                strips = widget.render_lines(Region(0, 0, widget.region.width, widget.region.height))
                self.assertTrue(any('Thinking' in ''.join(s.text for s in strip) for strip in strips))
            await app.set_language('ja')
            self.assertEqual(app._activity_widget.render().plain[6:-6], '考え中')
            colors = []
            for elapsed in (0., 1.1):
                app._activity_started = time.monotonic() - elapsed
                app._tick_activity()
                strips = app._activity_widget.render_lines(Region(0, 0, widget.region.width, widget.region.height))
                colors.append(tuple((s.text, str(s.style)) for strip in strips for s in strip))
            self.assertNotEqual(colors[0], colors[1])
            app._set_phase('执行失败', busy=False)
            self.assertEqual(app._activity_widget.render().plain, app.tr('执行失败'))

    async def test_no_animation_setting_freezes_wave_and_shimmer(self):
        with patch.dict(os.environ, {'NO_ANIMATION': '1'}):
            app = HorizonApp(FakeClient())
        async with app.run_test(size=(60, 20)):
            app._show_chat()
            app._set_phase('正在思考', busy=True)
            a = app._activity_widget.render()
            app._activity_started -= 2
            app._tick_activity()
            b = app._activity_widget.render()
            self.assertEqual(a.plain, b.plain)
            self.assertEqual(a.spans, b.spans)


if __name__ == '__main__':
    unittest.main()
