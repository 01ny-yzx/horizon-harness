"""Approved ASCII scan loop and compact text-tab header contracts."""
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.smoke_tui_session_tabs import TabClient
from tui.app import HorizonApp, HOME_TITLE_ART
from tui.terminal_colors import TerminalBackground


class HomeScanContracts(unittest.IsolatedAsyncioTestCase):
    async def test_animation_tick_without_a_screen_is_safe(self):
        app = HorizonApp(TabClient())
        app.add_class('home')
        app._tick_home_scan()

    async def test_live_scan_pauses_in_chat_and_respects_no_animation(self):
        with patch.dict(os.environ):
            os.environ.pop('NO_ANIMATION', None)
            app = HorizonApp(TabClient())
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause(.25)
            scan = app._home_scan
            self.assertGreater(scan.elapsed, 0)
            self.assertEqual(str(scan.render()), HOME_TITLE_ART)
            await app._open('ses_a')
            frozen = scan.elapsed
            await pilot.pause(.25)
            self.assertEqual(scan.elapsed, frozen)
        from tui.home_scan import HomeScan
        with patch.dict(os.environ, {'NO_ANIMATION': '1'}):
            scan = HomeScan(HOME_TITLE_ART)
            scan.tick(True)
            self.assertEqual(scan.elapsed, 0)
            self.assertFalse(scan.enabled)

    async def test_text_tabs_keep_terminal_background_and_existing_divider(self):
        with patch.dict(os.environ):
            os.environ.pop('NO_COLOR', None)
            app = HorizonApp(TabClient())
        async with app.run_test(size=(120, 30)) as pilot:
            await app._refresh_tabs()
            for chat in (False, True):
                if chat:
                    await app._open('ses_a')
                for rgb in ((255, 255, 255), (24, 24, 24)):
                    app.post_message(TerminalBackground(rgb))
                    await pilot.pause(.1)
                    header = app.query_one('#topbar-row')
                    tab = app.query_one('.session-tab')
                    self.assertEqual(header.region.height, 2)
                    self.assertEqual(tab.region.y, header.region.y)
                    self.assertEqual(tab.region.bottom, header.region.bottom)
                    rows = app.screen._compositor.render_strips()
                    self.assertEqual(set(rows[header.region.bottom - 1].text.strip()), {'─'})
                    for y in range(header.region.y, header.region.bottom - 1):
                        strip = rows[y].crop(tab.region.x, tab.region.right)
                        self.assertTrue(all(s.style and s.style.bgcolor
                                            and s.style.bgcolor.is_default for s in strip))
                    divider = rows[header.region.bottom - 1].crop(tab.region.x, tab.region.right)
                    self.assertTrue(all(s.style and s.style.bgcolor and s.style.bgcolor.is_default
                                        for s in divider))

    async def test_scan_moves_without_changing_ascii_or_background(self):
        from tui.home_scan import scan_frame
        for rgb in ((255, 255, 255), (24, 24, 24)):
            first = scan_frame(HOME_TITLE_ART, 0, rgb)
            middle = scan_frame(HOME_TITLE_ART, 3.6, rgb)
            loop = scan_frame(HOME_TITLE_ART, 7.2, rgb)
            self.assertEqual(first.plain, HOME_TITLE_ART)
            self.assertEqual(middle.plain, HOME_TITLE_ART)
            self.assertNotEqual(first.spans, middle.spans)
            self.assertEqual(first.spans, loop.spans)
            self.assertTrue(all(span.style.bgcolor is None for span in first.spans))


if __name__ == '__main__':
    unittest.main()
