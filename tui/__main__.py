"""One-command terminal entrypoint with an owned local API service."""
from __future__ import annotations

import sys

if sys.version_info < (3, 11):
    raise SystemExit('Horizon requires Python 3.11 or newer. Please upgrade Python and recreate the virtual environment.')

from tui.dependencies import missing_runtime_modules

_missing = missing_runtime_modules()
if _missing:
    raise SystemExit('Horizon dependencies are incomplete: ' + ', '.join(_missing)
                     + '\nRun with this Python: python -m pip install -r requirements.txt')

import argparse
import asyncio
import os

from tui.app import HorizonApp
from tui.client import HorizonClient
from tui.runtime import LocalService
from tui.i18n import Preferences, translate
from tui.version import __version__


async def run(args):
    preferences = Preferences()
    tr = lambda source: translate(source, preferences.language)
    from tui.updates import UpdateManager
    updater = UpdateManager(preferences.path.with_name('updates.json'))
    app = None
    service = None
    try:
        if args.url:
            url, key = args.url, os.getenv("HORIZON_API_KEY", "")
        else:
            service = LocalService(language=preferences.language, directory=getattr(args, 'directory', None))
            print(tr("正在启动 Horizon…"), flush=True)
            await service.start()
            url, key = service.url, service.api_key
        async with HorizonClient(url, api_key=key, user_id=args.user, project_id=args.project,
                                 api_key_header=args.api_key_header if args.url else "X-API-Key",
                                 language=preferences.language) as client:
            app = HorizonApp(client, welcome=True, preferences=preferences,
                             tab_scope=args.url or 'local', updater=updater,
                             local_directory=service.directory if service is not None else None)
            await app.run_async()
    finally:
        if service is not None:
            await service.stop()
    return (updater, app.update_requested) if app and app.update_requested else None


def main() -> None:
    if sys.argv[1:2] == ['upgrade']:
        from tui.updates import UpdateManager, perform_update
        preferences = Preferences()
        manager = UpdateManager(preferences.path.with_name('updates.json'))
        tr = lambda source: translate(source, preferences.language)
        if manager.installation.method in {'source', 'unknown'}:
            print(tr('当前从源码运行，请通过 Git 更新源码') if manager.installation.method == 'source'
                  else tr('无法识别安装方式，请通过原安装渠道更新'))
            return
        try:
            version = asyncio.run(manager.check(manual=True))
            if not version:
                print(tr('已是最新版本') + ' · v' + __version__)
                return
            print(f'Horizon {__version__} → {version} ({manager.installation.method})')
            if input('Update and restart? [y/N] ').strip().casefold() not in {'y', 'yes'}:
                return
            raise SystemExit(perform_update(manager, version, []))
        except KeyboardInterrupt:
            return
        except (OSError, RuntimeError) as error:
            print(tr(str(error)), file=sys.stderr)
            raise SystemExit(1)
    language = Preferences().language
    tr = lambda source: translate(source, language)
    parser = argparse.ArgumentParser(description=tr('Horizon terminal interface (starts a local service automatically)'))
    parser.add_argument('--version', action='version', version='Horizon ' + __version__)
    parser.add_argument('directory', nargs='?', help='Working directory (default: current directory)')
    parser.add_argument("--url", help=tr("连接已启动的 Horizon 服务；退出时不关闭该服务"))
    parser.add_argument("--user", help=tr("用户 ID，默认使用服务配置"))
    parser.add_argument("--project", help=tr("项目 ID，默认使用服务配置"))
    parser.add_argument("--api-key-header", default="X-API-Key", help=tr("已有服务的 API Key 请求头"))
    args = parser.parse_args()
    if args.url and args.directory is not None:
        parser.error('A directory cannot be selected with --url; the remote service owns its working directory.')
    try:
        update = asyncio.run(run(args))
        if update:
            from tui.updates import perform_update
            raise SystemExit(perform_update(update[0], update[1], sys.argv[1:]))
    except KeyboardInterrupt:
        pass
    except (OSError, RuntimeError, ValueError) as exc:
        parser.exit(1, tr("Horizon 启动失败：") + str(exc) + "\n")


if __name__ == "__main__":
    main()
