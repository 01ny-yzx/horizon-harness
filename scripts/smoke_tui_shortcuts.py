"""Offline shortcut persistence, conflict and effective dispatch tests."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest


class KeyMapTests(unittest.TestCase):
    def test_save_reload_and_reset(self):
        from tui.keybindings import KeyBindings
        with TemporaryDirectory() as tmp:
            path = Path(tmp)/'keybindings.json'
            keys = KeyBindings(path)
            keys.assign('recall', 'ctrl+b')
            self.assertEqual(KeyBindings(path).key('recall'), 'ctrl+b')
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            keys.reset()
            self.assertEqual(KeyBindings(path).key('recall'), 'ctrl+r')

    def test_conflicts_and_reserved_chords_do_not_change_current_map(self):
        from tui.keybindings import KeyBindings
        keys = KeyBindings()
        for key in ('ctrl+g', 'ctrl+c', 'ctrl+y', 'ctrl+up', 'f1', 'ctrl+q', 'a', 'up'):
            with self.subTest(key=key), self.assertRaises(ValueError):
                keys.assign('recall', key)
            self.assertEqual(keys.key('recall'), 'ctrl+r')

    def test_failed_save_does_not_apply_and_corrupt_load_falls_back(self):
        from tui.keybindings import KeyBindings
        from unittest.mock import patch
        with TemporaryDirectory() as tmp:
            p = Path(tmp)/'keys.json'
            p.write_text('invalid')
            keys = KeyBindings(p)
            with patch('tui.keybindings.os.replace', side_effect=OSError('readonly')):
                with self.assertRaises(OSError):
                    keys.assign('send', 'ctrl+b')
            self.assertEqual(keys.key('send'), 'enter')


class ShortcutUI(unittest.IsolatedAsyncioTestCase):
    async def test_help_capture_cancel_conflict_and_immediate_dispatch(self):
        from tui.app import HorizonApp
        from scripts.smoke_tui_queue import QueueClient
        c = QueueClient()
        app = HorizonApp(c)
        async with app.run_test(size=(100, 32)) as pilot:
            app.action_help()
            await pilot.pause(.05)
            await pilot.click('#open-shortcuts')
            await pilot.pause(.05)
            self.assertEqual(type(app.screen).__name__, 'ShortcutSettings')
            # First action is send. Conflict with fixed quit must not quit or save.
            await pilot.press('enter', 'ctrl+q')
            await pilot.pause(.05)
            self.assertEqual(type(app.screen).__name__, 'KeyCapture')
            self.assertEqual(app.shortcuts.key('send'), 'enter')
            await pilot.press('escape')
            await pilot.pause(.05)
            self.assertEqual(type(app.screen).__name__, 'ShortcutSettings')
            await pilot.press('enter', 'ctrl+b')
            await pilot.pause(.1)
            self.assertEqual(app.shortcuts.key('send'), 'ctrl+b')
            await pilot.press('escape', 'escape')
            await pilot.pause(.05)
            app._prompt_widget.load_text('Actual remapped message')
            await pilot.press('enter')
            self.assertEqual(c.sent, [])
            await pilot.press('ctrl+b')
            await pilot.pause(.1)
            self.assertEqual(c.sent, ['Actual remapped message'])

    async def test_global_remap_and_reset_and_preferences_preserved(self):
        from tui.app import HorizonApp
        from tui.i18n import Preferences
        from scripts.smoke_tui_ui import FakeClient
        with TemporaryDirectory() as tmp:
            prefs = Preferences(Path(tmp)/'preferences.json')
            prefs.save('ja'); prefs.save_access_mode('read_only')
            before = prefs.path.read_bytes()
            app = HorizonApp(FakeClient(), preferences=prefs)
            async with app.run_test(size=(100, 30)) as pilot:
                app.shortcuts.assign('toggle_access', 'ctrl+b'); app.apply_shortcuts()
                await pilot.press('tab')
                self.assertEqual(app._access_mode, 'read_only')
                app._prompt_widget.focus()
                await pilot.press('ctrl+b')
                self.assertEqual(app._access_mode, 'full_access')
                app.shortcuts.assign('new_session', 'ctrl+t'); app.apply_shortcuts()
                app.session_id = 'ses_ui'
                app._ensure_session_tab({'id': 'ses_ui'})
                app._show_chat()
                app._prompt_widget.load_text('keep')
                await pilot.press('ctrl+n')
                self.assertEqual(app._prompt_widget.text, 'keep')
                await pilot.press('ctrl+t')
                await pilot.pause(.05)
                self.assertEqual(app._prompt_widget.text, '')
                app.shortcuts.reset(); app.apply_shortcuts()
                self.assertEqual(app.shortcuts.key('toggle_access'), 'tab')
            loaded = Preferences(prefs.path)
            self.assertEqual(loaded.language, 'ja')
            self.assertEqual(loaded.access_mode, 'full_access')
            self.assertEqual(json.loads(prefs.path.read_text())['language'], json.loads(before)['language'])


    async def test_custom_queue_keys_and_send_key_do_not_keep_old_aliases(self):
        from tui.app import HorizonApp
        from scripts.smoke_tui_queue import QueueClient
        c = QueueClient()
        app = HorizonApp(c)
        async with app.run_test(size=(90, 28)) as pilot:
            for action, key in [('send', 'ctrl+b'), ('recall', 'alt+r'), ('restore', 'alt+g')]:
                app.shortcuts.assign(action, key)
            app.apply_shortcuts()
            app.session_id = 'ses_ui'
            app._show_chat(); app._set_phase('正在思考', busy=True)
            app._prompt_widget.load_text('Queued by custom shortcut')
            await pilot.press('ctrl+b'); await pilot.pause(.05)
            self.assertEqual(len(c.waiting), 1)
            await pilot.press('ctrl+r'); await pilot.pause(.05)
            self.assertEqual(len(c.waiting), 1)
            app._prompt_widget.load_text('Original draft')
            await pilot.press('alt+r'); await pilot.pause(.05)
            self.assertEqual(c.waiting, [])
            self.assertEqual(app._prompt_widget.text, 'Queued by custom shortcut')
            await pilot.press('alt+g')
            self.assertEqual(app._prompt_widget.text, 'Original draft')

    async def test_capture_save_failure_and_settings_reset_using_keyboard(self):
        from tui.app import HorizonApp
        from scripts.smoke_tui_ui import FakeClient
        from tui.shortcut_settings import ShortcutSettings
        from textual.widgets import Static
        from unittest.mock import patch
        app = HorizonApp(FakeClient())
        async with app.run_test(size=(60, 20)) as pilot:
            app.shortcuts.assign('send', 'ctrl+b'); app.apply_shortcuts()
            app.push_screen(ShortcutSettings()); await pilot.pause(.05)
            await pilot.press('enter')
            with patch.object(app.shortcuts, '_save', side_effect=OSError('read only')):
                await pilot.press('ctrl+t'); await pilot.pause(.05)
            self.assertEqual(type(app.screen).__name__, 'KeyCapture')
            self.assertEqual(app.shortcuts.key('send'), 'ctrl+b')
            self.assertIn('Could not save', str(app.screen.query_one('#key-capture-error', Static).render()))
            await pilot.press('escape'); await pilot.pause(.05)
            await pilot.press(*(['down'] * 10), 'enter'); await pilot.pause(.05)
            self.assertEqual(app.shortcuts.key('send'), 'enter')
            # Hint stays visible even in a short terminal; options can scroll.
            panel = app.screen.query_one('#shortcut-settings')
            self.assertLessEqual(panel.region.bottom, 20)
            self.assertIn('Esc Back', ''.join(strip.text for strip in app.screen._compositor.render_strips()))


if __name__ == '__main__':
    unittest.main()
