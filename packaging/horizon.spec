# Native console application; include only public resources, never local data.
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata

root = Path(SPECPATH).parent
datas = [(str(root / 'tui' / 'horizon.tcss'), 'tui')]
for name in ('connection_presets.json', 'model_catalog_snapshot.json', 'model_catalog_overrides.json',
             'model_aliases.json', 'provider_endpoint_hosts.json'):
    datas.append((str(root / 'providers' / name), 'providers'))
datas += collect_data_files('textual')
hidden = ['api.app', 'pypdf', 'openpyxl', 'pymysql', 'bs4', 'tavily',
          'uvicorn.logging', 'uvicorn.loops.auto', 'uvicorn.protocols.http.auto',
          'uvicorn.protocols.http.h11_impl', 'uvicorn.protocols.websockets.auto', 'uvicorn.lifespan.on']
for package in ('tui', 'api', 'core', 'tools', 'providers', 'prompts'):
    hidden += collect_submodules(package)
for package in ('openai', 'textual', 'tavily-python'):
    datas += copy_metadata(package)

a = Analysis([str(root / 'scripts' / 'horizon.py')], pathex=[str(root)],
             datas=datas, binaries=[], hiddenimports=hidden, hookspath=[],
             excludes=['playwright', 'pytest', 'IPython', 'tkinter'], noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='horizon',
          debug=False, strip=False, upx=False, console=True)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='horizon')
