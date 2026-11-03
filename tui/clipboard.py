"""Write selected text without shell interpolation or blocking the TUI."""
import asyncio
import sys
import os
from pathlib import Path
from core.external_process import run_external


async def copy_text(text: str, app) -> None:
    text = text.replace('\0', '')
    if not text:
        raise ValueError('Empty clipboard text')
    command = None
    options = {}
    if sys.platform == 'darwin':
        # macOS Terminal does not implement OSC52. Write the host clipboard too.
        command = ['/usr/bin/pbcopy']
    elif sys.platform == 'win32':
        powershell = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
        script = "$ErrorActionPreference='Stop'; [Console]::InputEncoding=[Text.UTF8Encoding]::new($false); Set-Clipboard -Value ([Console]::In.ReadToEnd())"
        command = [str(powershell), '-NoLogo', '-NoProfile', '-NonInteractive', '-Command', script]
        options['creationflags'] = 0x08000000
    if command and sys.platform == 'win32' and getattr(sys, 'frozen', False):
        await asyncio.to_thread(run_external, command, input=text.encode('utf-8'),
                                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
                                timeout=5, check=True, **options)
    elif command:
        process = await asyncio.create_subprocess_exec(
            *command, stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL, **options,
        )
        try:
            await asyncio.wait_for(process.communicate(text.encode('utf-8')), timeout=5 if sys.platform == 'win32' else 2)
            if process.returncode != 0:
                raise OSError('Clipboard write failed')
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()
    # Keep Textual's clipboard and OSC52-capable terminal destinations in sync.
    app.copy_to_clipboard(text)
