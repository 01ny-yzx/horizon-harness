"""Animated welcome checks using no user data or live model calls."""
from __future__ import annotations

import os
import json
import re
import select
import struct
import subprocess
import tempfile
import time
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.smoke_tui_ui import FakeClient


class WelcomeChecks(unittest.IsolatedAsyncioTestCase):
    async def test_home_wordmark_reuses_local_welcome_light_effect(self):
        from tui.welcome import render_home_logo
        for dark in (False, True):
            first = render_home_logo(width=27, height=13, dark=dark, elapsed=.42)
            later = render_home_logo(width=27, height=13, dark=dark, elapsed=.7)
            loop = render_home_logo(width=27, height=13, dark=dark, elapsed=4.02)
            self.assertEqual(first.plain, later.plain)
            self.assertEqual(first.spans, loop.spans)
            self.assertNotEqual(first.spans, later.spans)
            self.assertNotIn('HARNESS', first.plain)
            a, b = first.split('\n'), later.split('\n')
            self.assertEqual([r.spans for r in a[:5]], [r.spans for r in b[:5]])
            dimmed = sum(span.end - span.start for row in a[-3:]
                         for span in row.spans if span.style == 'dim')
            self.assertGreater(dimmed, 0)
            self.assertLessEqual(dimmed, 9)

    async def test_home_brand_matches_welcome_and_fits_after_resize(self):
        from tui.app import HorizonApp, HOME_TITLE_ART
        app = HorizonApp(FakeClient())
        async with app.run_test(size=(120, 30)) as pilot:
            for size in ((120, 30), (112, 30), (119, 30), (80, 24), (45, 20)):
                await pilot.resize_terminal(*size)
                await pilot.pause(.1)
                logo = app.query_one('#logo')
                text = str(logo.render())
                self.assertNotIn('H A R N E S S', text)
                self.assertNotIn('Press Enter', text)
                self.assertLessEqual(max(map(len, text.splitlines())), logo.content_size.width)
                self.assertLessEqual(app.query_one('#home-logo').region.bottom,
                                     app.query_one('#composer').region.y)
                if app.has_class('large-home'):
                    self.assertLessEqual(max(map(len, HOME_TITLE_ART.splitlines())),
                                         app.query_one('#home-intro').content_size.width)

    async def test_animation_timer_stops_at_final_frame(self):
        from tui.app import HorizonApp
        from tui.welcome import DURATION, WelcomeScreen
        app = HorizonApp(FakeClient(), welcome=True)
        async with app.run_test(size=(120, 30)) as pilot:
            splash = app.screen
            self.assertIsNotNone(splash._animation_timer)
            splash._started -= DURATION + 1
            splash._advance()
            self.assertIsNone(splash._animation_timer)
            self.assertIsInstance(app.screen, WelcomeScreen)
            await pilot.press('ctrl+q')
    async def test_enter_skips_animation_without_submitting_and_new_session_does_not_replay(self):
        from tui.app import HorizonApp, PromptInput
        from tui.welcome import WelcomeScreen
        client = FakeClient()
        app = HorizonApp(client, welcome=True)
        async with app.run_test(size=(120, 30)) as pilot:
            self.assertIsInstance(app.screen, WelcomeScreen)
            splash = app.screen
            await pilot.press('enter')
            await pilot.pause(.08)
            self.assertNotIsInstance(app.screen, WelcomeScreen)
            self.assertIsNone(splash._animation_timer)
            self.assertEqual(client.sent, [])
            self.assertEqual(app.query_one(PromptInput).text, '')
            self.assertTrue(app.query_one(PromptInput).has_focus)
            await pilot.press('ctrl+n')
            self.assertNotIsInstance(app.screen, WelcomeScreen)

    async def test_final_frame_stays_until_enter_and_fits_small_windows(self):
        from tui.app import HorizonApp
        from tui.welcome import WelcomeScreen
        with patch.dict(os.environ, {'NO_ANIMATION': '1'}):
            app = HorizonApp(FakeClient(), welcome=True)
            async with app.run_test(size=(80, 24)) as pilot:
                splash = app.screen
                self.assertIsInstance(splash, WelcomeScreen)
                self.assertIsNone(splash._animation_timer)
                for size in ((120, 30), (45, 20), (32, 14)):
                    await pilot.resize_terminal(*size)
                    await pilot.pause(.05)
                    art = splash.query_one('#welcome-art')
                    self.assertGreaterEqual(art.region.x, 0)
                    self.assertLessEqual(art.region.right, size[0])
                    self.assertGreaterEqual(art.region.y, 0)
                    self.assertLessEqual(art.region.bottom, size[1])
                    self.assertIn('Press Enter', str(art.render()))


