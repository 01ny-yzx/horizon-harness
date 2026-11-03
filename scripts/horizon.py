"""Run Horizon from any directory without changing the caller's cwd."""
from pathlib import Path
import sys

if not getattr(sys, 'frozen', False):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    if getattr(sys, 'frozen', False):
        # Frozen Python does not necessarily honor PYTHONIOENCODING on Windows.
        # Pipe output (service logs and Python tools) must still be UTF-8.
        for stream in (sys.stdin, sys.stdout, sys.stderr):
            if stream is not None and hasattr(stream, 'reconfigure'):
                stream.reconfigure(encoding='utf-8', errors='replace')
    import multiprocessing
    multiprocessing.freeze_support()
    mode = sys.argv[1] if len(sys.argv) > 1 else ''
    if mode == '--_horizon-service':
        from tui.service import main as service_main
        service_main()
    elif mode == '--_horizon-python':
        import runpy
        if len(sys.argv) < 3:
            raise SystemExit('Missing Python script')
        sys.argv = sys.argv[2:]
        sys.path.insert(0, str(Path(sys.argv[0]).resolve().parent))
        runpy.run_path(sys.argv[0], run_name='__main__')
    elif mode == '--_horizon-self-test':
        from tui.bundle_check import main as check_main
        check_main()
    else:
        from tui.__main__ import main as tui_main
        tui_main()

if __name__ == '__main__':
    main()
