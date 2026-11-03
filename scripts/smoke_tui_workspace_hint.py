"""Directory hints remain accurate, literal, and within terminal cell budgets."""
from __future__ import annotations

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class WorkspaceHintTests(unittest.TestCase):
    def hint(self, directory, **kwargs):
        from tui.workspace_hint import workspace_hint
        return workspace_hint(directory, **kwargs)

    def test_shortens_only_explicit_home_at_path_boundary(self):
        self.assertEqual(self.hint('/Users/demo/work/project', home='/Users/demo').plain, '~/work/project')
        self.assertEqual(self.hint('/Users/demo', home='/Users/demo').plain, '~')
        self.assertEqual(self.hint('/Users/demonstration', home='/Users/demo').plain, '/Users/demonstration')
        self.assertEqual(self.hint('/Users/demo/work').plain, '/Users/demo/work')

    def test_does_not_invent_directory_or_resolve_remote_path(self):
        for value in (None, '', '   '):
            self.assertEqual(self.hint(value).plain, '')
        self.assertEqual(self.hint('/srv/../data').plain, '/srv/../data')
        self.assertEqual(self.hint(r'C:\work\project').plain, r'C:\work\project')

    def test_literal_markup_and_terminal_control_characters(self):
        value = self.hint('/tmp/[bold]project[/bold]\n\x1b[31m\x07\u202efile')
        self.assertIn('[bold]project[/bold]', value.plain)
        self.assertNotIn('\n', value.plain)
        self.assertNotIn('\x1b', value.plain)
        self.assertNotIn('\x07', value.plain)
        self.assertNotIn('\u202e', value.plain)
        self.assertEqual(value.spans, [])

    def test_narrow_ascii_and_unicode_width_preserves_project_tail(self):
        for path in ('/a/very/long/workspace/project', '/工作空间/一些/目录/项目'):
            for width in range(0, 31):
                with self.subTest(path=path, width=width):
                    hint = self.hint(path, max_cells=width)
                    self.assertLessEqual(hint.cell_len, width)
                    if hint.cell_len and width < len(path) and width > 2:
                        self.assertIn('…', hint.plain)
        self.assertEqual(self.hint('/a/very/long/workspace/project', max_cells=15).plain, '/a/…ace/project')
        self.assertEqual(self.hint('/tmp/project', max_cells=0).plain, '')
        self.assertEqual(self.hint('/tmp/project', max_cells=1).plain, '…')


class WorkspaceFooterUI(unittest.IsolatedAsyncioTestCase):
    def lines(self, app, selector='#workspace-hint'):
        from textual.geometry import Region
        field = app.query_one(selector)
        return [line.text for line in field.render_lines(Region(0, 0, field.region.width, field.region.height))]

    async def test_owned_home_directory_is_shown_at_right_with_model_on_left(self):
        from tui.app import HorizonApp
        from scripts.smoke_tui_ui import FakeClient
        app = HorizonApp(FakeClient(), local_directory=str(Path.home() / 'fixture-project'))
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause(.05)
            self.assertTrue(self.lines(app)[-1].endswith('~/fixture-project'))
            self.assertIn('offline-test', ''.join(self.lines(app, '#composer-hint')))
            hint = app.query_one('#workspace-hint')
            left = app.query_one('#composer-hint')
            footer = app.query_one('#composer-footer')
            self.assertEqual(hint.region.right, footer.content_region.right)
            self.assertEqual(hint.region.y, left.region.y)
            self.assertLessEqual(left.region.right, hint.region.x)

    async def test_session_switches_use_metadata_and_home_restores_owned_directory(self):
        from tui.app import HorizonApp
        from scripts.smoke_tui_ui import FakeClient
        class Directories(FakeClient):
            async def sessions(self):
                return [{'id': 'ses_a', 'directory': '/srv/project-a', 'title': 'A'},
                        {'id': 'ses_b', 'directory': '/srv/project-b', 'title': 'B'}]
        app = HorizonApp(Directories(), local_directory='/srv/home')
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause(.05)
            for sid, expected in [('ses_a', '/srv/project-a'), ('ses_b', '/srv/project-b'), ('ses_a', '/srv/project-a')]:
                await app._open(sid)
                await pilot.pause(.03)
                self.assertEqual(self.lines(app)[-1].strip(), expected)
            await app.action_new_session()
            await pilot.pause(.03)
            self.assertEqual(self.lines(app)[-1].strip(), '/srv/home')

    async def test_remote_home_unknown_session_and_detail_fetch_never_guess_local_cwd(self):
        from tui.app import HorizonApp
        from scripts.smoke_tui_ui import FakeClient
        class Remote(FakeClient):
            async def sessions(self):
                return []
            async def session(self, sid):
                return {'id': sid, 'directory': '/remote/server/project'} if sid == 'ses_remote' else {'id': sid}
        app = HorizonApp(Remote(), tab_scope='https://example.invalid')
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause(.05)
            self.assertEqual(''.join(self.lines(app)).strip(), '')
            await app._open('ses_remote')
            await pilot.pause(.03)
            self.assertEqual(self.lines(app)[-1].strip(), '/remote/server/project')
            await app._open('ses_unknown')
            await pilot.pause(.03)
            self.assertEqual(''.join(self.lines(app)).strip(), '')
            await app.action_new_session()
            await pilot.pause(.03)
            self.assertEqual(''.join(self.lines(app)).strip(), '')

    async def test_unavailable_session_directory_does_not_hide_working_model(self):
        from tui.app import HorizonApp
        from scripts.smoke_tui_ui import FakeClient
        class MissingMetadata(FakeClient):
            async def sessions(self):
                return []
            async def session(self, sid):
                raise RuntimeError('directory unavailable')
        app = HorizonApp(MissingMetadata())
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause(.05)
            await app._open('ses_unavailable')
            await pilot.pause(.03)
            self.assertEqual(''.join(self.lines(app)).strip(), '')
            self.assertIn('offline-test', ''.join(self.lines(app, '#composer-hint')))

    async def test_long_directory_remains_one_line_and_keeps_project_tail_after_resize(self):
        from tui.app import HorizonApp
        from scripts.smoke_tui_ui import FakeClient
        app = HorizonApp(FakeClient(), local_directory='/very/long/workspace/directory/with/nesting/project-tail')
        async with app.run_test(size=(160, 30)) as pilot:
            for width in (160, 80, 45):
                await pilot.resize_terminal(width, 30)
                await pilot.pause(.05)
                lines = self.lines(app)
                self.assertEqual(lines[0].strip(), '')
                self.assertTrue(lines[-1].endswith('project-tail'), (width, lines))


if __name__ == '__main__':
    unittest.main()