class Frames(unittest.TestCase):
    @unittest.skipUnless(os.name == 'posix', 'POSIX driver')
    def test_real_terminal_enter_opens_home_and_does_not_send_a_prompt(self):
        import fcntl
        import pty
        import termios
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'result.json'
            code = '''
import json,sys
from tui.app import HorizonApp
from scripts.smoke_tui_ui import FakeClient
client=FakeClient()
app=HorizonApp(client,welcome=True)
app.run()
with open(sys.argv[1],'w') as f:json.dump({'sent':client.sent,'draft':app._prompt_widget.text},f)
'''
            master, slave = pty.openpty()
            fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', 24, 80, 0, 0))
            env = {**os.environ, 'TERM': 'xterm-256color', 'COLORTERM': 'truecolor'}
            env.pop('NO_COLOR', None)
            proc = subprocess.Popen([sys.executable, '-c', code, str(path)], stdin=slave, stdout=slave, stderr=slave, env=env)
            os.close(slave)
            output, entered, typed, quit_sent = b'', None, False, False
            try:
                deadline = time.monotonic() + 8
                while proc.poll() is None and time.monotonic() < deadline:
                    if select.select([master], [], [], .03)[0]:
                        try:
                            output += os.read(master, 65536)
                        except OSError:
                            break
                    plain = re.sub(rb'\x1b\[[0-?]*[ -/]*[@-~]', b'', output)
                    if entered is None and b'Press Enter to start' in plain:
                        os.write(master, b'\x1b]11;rgb:ffff/ffff/ffff\x1b\\\r')
                        entered = time.monotonic()
                    if entered is not None and not typed and time.monotonic() - entered > .2:
                        os.write(master, b'hi')
                        typed = True
                    if entered is not None and not quit_sent and time.monotonic() - entered > .45:
                        os.write(master, b'\x11')
                        quit_sent = True
                proc.wait(timeout=2)
                self.assertEqual(proc.returncode, 0, output[-2000:].decode(errors='replace'))
                self.assertEqual(json.loads(path.read_text()), {'sent': [], 'draft': 'hi'})
            finally:
                if proc.poll() is None:
                    proc.kill()
                    proc.wait()
                os.close(master)

    def test_frames_change_then_settle_and_use_no_fixed_background(self):
        from tui.welcome import render_welcome, DURATION
        early = render_welcome(.5, width=116, height=28, dark=True)
        final = render_welcome(DURATION, width=116, height=28, dark=True)
        self.assertNotEqual(early.plain, final.plain)
        self.assertEqual(final.plain, render_welcome(DURATION + 20, width=116, height=28, dark=True).plain)
        self.assertIn('H A R N E S S', final.plain)
        self.assertIn('▀', final.plain)
        self.assertIn('▄', final.plain)
        self.assertEqual(len(final.plain.splitlines()), 16)
        self.assertNotEqual(final.spans, render_welcome(DURATION, width=116, height=28, dark=False).spans)
        self.assertTrue(all(' on ' not in str(span.style) for span in final.spans))


if __name__ == '__main__':
    unittest.main()
