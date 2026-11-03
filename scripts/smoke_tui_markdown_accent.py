"""Reply headings/strong spans use orange without recoloring prose or tabs."""
import os
import unittest
from unittest.mock import patch

from textual.geometry import Region
from scripts.smoke_tui_ui import FakeClient
from tui.app import HorizonApp
from tui.terminal_colors import TerminalBackground


def segments(widget):
    return [s for line in widget.render_lines(Region(0, 0, widget.region.width, widget.region.height)) for s in line]


class MarkdownAccent(unittest.IsolatedAsyncioTestCase):
    async def test_reply_spacing_matches_while_streaming_and_saved(self):
        app = HorizonApp(FakeClient())
        async with app.run_test(size=(100, 40)) as pilot:
            app._show_chat()
            source = '# First heading\n\nFirst paragraph.\n\nSecond paragraph.\n\n## Next section\n\n- One\n- Two\n\n```python\nprint(1)\n```'
            await app._render_context([{'type': 'assistant', 'content': [{'type': 'text', 'text': source}]}])
            app._update_live_output(source)
            await pilot.pause(.15)
            layouts = []
            for markdown in (app.query_one('#messages .answer'), app.query_one('#live-output')):
                blocks = list(markdown.children)
                self.assertEqual(blocks[0].styles.margin.top, 0)
                self.assertEqual(blocks[-1].styles.margin.bottom, 0)
                self.assertEqual(blocks[0].styles.content_align_horizontal, 'left')
                paragraphs = list(markdown.query('MarkdownParagraph'))[:2]
                self.assertEqual(paragraphs[1].region.y - paragraphs[0].region.bottom, 1)
                layouts.append([(b.region.y - markdown.region.y, b.region.height) for b in blocks])
            self.assertEqual(layouts[0], layouts[1])

    async def test_orange_headings_and_strong_in_live_and_saved_replies(self):
        with patch.dict(os.environ):
            os.environ.pop('NO_COLOR', None)
            app = HorizonApp(FakeClient())
        async with app.run_test(size=(100, 32)) as pilot:
            app._show_chat()
            source = '## Heading\n\nNormal prose **important** text.'
            await app._render_context([{'type': 'assistant', 'content': [{'type': 'text', 'text': source}]}])
            app._update_live_output(source)
            await pilot.pause(.15)
            for rgb, orange in [((255, 255, 255), '#b45309'), ((24, 24, 24), '#f2a35c'),
                                ((245, 245, 245), '#b45309')]:
                app.post_message(TerminalBackground(rgb))
                await pilot.pause()
                for root in ('#messages', '#live-output'):
                    heading = app.query_one(root + ' MarkdownH2')
                    paragraph = app.query_one(root + ' MarkdownParagraph')
                    self.assertTrue(any('Heading' in s.text and s.style.color.name == orange for s in segments(heading)))
                    self.assertTrue(any('important' in s.text and s.style.bold and s.style.color.name == orange
                                        for s in segments(paragraph)))
                    self.assertTrue(any('Normal prose' in s.text and (s.style.color is None or s.style.color.is_default)
                                        for s in segments(paragraph)))


if __name__ == '__main__':
    unittest.main()
