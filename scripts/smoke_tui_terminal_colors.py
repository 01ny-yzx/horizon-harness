"""Terminal color reports must adapt highlights without corrupting keyboard input."""
from __future__ import annotations

import json
import os
from pathlib import Path
import select
import struct
import subprocess
import sys
import tempfile
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from textual import events
from tui.terminal_colors import ColorReportParser, TerminalBackground, message_background


class TerminalColors(unittest.TestCase):
    def test_fragmented_reports_do_not_enter_prompt(self):
        parser = ColorReportParser()
        result = []
        for char in '你\x1b]11;rgb:ffff/f0f0/8080\x1b\\好':
            result.extend(parser.feed(char))
        self.assertEqual([e.rgb for e in result if isinstance(e, TerminalBackground)], [(255, 240, 128)])
        self.assertEqual(''.join(e.character or '' for e in result if isinstance(e, events.Key)), '你好')
        self.assertEqual(list(ColorReportParser().feed('\x1b]11;rgb:ff/ff/ff\x07'))[0].rgb, (255, 255, 255))

    def test_arrows_paste_and_escape_are_preserved(self):
        parser = ColorReportParser()
        result = list(parser.feed('hi\x1b[D\x1b[200~literal 你好\x1b[201~'))
        self.assertFalse(any(isinstance(e, TerminalBackground) for e in result))
        self.assertIn('left', [e.key for e in result if isinstance(e, events.Key)])
        self.assertEqual([e.text for e in result if isinstance(e, events.Paste)], ['literal 你好'])
        result = list(parser.feed('\x1b[200~\x1b]11;rgb:ff/ff/ff\x07\x1b[201~'))
        self.assertFalse(any(isinstance(e, TerminalBackground) for e in result))
        list(parser.feed('\x1b'))
        parser.since -= 1
        list(parser.tick())
        # The normal parser retains its own Escape timeout.
        time.sleep(.12)
        self.assertIn('escape', [e.key for e in parser.tick() if isinstance(e, events.Key)])

    def test_subtle_shades_and_malformed_reply(self):
        self.assertEqual(message_background((255, 255, 255)), '#e0e0e0')
        self.assertEqual(message_background((0, 0, 0)), '#1f1f1f')
        parser = ColorReportParser()
        self.assertEqual(list(parser.feed('\x1b]11;garbage\x07')), [])
        self.assertEqual(''.join(e.character or '' for e in parser.feed('hello')), 'hello')

    @unittest.skipUnless(os.name == 'posix', 'POSIX terminal driver')
    def test_real_driver_in_pty_light_and_dark(self):
        import fcntl
        import pty
        import termios
        for rgb in ((255, 255, 255), (24, 24, 24)):
            with self.subTest(rgb=rgb), tempfile.TemporaryDirectory() as tmp:
                result_path = Path(tmp) / 'result.json'
                source = '''
import asyncio, json, sys
from pathlib import Path
from tui.app import HorizonApp, PromptInput
from scripts.smoke_tui_ui import FakeClient
class Demo(HorizonApp):
    CSS_PATH = str(Path('tui/horizon.tcss').resolve())
    def on_mount(self):
        super().on_mount()
        self._show_chat()
        self._spawn(self._render_context([{'type':'user','text':'hello'}]))
app = Demo(FakeClient())
app.run()
with open(sys.argv[1], 'w') as file:
    json.dump({'background':app._user_message_background, 'text':app._prompt_widget.text}, file)
'''
                master, slave = pty.openpty()
                fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', 24, 80, 0, 0))
                env = {**os.environ, 'TERM': 'xterm-256color', 'COLORTERM': 'truecolor'}
                env.pop('NO_COLOR', None)
                proc = subprocess.Popen([sys.executable, '-c', source, str(result_path)],
                                        stdin=slave, stdout=slave, stderr=slave, env=env)
                os.close(slave)
                output = b''
                sent_at = None
                quit_sent = False
                deadline = time.monotonic() + 8
                try:
                    while time.monotonic() < deadline and proc.poll() is None:
                        if select.select([master], [], [], .03)[0]:
                            try:
                                output += os.read(master, 65536)
                            except OSError:
                                break
                        if sent_at is None and b'\x1b]11;?' in output:
                            reply = '\x1b]11;rgb:' + '/'.join(f'{c * 257:04x}' for c in rgb) + '\x1b\\'
                            os.write(master, (reply + '你好x').encode())
                            sent_at = time.monotonic()
                        if sent_at is not None and not quit_sent and time.monotonic() - sent_at > .4:
                            os.write(master, b'\x11')
                            quit_sent = True
                    proc.wait(timeout=2)
                    self.assertEqual(proc.returncode, 0, output[-2000:].decode(errors='replace'))
                    result = json.loads(result_path.read_text())
                    self.assertEqual(result, {'background': message_background(rgb), 'text': '你好x'})
                finally:
                    if proc.poll() is None:
                        proc.kill()
                        proc.wait()
                    os.close(master)


if __name__ == '__main__':
    unittest.main()
