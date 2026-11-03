"""Windows console palette adaptation, without requiring a native console in CI."""
import ctypes
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch


class WindowsColors(unittest.TestCase):
    def test_console_reads_background_slot_and_colorref_channels(self):
        from tui.terminal_colors import windows_background
        for attributes, color, expected in ((0x07, 0x181818, (24, 24, 24)),
                                             (0xF0, 0xFFFFFF, (255, 255, 255)),
                                             (0x27, 0x563412, (18, 52, 86))):
            def read(handle, pointer):
                self.assertEqual(handle, 123)
                info = pointer._obj
                self.assertEqual(info.cbSize, 96)
                info.wAttributes = attributes
                info.ColorTable[(attributes >> 4) & 15] = color
                return 1
            kernel = SimpleNamespace(GetStdHandle=Mock(return_value=123),
                                     GetConsoleScreenBufferInfoEx=Mock(side_effect=read))
            self.assertEqual(windows_background(kernel), expected)

    def test_unavailable_console_is_safe(self):
        from tui.terminal_colors import windows_background
        for handle in (None, ctypes.c_void_p(-1).value):
            kernel = SimpleNamespace(GetStdHandle=Mock(return_value=handle),
                                     GetConsoleScreenBufferInfoEx=Mock())
            self.assertIsNone(windows_background(kernel))
            kernel.GetConsoleScreenBufferInfoEx.assert_not_called()
        kernel = SimpleNamespace(GetStdHandle=Mock(return_value=123),
                                 GetConsoleScreenBufferInfoEx=Mock(return_value=0))
        self.assertIsNone(windows_background(kernel))

    def test_driver_captures_original_console_before_app_draws(self):
        from tui.terminal_colors import windows_color_driver
        order = []
        class Driver:
            def __init__(self, app):
                order.append('driver')
                self.app = app
        def capture():
            order.append('capture')
            return (255, 255, 255)
        with patch('tui.terminal_colors.windows_background', side_effect=capture):
            driver = windows_color_driver(Driver)('app')
        self.assertEqual(order, ['capture', 'driver'])
        self.assertEqual(driver.background_rgb, (255, 255, 255))
        self.assertEqual(driver.app, 'app')

    def test_driver_has_visible_dark_fallback_when_console_unavailable(self):
        from tui.terminal_colors import windows_color_driver
        class Driver:
            pass
        with patch('tui.terminal_colors.windows_background', return_value=None):
            driver = windows_color_driver(Driver)()
        self.assertEqual(driver.background_rgb, (0, 0, 0))


class WindowsMessageBackground(unittest.IsolatedAsyncioTestCase):
    async def test_windows_background_reaches_existing_and_new_user_messages(self):
        from tui.app import HorizonApp
        from scripts.smoke_tui_ui import FakeClient
        from tui.terminal_colors import message_background
        app = HorizonApp(FakeClient())
        async with app.run_test() as pilot:
            app._show_chat()
            await app._render_context([{'type': 'user', 'text': 'first'}])
            for rgb in ((24, 24, 24), (255, 255, 255)):
                with patch.object(app._driver, 'background_rgb', rgb, create=True):
                    app._query_terminal_background()
                await pilot.pause()
                self.assertEqual(app._user_message_background, message_background(rgb))
                self.assertEqual(app.has_class('terminal-light'), rgb == (255, 255, 255))
                field = app._prompt_widget
                field.cursor_blink = False
                expected_cursor = (0, 0, 0) if rgb == (255, 255, 255) else (255, 255, 255)
                self.assertEqual(tuple(field._theme.cursor_style.bgcolor.get_truecolor()), expected_cursor)
                await app._render_context([{'type': 'user', 'text': 'first'},
                                           {'type': 'user', 'text': 'next'}])
                for message in app.query('.user-message'):
                    self.assertEqual(message.styles.background.hex.lower(), message_background(rgb))


if __name__ == '__main__':
    unittest.main()
